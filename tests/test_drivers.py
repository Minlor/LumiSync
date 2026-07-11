import base64
import json
import unittest

import numpy as np

from lumisync.utils.colors import kelvin_to_rgb
from lumisync.drivers.govee_lan import GoveeLanAdapter
from lumisync.drivers.idotmatrix_ble import (
    IDotMatrixBleAdapter,
    average_color,
    build_brightness_frame,
    build_color_frame,
    build_diy_color_group,
    build_diy_frames,
    build_diy_mode_frame,
    build_power_frame,
    chunk,
    frame,
    looks_like_idotmatrix,
    pack_pixels,
    screen_to_pixels,
)
from lumisync.drivers.registry import create_adapter
from lumisync.drivers.tuya_lan import (
    TuyaLightAdapter,
    build_color_command,
    encode_brightness,
    encode_colour,
    encode_temperature,
    rgb_to_hsv_tuya,
)


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        pass


class GoveeLanAdapterTests(unittest.TestCase):
    def _adapter(self, device=None):
        device = device or {"ip": "192.168.0.10", "model": "H619C"}
        return GoveeLanAdapter(device, FakeSocket())

    def test_capabilities_come_from_catalog(self):
        cap = self._adapter().capabilities
        self.assertEqual(cap.transport, "lan")
        self.assertEqual(cap.segment_count, 10)     # H619C from bundled catalog
        self.assertTrue(cap.supports_white)         # 2000-9000 K
        self.assertEqual(cap.color_temp_max, 9000)

    def test_set_segments_sends_valid_razer_payload(self):
        adapter = self._adapter()
        adapter.set_segments([(255, 0, 0), (0, 255, 0)])
        self.assertEqual(len(adapter.server.sent), 1)
        data, addr = adapter.server.sent[0]
        self.assertEqual(addr[0], "192.168.0.10")
        # Payload is a JSON razer command carrying a base64 segment blob.
        self.assertIn(b'"razer"', data)

    def test_set_segments_resizes_to_device_segment_count(self):
        adapter = self._adapter()
        adapter.set_segments([(1, 2, 3)])  # 1 color -> fitted to 10
        # Decode the razer payload and confirm it declares 10 segments.
        import json
        payload = json.loads(adapter.server.sent[0][0])
        blob = base64.b64decode(payload["msg"]["data"]["pt"])
        self.assertEqual(blob[5], 10)  # header byte 5 = segment count

    def test_power_and_color_emit_commands(self):
        adapter = self._adapter()
        adapter.set_power(True)
        adapter.set_color(10, 20, 30)
        self.assertEqual(len(adapter.server.sent), 2)

    def test_begin_stream_enables_razer_mode(self):
        adapter = self._adapter()
        adapter.begin_stream()
        self.assertEqual(len(adapter.server.sent), 1)
        self.assertIn(b'"razer"', adapter.server.sent[0][0])

    def test_set_color_temperature_sends_kelvin(self):
        adapter = self._adapter()
        adapter.set_color_temperature(3000)
        payload = json.loads(adapter.server.sent[0][0])
        self.assertEqual(payload["msg"]["cmd"], "colorwc")
        self.assertEqual(payload["msg"]["data"]["colorTemInKelvin"], 3000)


class KelvinToRgbTests(unittest.TestCase):
    def test_warm_is_reddish(self):
        r, g, b = kelvin_to_rgb(2200)
        self.assertGreater(r, b)

    def test_cool_is_bluer_than_warm(self):
        _, _, warm_b = kelvin_to_rgb(2700)
        _, _, cool_b = kelvin_to_rgb(8000)
        self.assertGreater(cool_b, warm_b)

    def test_output_is_in_byte_range(self):
        for k in (1500, 2700, 4000, 6500, 9000, 12000):
            for ch in kelvin_to_rgb(k):
                self.assertTrue(0 <= ch <= 255)

    def test_owns_socket_closes_it(self):
        closed = {"v": False}

        class OwnSock(FakeSocket):
            def close(self):
                closed["v"] = True

        adapter = GoveeLanAdapter({"ip": "1.2.3.4"}, OwnSock(), owns_socket=True)
        adapter.close()
        self.assertTrue(closed["v"])
        self.assertIsNone(adapter.server)


