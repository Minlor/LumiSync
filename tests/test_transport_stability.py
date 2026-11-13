"""Regression tests for the 1.0 device-transport stability fixes.

Everything runs against in-process fakes: no tinytuya sockets, no Bluetooth
radio and no vendor account is touched.
"""

import asyncio
import threading
import time
import unittest
import unittest.mock as mock

from lumisync.accounts.secrets import vault
from lumisync.drivers import pool, tuya_aoi, tuya_lan
from lumisync.drivers.idotmatrix_ble import IDotMatrixBleAdapter
from lumisync.drivers import idotmatrix_ble
from lumisync.drivers.tuya_lan import (
    TuyaCommandError,
    TuyaLightAdapter,
    encode_paint_clear,
    status_enrichment,
)

OLD_KEY = "0123456789abcdef"
NEW_KEY = "fedcba9876543210"


class RecordingHandle:
    """A tinytuya-like handle that records writes and answers with ``reply``."""

    def __init__(self, reply=None, status=None):
        self.reply = reply
        self.status_reply = status if status is not None else {"dps": {"20": True}}
        self.single = []
        self.multiple = []
        self.status_calls = 0
        self.closed = False

    def set_value(self, dp, value, nowait=False):
        self.single.append((dp, value))
        return self.reply

    def set_multiple_values(self, mapping):
        self.multiple.append(dict(mapping))
        return self.reply

    def status(self):
        self.status_calls += 1
        return self.status_reply

    def close(self):
        self.closed = True


OFFLINE = {"Error": "Network Error: Unable to Connect", "Err": "901"}


class OpenedHandle(RecordingHandle):
    """What the adapter gets when it (re)opens a session: never a real socket."""

    def __init__(self, dev_id, address=None, local_key=None):
        super().__init__()
        self.address = address

    def set_version(self, version):
        pass

    def set_socketPersistent(self, persist):
        pass

    def set_socketTimeout(self, seconds):
        pass

    def set_socketNODELAY(self, nodelay):
        pass

    def set_socketRetryLimit(self, limit):
        pass