class IDotMatrixAdapterTests(unittest.TestCase):
    def test_default_matrix_capabilities(self):
        cap = IDotMatrixBleAdapter({"type": "idotmatrix"}).capabilities
        self.assertEqual(cap.transport, "ble")
        self.assertEqual(cap.matrix_size, (32, 32))
        self.assertEqual(cap.segment_count, 32 * 32)
        self.assertTrue(cap.is_matrix)

    def test_named_matrix_size(self):
        cap = IDotMatrixBleAdapter({"matrix_size": "16x16"}).capabilities
        self.assertEqual(cap.segment_count, 256)

    def test_screen_to_pixels_downscales_to_grid(self):
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        frame[:, :32] = (255, 0, 0)   # left half red
        pixels = screen_to_pixels(frame, (4, 4))
        self.assertEqual(len(pixels), 16)
        self.assertEqual(pixels[0], (255, 0, 0))   # top-left cell
        self.assertEqual(pixels[3], (0, 0, 0))     # top-right cell

    def test_brightness_frame_has_length_prefix(self):
        f = build_brightness_frame(50)
        self.assertEqual(f[0], len(f))     # low byte of total length
        self.assertIn(50, f)

    def test_ble_writes_raise_clear_error_without_backend_or_address(self):
        # Without bleak installed (or without an address) this must fail loudly,
        # not silently do nothing.
        with self.assertRaises(RuntimeError):
            IDotMatrixBleAdapter({}).set_brightness(50)


class IDotMatrixEncoderTests(unittest.TestCase):
    def test_frame_prefixes_total_length_little_endian(self):
        self.assertEqual(frame(b"\x01\x02\x03"), bytes([5, 0, 1, 2, 3]))

    def test_pack_pixels_is_flat_rgb(self):
        self.assertEqual(pack_pixels([(1, 2, 3), (4, 5, 6)]), bytes([1, 2, 3, 4, 5, 6]))

    def test_brightness_frame_matches_app_bytes(self):
        # From the app: setLight -> [5, 0, 4, 0x80, value]
        self.assertEqual(build_brightness_frame(50), bytes([5, 0, 4, 0x80, 50]))

    def test_color_frame_matches_app_bytes(self):
        # From the app: sendColor -> [7, 0, 2, 2, r, g, b]
        self.assertEqual(build_color_frame(10, 20, 30), bytes([7, 0, 2, 2, 10, 20, 30]))

    def test_power_frame_matches_app_bytes(self):
        # From the app: sendSwitchplate -> [5, 0, 7, 1, on]
        self.assertEqual(build_power_frame(True), bytes([5, 0, 7, 1, 1]))
        self.assertEqual(build_power_frame(False), bytes([5, 0, 7, 1, 0]))

    def test_average_color(self):
        self.assertEqual(average_color([(0, 0, 0), (100, 200, 60)]), (50, 100, 30))
        self.assertEqual(average_color([]), (0, 0, 0))

    def test_diy_mode_frame_matches_app(self):
        # enterDiy(clear) -> [5,0,4,1,1]; quit-keep -> [5,0,4,1,2]
        self.assertEqual(build_diy_mode_frame(1), bytes([5, 0, 4, 1, 1]))
        self.assertEqual(build_diy_mode_frame(2), bytes([5, 0, 4, 1, 2]))

    def test_diy_color_group_layout(self):
        # [len_lo,len_hi, 5,1, move, r,g,b, x0,y0, x1,y1]
        packet = build_diy_color_group(10, 20, 30, [(1, 2), (3, 4)])
        self.assertEqual(packet, bytes([12, 0, 5, 1, 0, 10, 20, 30, 1, 2, 3, 4]))
        self.assertEqual(packet[0], len(packet))  # length prefix

    def test_diy_frames_group_by_color_and_skip_black(self):
        grid = [
            [(255, 0, 0), (0, 0, 0)],
            [(0, 0, 0), (255, 0, 0)],
        ]
        packets = build_diy_frames(grid)
        self.assertEqual(len(packets), 1)  # one red group, black skipped
        # red group carries both red pixel coords (0,0) and (1,1)
        self.assertEqual(packets[0], bytes([12, 0, 5, 1, 0, 255, 0, 0, 0, 0, 1, 1]))

    def test_diy_frames_can_include_black(self):
        grid = [[(0, 0, 0)]]
        self.assertEqual(build_diy_frames(grid, skip_black=False)[0],
                         bytes([10, 0, 5, 1, 0, 0, 0, 0, 0, 0]))

    def test_chunk_splits_to_mtu(self):
        data = bytes(range(10))
        self.assertEqual(chunk(data, 4), [bytes(range(0, 4)), bytes(range(4, 8)), bytes(range(8, 10))])

    def test_name_heuristic_matches_advertised_names(self):
        for name in ("IDM-1234", "idm-ABCD", "iDotMatrix 32x32", "My iDot panel"):
            self.assertTrue(looks_like_idotmatrix(name))
        for name in ("", None, "Galaxy Buds", "Living Room TV"):
            self.assertFalse(looks_like_idotmatrix(name))