class _TuyaStateMixin:
    """Isolate module-level Tuya state (key refresh map, AoI fast path)."""

    def setUp(self):
        super().setUp()
        for state in (tuya_lan._key_refresh_attempts, tuya_lan._refreshed_keys):
            patcher = mock.patch.dict(state, clear=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(tuya_aoi, "AOI_FAST_STREAM", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A reconnect inside the code under test opens a fake, never tinytuya.
        patcher = mock.patch.object(
            tuya_lan, "_require_tinytuya", return_value=mock.Mock(Device=OpenedHandle)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def adapter(self, **extra):
        device = {"transport": "tuya", "ip": "192.0.2.10", "device_id": "dev",
                  "local_key": OLD_KEY, **extra}
        adapter = TuyaLightAdapter(device)
        adapter._dev = RecordingHandle()
        return adapter


# ----------------------------------------------------------------- key refresh


class KeyRotatingDevice:
    """Stands in for tinytuya.Device; rejects every key but ``accepted``."""

    accepted = NEW_KEY
    opened: list = []

    def __init__(self, dev_id, address=None, local_key=None):
        self.address = address
        self.local_key = local_key
        self.single = []
        KeyRotatingDevice.opened.append(local_key)

    def set_version(self, version):
        pass

    def set_socketPersistent(self, persist):
        pass

    def set_socketTimeout(self, seconds):
        pass

    def set_socketNODELAY(self, nodelay):
        pass

    def set_socketRetryLimit(self, limit):
        pass

    def set_value(self, dp, value, nowait=False):
        if self.local_key != self.accepted:
            return {"Error": "Check device key or version", "Err": "914"}
        self.single.append((dp, value))
        return {"dps": {str(dp): value}}

    def close(self):
        pass


class TuyaKeyRefreshTests(_TuyaStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.reference = "tuya-local/dev-refresh"
        vault.put(self.reference, {"local_key": OLD_KEY}, remember=False)
        self.addCleanup(vault.delete, self.reference)
        saved = dict(pool._pooled_adapters)
        pool._pooled_adapters.clear()
        self.addCleanup(lambda: (pool._pooled_adapters.clear(), pool._pooled_adapters.update(saved)))
        KeyRotatingDevice.opened = []
        tinytuya = mock.Mock(Device=KeyRotatingDevice)
        patcher = mock.patch.object(tuya_lan, "_require_tinytuya", return_value=tinytuya)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The saved descriptor, as the controller loads it from settings.json.
        self.saved = {"transport": "tuya", "device_id": "dev", "ip": "192.0.2.10",
                      "local_key_ref": self.reference, "account_id": "acct"}
        self.client = mock.Mock()
        self.client.list_devices.return_value = [{"device_id": "dev", "local_key": NEW_KEY}]
        patcher = mock.patch("lumisync.accounts.manager.account_manager.client_for",
                             return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_refreshed_key_is_saved_and_does_not_recycle_the_pooled_adapter(self):
        first = pool.acquire(self.saved)
        first.set_power(True)

        self.assertEqual(KeyRotatingDevice.opened, [OLD_KEY, NEW_KEY])
        self.assertEqual(vault.get(self.reference)["local_key"], NEW_KEY)
        # The next command looks the adapter up with the unchanged descriptor:
        # same adapter, same session, no rebuild with the old key.
        self.assertIs(pool.acquire(self.saved), first)
        first.set_power(False)
        self.assertEqual(KeyRotatingDevice.opened, [OLD_KEY, NEW_KEY])
        # A later session (or a rebuilt adapter) reads the saved key.
        self.assertEqual(TuyaLightAdapter(self.saved)._local_key(), NEW_KEY)
        self.client.list_devices.assert_called_once_with()

    def test_a_refresh_returning_the_same_key_does_not_retry(self):
        self.client.list_devices.return_value = [{"device_id": "dev", "local_key": OLD_KEY}]
        adapter = pool.acquire(self.saved)
        with self.assertRaisesRegex(TuyaCommandError, "local key"):
            adapter.set_power(True)
        self.assertEqual(KeyRotatingDevice.opened, [OLD_KEY])  # no second attempt
        self.assertEqual(vault.get(self.reference)["local_key"], OLD_KEY)

    def test_account_lookups_are_rate_limited_per_device(self):
        self.client.list_devices.return_value = [{"device_id": "dev", "local_key": OLD_KEY}]
        adapter = pool.acquire(self.saved)
        for _ in range(3):
            with self.assertRaises(TuyaCommandError):
                adapter.set_power(True)
        self.client.list_devices.assert_called_once_with()

        # Past the interval the account is asked again.
        stamp = tuya_lan._key_refresh_attempts["dev"]
        tuya_lan._key_refresh_attempts["dev"] = stamp - tuya_lan.KEY_REFRESH_INTERVAL - 1
        with self.assertRaises(TuyaCommandError):
            adapter.set_power(True)
        self.assertEqual(self.client.list_devices.call_count, 2)

    def test_the_account_lookup_runs_outside_the_adapter_lock(self):
        adapter = pool.acquire(self.saved)
        lock_free = []

        def list_devices():
            def probe():
                got = adapter._lock.acquire(blocking=False)
                lock_free.append(got)
                if got:
                    adapter._lock.release()

            worker = threading.Thread(target=probe)
            worker.start()
            worker.join(2)
            return [{"device_id": "dev", "local_key": NEW_KEY}]

        self.client.list_devices.side_effect = list_devices
        adapter.set_power(True)
        self.assertEqual(lock_free, [True])

    def test_a_newer_key_already_in_the_vault_needs_no_account_lookup(self):
        adapter = pool.acquire(self.saved)
        adapter._handle()  # session opened with the old key
        # The account was refreshed meanwhile (re-import writes the vault).
        vault.put(self.reference, {"local_key": NEW_KEY}, remember=False)
        adapter.set_power(True)
        self.client.list_devices.assert_not_called()
        self.assertEqual(KeyRotatingDevice.opened, [OLD_KEY, NEW_KEY])


class TuyaErrorCodeTests(_TuyaStateMixin, unittest.TestCase):
    def test_908_reconnects_without_asking_the_account(self):
        adapter = self.adapter(account_id="acct")
        busy = RecordingHandle({"Error": "Device22 retry", "Err": "908"})
        live = RecordingHandle()
        with mock.patch.object(adapter, "_handle", side_effect=[busy, live]), \
                mock.patch("lumisync.accounts.manager.account_manager.client_for") as client_for:
            adapter.set_power(True)
        self.assertEqual(live.single, [(20, True)])
        client_for.assert_not_called()

    def test_904_is_retried_once_then_reported_without_a_key_refresh(self):
        adapter = self.adapter(account_id="acct")
        garbled = [RecordingHandle({"Error": "Unexpected payload", "Err": "904"}) for _ in range(2)]
        with mock.patch.object(adapter, "_handle", side_effect=garbled), \
                mock.patch("lumisync.accounts.manager.account_manager.client_for") as client_for:
            with self.assertRaisesRegex(TuyaCommandError, "local key") as raised:
                adapter.set_power(True)
        self.assertFalse(raised.exception.key_error)
        self.assertEqual([len(handle.single) for handle in garbled], [1, 1])
        client_for.assert_not_called()

    def test_only_914_asks_for_a_key_refresh(self):
        for code, key_error in (("901", False), ("904", False), ("905", False),
                                ("908", False), ("914", True)):
            with self.subTest(code=code):
                with self.assertRaises(TuyaCommandError) as raised:
                    TuyaLightAdapter._check_result({"Error": "x", "Err": code})
                self.assertEqual(raised.exception.key_error, key_error)


# ----------------------------------------------------------------- offline paint


class OfflinePaintStreamTests(_TuyaStateMixin, unittest.TestCase):
    def test_offline_frame_fails_fast_and_backs_off(self):
        adapter = self.adapter(tuya_paint_dp=61)
        offline = RecordingHandle(OFFLINE, status=OFFLINE)
        adapter._dev = offline
        frame = [(255, 0, 0)] * 5 + [(0, 0, 255)] * 5

        with self.assertRaises(TuyaCommandError) as raised:
            adapter.set_segments(frame)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(len(offline.single), 1)  # stopped at the first stroke
        self.assertIsNone(adapter._last_frame)  # nothing counts as painted
        self.assertEqual(adapter._unreachable_backoff, tuya_lan.UNREACHABLE_BACKOFF_START)

        # While backing off, frames are skipped without touching the network.
        started = time.monotonic()
        with self.assertRaises(TuyaCommandError):
            adapter.set_segments(frame)
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertEqual(len(offline.single), 1)

        # Each further failure doubles the wait, up to the cap.
        for expected in (2.0, 4.0, 8.0, 16.0, 30.0, 30.0):
            adapter._unreachable_until = 0.0
            with self.assertRaises(TuyaCommandError):
                adapter.set_segments(frame)
            self.assertEqual(adapter._unreachable_backoff, expected)

        # The first delivered frame resets the backoff.
        adapter._dev = RecordingHandle()
        adapter._unreachable_until = 0.0
        adapter.set_segments(frame)
        self.assertEqual(adapter._unreachable_backoff, 0.0)
        self.assertIsNotNone(adapter._last_frame)

    def test_offline_device_is_probed_again_once_it_answers(self):
        adapter = self.adapter()  # no paint DP known yet
        adapter._dev = RecordingHandle(OFFLINE, status=OFFLINE)
        with self.assertRaises(TuyaCommandError):
            adapter.set_segments([(255, 0, 0)])
        self.assertFalse(adapter._paint_probed)
        self.assertEqual(adapter._dev.single, [])  # no candidate writes while offline

    def test_begin_stream_does_not_retry_an_unreachable_device(self):
        adapter = self.adapter(tuya_paint_dp=61)
        offline = RecordingHandle(OFFLINE, status=OFFLINE)
        adapter._dev = offline
        with mock.patch.object(adapter, "close") as close:
            adapter.begin_stream()  # best effort: must not raise
        close.assert_not_called()  # no second setup() pass
        self.assertEqual(offline.status_calls, 1)
        self.assertEqual(offline.single, [])
        self.assertGreater(adapter._unreachable_until, time.monotonic())


# ----------------------------------------------------------------- status reads


class TuyaStatusRetryTests(_TuyaStateMixin, unittest.TestCase):
    def test_stale_session_is_rebuilt_once_with_a_single_read(self):
        adapter = self.adapter()
        stale = RecordingHandle(status={"Error": "Timeout Waiting for Device", "Err": "902"})
        adapter._dev = stale
        closes = []
        adapter.close = lambda: closes.append(True)
        self.assertIsNone(adapter.query_status())
        self.assertEqual(closes, [True])
        self.assertEqual(stale.status_calls, 3)  # two snapshot tries + one after rebuild

    def test_no_rebuild_while_sync_streams(self):
        adapter = self.adapter()
        adapter._dev = RecordingHandle(status={"Error": "Timeout", "Err": "902"})
        adapter._streaming = True
        closes = []
        adapter.close = lambda: closes.append(True)
        self.assertIsNone(adapter.query_status())
        self.assertEqual(closes, [])

    def test_no_rebuild_when_no_session_could_be_opened(self):
        adapter = self.adapter()
        offline = RecordingHandle(status=OFFLINE)
        adapter._dev = offline
        closes = []
        adapter.close = lambda: closes.append(True)
        self.assertIsNone(adapter.query_status())
        self.assertEqual(closes, [])
        self.assertEqual(offline.status_calls, 1)  # a connect failure is not retried


# ----------------------------------------------------------------- late frames


class LateFrameTests(_TuyaStateMixin, unittest.TestCase):
    def test_frame_waiting_for_the_lock_when_sync_stops_is_dropped(self):
        adapter = self.adapter(tuya_paint_dp=61)
        handle = adapter._dev
        adapter.begin_stream()
        adapter._lock.acquire()
        try:
            worker = threading.Thread(
                target=adapter.set_segments, args=([(0, 255, 0)] * 10,), daemon=True
            )
            worker.start()
            time.sleep(0.2)  # the frame is now blocked on the lock
            adapter.end_stream()
            writes_after_stop = len(handle.single)
        finally:
            adapter._lock.release()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(handle.single), writes_after_stop)
        clear = encode_paint_clear(list(range(10)), zones=10)
        self.assertIn((61, clear), handle.single)

    def test_led_mapping_frames_still_paint_after_a_sync_ended(self):
        # The LED-mapping dialog sends test frames without begin_stream.
        adapter = self.adapter(tuya_paint_dp=61)
        adapter.begin_stream()
        adapter.end_stream()
        before = len(adapter._dev.single)
        adapter.set_segments([(0, 255, 0)] * 10)
        self.assertGreater(len(adapter._dev.single), before)


# ----------------------------------------------------------------- work mode


class TuyaBrightnessModeTests(_TuyaStateMixin, unittest.TestCase):
    def test_brightness_after_a_colour_pick_rides_the_colour_value(self):
        adapter = self.adapter()
        adapter.set_color(255, 0, 0)
        adapter.set_brightness(40)
        self.assertEqual(adapter._dev.multiple[-1], {21: "colour", 24: "000003e80190"})
        self.assertNotIn(22, [dp for dp, _value in adapter._dev.single])

    def test_brightness_back_up_from_zero_keeps_the_colour(self):
        adapter = self.adapter()
        adapter.set_color(255, 0, 0)
        adapter.set_brightness(0)
        adapter.set_brightness(40)
        # Hue 0 (red) at full saturation, not white: same payload as 40% red.
        self.assertEqual(adapter._dev.multiple[-1], {21: "colour", 24: "000003e80190"})

    def test_brightness_in_white_mode_writes_the_white_brightness_dp(self):
        adapter = self.adapter()
        adapter.set_color(255, 0, 0)
        adapter.set_color_temperature(4000)
        adapter.set_brightness(50)
        self.assertEqual(adapter._dev.single, [(22, 505)])

    def test_brightness_follows_the_mode_the_device_reports(self):
        adapter = self.adapter()
        adapter._dev.status_reply = {"dps": {"20": True, "21": "white", "22": 1000, "23": 500}}
        adapter._last_colour = (255, 0, 0)
        adapter.query_status()
        adapter.set_brightness(50)
        self.assertEqual(adapter._dev.single, [(22, 505)])

        adapter._dev.status_reply = {"dps": {"20": True, "21": "colour", "24": "000003e803e8"}}
        adapter.query_status()
        adapter.set_brightness(50)
        self.assertEqual(adapter._dev.multiple[-1], {21: "colour", 24: "000003e801f6"})


# ----------------------------------------------------------------- paint detection


class PaintDetectionTests(_TuyaStateMixin, unittest.TestCase):
    def test_a_candidate_dp_id_without_a_paint_value_is_not_paint(self):
        self.assertNotIn("tuya_paint_dp", status_enrichment({"20": True, "21": "white", "103": 5}))
        self.assertNotIn("tuya_paint_dp", status_enrichment({"20": True, "61": "AAEAAAAAAABk"}))

    def test_probe_needs_a_paint_shaped_echo(self):
        class ScenesOn61(RecordingHandle):
            """A bulb that owns DP 61 for a scene number and echoes it."""

            def set_value(self, dp, value, nowait=False):
                super().set_value(dp, value, nowait)
                return None

        adapter = self.adapter()
        adapter._dev = ScenesOn61(status={"dps": {"20": True, "61": 3, "103": 7}})
        with mock.patch.object(tuya_lan.time, "sleep"):
            adapter.set_segments([(255, 0, 0), (0, 0, 255)])
        self.assertIsNone(adapter._paint_dp)
        self.assertNotIn("tuya_paint_dp", adapter.device)


# ----------------------------------------------------------------- multi-DP latch


class MultiDpLatchTests(_TuyaStateMixin, unittest.TestCase):
    def test_colour_writes_undo_tinytuyas_one_dp_at_a_time_latch(self):
        class Latched(RecordingHandle):
            max_simultaneous_dps = 1  # set by tinytuya after one failed write

            def set_multiple_values(self, mapping):
                self.seen_limit = self.max_simultaneous_dps
                return super().set_multiple_values(mapping)

        adapter = self.adapter()
        adapter._dev = Latched()
        adapter.set_color(0, 0, 255)
        self.assertEqual(adapter._dev.seen_limit, 0)
        self.assertEqual(adapter._dev.multiple, [{21: "colour", 24: "00f003e803e8"}])


# ----------------------------------------------------------------- pool


class PoolStabilityTests(_TuyaStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        saved = dict(pool._pooled_adapters)
        pool._pooled_adapters.clear()
        self.addCleanup(lambda: (pool._pooled_adapters.clear(), pool._pooled_adapters.update(saved)))

    def test_replaced_adapter_is_closed_outside_the_pool_lock(self):
        lock_free = []

        class Fake:
            def __init__(self, device, *a, **k):
                self.device = dict(device)

            def close(self):
                def probe():
                    got = pool._pool_lock.acquire(timeout=1)
                    lock_free.append(got)
                    if got:
                        pool._pool_lock.release()

                worker = threading.Thread(target=probe)
                worker.start()
                worker.join(2)

        with mock.patch("lumisync.drivers.pool.create_adapter",
                        side_effect=lambda d, server=None: Fake(d)):
            device = {"transport": "tuya", "device_id": "dev1", "ip": "192.0.2.1"}
            pool.acquire(device)
            pool.acquire(dict(device, ip="192.0.2.2"))
        self.assertEqual(lock_free, [True])

    def test_replaced_tuya_adapter_is_retired_and_stops_reconnecting(self):
        device = {"transport": "tuya", "device_id": "dev2", "ip": "192.0.2.1",
                  "local_key": OLD_KEY, "tuya_paint_dp": 61}
        old = pool.acquire(device)
        replacement = pool.acquire(dict(device, ip="192.0.2.2"))
        self.assertIsNot(old, replacement)
        self.assertTrue(getattr(old, "_retired"))
        with mock.patch.object(tuya_lan, "_require_tinytuya") as tinytuya:
            with self.assertRaisesRegex(TuyaCommandError, "replaced") as raised:
                old.set_segments([(1, 2, 3)])
            self.assertTrue(raised.exception.retryable)
            old.end_stream()  # no network work on a retired adapter
        tinytuya.assert_not_called()

    def test_followed_dhcp_address_does_not_recycle_the_session(self):
        device = {"transport": "tuya", "device_id": "dev3", "ip": "192.0.2.1",
                  "local_key_ref": "tuya-local/dev3"}
        first = pool.acquire(device)
        first.device["ip"] = "192.0.2.77"  # _address() followed a presence beat
        self.assertIs(pool.acquire(device), first)
        # The controller later saves the new address: still the same session.
        self.assertIs(pool.acquire(dict(device, ip="192.0.2.77")), first)

    def test_identity_never_carries_a_plaintext_key(self):
        identity = pool._tuya_connection_key({"device_id": "d", "local_key": OLD_KEY})
        self.assertNotIn(OLD_KEY, repr(identity))


# ----------------------------------------------------------------- BLE close


class FakeBleClient:
    def __init__(self, address=None, connected=True):
        self.address = address
        self.is_connected = connected
        self.disconnects = 0

    async def connect(self):
        self.is_connected = True

    async def start_notify(self, *_args):
        pass

    async def disconnect(self):
        self.disconnects += 1
        self.is_connected = False


class BleCloseTests(unittest.TestCase):
    def test_close_does_not_wait_out_a_stuck_write(self):
        adapter = IDotMatrixBleAdapter({"transport": "ble", "ble_address": "AA:BB"})
        client = FakeBleClient()
        adapter._client = client
        holding, release = threading.Event(), threading.Event()

        def stuck_write():
            with adapter._write_lock:
                holding.set()
                release.wait(5)

        worker = threading.Thread(target=stuck_write, daemon=True)
        worker.start()
        holding.wait(2)
        try:
            started = time.monotonic()
            with mock.patch.object(idotmatrix_ble, "CLOSE_LOCK_TIMEOUT", 0.05):
                adapter.close()
            self.assertLess(time.monotonic() - started, 2.0)
        finally:
            release.set()
            worker.join(2)
        self.assertEqual(client.disconnects, 1)
        self.assertIsNone(adapter._client)

    def test_a_stale_client_is_disconnected_before_reconnecting(self):
        adapter = IDotMatrixBleAdapter({"transport": "ble", "ble_address": "AA:BB"})
        stale = FakeBleClient(connected=False)
        adapter._client = stale
        bleak = mock.Mock(BleakClient=FakeBleClient)
        with mock.patch.object(idotmatrix_ble, "_require_bleak", return_value=bleak):
            fresh = adapter._ensure_client()
        self.assertEqual(stale.disconnects, 1)
        self.assertIsNot(fresh, stale)
        self.assertTrue(fresh.is_connected)

    def test_a_connect_finishing_after_close_is_not_kept(self):
        adapter = IDotMatrixBleAdapter({"transport": "ble", "ble_address": "AA:BB"})
        made = []

        class SlowClient(FakeBleClient):
            def __init__(self, address=None):
                super().__init__(address, connected=False)
                made.append(self)

            async def connect(self):
                adapter._generation += 1  # close() ran while connecting
                await asyncio.sleep(0)
                self.is_connected = True

        bleak = mock.Mock(BleakClient=SlowClient)
        with mock.patch.object(idotmatrix_ble, "_require_bleak", return_value=bleak):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                adapter._ensure_client()
        self.assertIsNone(adapter._client)
        self.assertEqual(made[0].disconnects, 1)


if __name__ == "__main__":
    unittest.main()