class PoolTests(unittest.TestCase):
    def setUp(self):
        from lumisync.drivers import pool
        self.pool = pool
        self._orig = dict(pool._ble_adapters)
        pool._ble_adapters.clear()

    def tearDown(self):
        self.pool._ble_adapters.clear()
        self.pool._ble_adapters.update(self._orig)

    def test_ble_device_reuses_one_adapter(self):
        from unittest.mock import patch

        class Fake:
            def __init__(self, device, *a, **k):
                self.device = device

            def close(self):
                pass

        with patch("lumisync.drivers.pool.create_adapter", side_effect=lambda d, server=None: Fake(d)):
            device = {"transport": "ble", "ble_address": "AA:BB"}
            self.assertIs(self.pool.acquire(device), self.pool.acquire(device))

    def test_lan_device_not_pooled(self):
        from unittest.mock import patch

        class Fake:
            def __init__(self, device, *a, **k):
                pass

        with patch("lumisync.drivers.pool.create_adapter", side_effect=lambda d, server=None: Fake(d)):
            lan = {"ip": "1.2.3.4"}
            self.assertIsNot(self.pool.acquire(lan), self.pool.acquire(lan))

    def test_close_drops_pooled_adapter(self):
        from unittest.mock import patch

        closed = {"v": False}

        class Fake:
            def __init__(self, device, *a, **k):
                pass

            def close(self):
                closed["v"] = True

        with patch("lumisync.drivers.pool.create_adapter", side_effect=lambda d, server=None: Fake(d)):
            device = {"transport": "ble", "ble_address": "CC:DD"}
            self.pool.acquire(device)
            self.pool.close(device)
            self.assertTrue(closed["v"])
            self.assertNotIn("CC:DD", self.pool._ble_adapters)


class RegistryTests(unittest.TestCase):
    def test_defaults_to_govee_lan(self):
        adapter = create_adapter({"ip": "1.2.3.4"}, FakeSocket())
        self.assertIsInstance(adapter, GoveeLanAdapter)

    def test_ble_transport_selects_idotmatrix(self):
        adapter = create_adapter({"transport": "ble"})
        self.assertIsInstance(adapter, IDotMatrixBleAdapter)

    def test_tuya_transport_selects_tuya(self):
        adapter = create_adapter(
            {"transport": "tuya", "ip": "1.2.3.4", "device_id": "x", "local_key": "y"}
        )
        self.assertIsInstance(adapter, TuyaLightAdapter)


class FakeTuyaHandle:
    """Records DP writes so the adapter can be tested without tinytuya."""

    def __init__(self):
        self.single = []
        self.multiple = []

    def set_value(self, dp, value):
        self.single.append((dp, value))

    def set_multiple_values(self, mapping):
        self.multiple.append(dict(mapping))


class TuyaEncoderTests(unittest.TestCase):
    def test_hsv_units_are_h360_s1000_v1000(self):
        self.assertEqual(rgb_to_hsv_tuya(255, 0, 0), (0, 1000, 1000))
        self.assertEqual(rgb_to_hsv_tuya(0, 255, 0), (120, 1000, 1000))
        self.assertEqual(rgb_to_hsv_tuya(0, 0, 255), (240, 1000, 1000))
        self.assertEqual(rgb_to_hsv_tuya(255, 255, 255), (0, 0, 1000))

    def test_colour_data_v2_golden(self):
        self.assertEqual(encode_colour(255, 0, 0), "000003e803e8")
        self.assertEqual(encode_colour(0, 255, 0), "007803e803e8")
        self.assertEqual(encode_colour(0, 0, 255), "00f003e803e8")
        self.assertEqual(encode_colour(255, 255, 255), "0000000003e8")

    def test_colour_data_v1_golden(self):
        self.assertEqual(encode_colour(255, 0, 0, schema="v1"), "0000ffff")
        self.assertEqual(encode_colour(0, 255, 0, schema="v1"), "0078ffff")
        self.assertEqual(encode_colour(255, 255, 255, schema="v1"), "000000ff")

    def test_brightness_scaling(self):
        self.assertEqual([encode_brightness(p) for p in (0, 50, 100)], [10, 505, 1000])
        self.assertEqual(
            [encode_brightness(p, schema="v1") for p in (0, 50, 100)], [25, 140, 255]
        )

    def test_temperature_scaling(self):
        self.assertEqual(encode_temperature(2700, 2700, 6500), 0)
        self.assertEqual(encode_temperature(6500, 2700, 6500), 1000)
        self.assertEqual(encode_temperature(6500, 2700, 6500, schema="v1"), 255)
        self.assertEqual(encode_temperature(4000, 2700, 2700), 0)  # degenerate range

    def test_build_color_command_switches_to_colour_mode(self):
        self.assertEqual(
            build_color_command({}, 255, 0, 0), {21: "colour", 24: "000003e803e8"}
        )
        self.assertEqual(
            build_color_command({"dp_schema": "v1"}, 0, 255, 0),
            {2: "colour", 5: "0078ffff"},
        )


class TuyaAdapterTests(unittest.TestCase):
    def _adapter(self, **extra):
        device = {"ip": "1.2.3.4", "device_id": "d", "local_key": "k", **extra}
        adapter = TuyaLightAdapter(device)
        adapter._dev = FakeTuyaHandle()
        return adapter

    def test_power_sets_switch_dp(self):
        a = self._adapter()
        a.set_power(True)
        self.assertEqual(a._dev.single, [(20, True)])

    def test_color_sends_mode_and_colour_dps(self):
        a = self._adapter()
        a.set_color(0, 0, 255)
        self.assertEqual(a._dev.multiple, [{21: "colour", 24: "00f003e803e8"}])

    def test_segments_average_to_one_ambient_color(self):
        a = self._adapter()
        a.set_segments([(255, 0, 0), (0, 0, 255)])  # avg -> (127, 0, 127)
        self.assertEqual(len(a._dev.multiple), 1)
        self.assertEqual(a._dev.multiple[0][21], "colour")

    def test_missing_credentials_raise(self):
        adapter = TuyaLightAdapter({"ip": "1.2.3.4"})  # no id/key
        with self.assertRaises(RuntimeError):
            adapter._handle()


if __name__ == "__main__":
    unittest.main()
