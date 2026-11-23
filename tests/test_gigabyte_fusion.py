"""Gigabyte RGB Fusion 2 (USB HID) driver, against a fake HID backend only.

No test here talks to a real controller: every adapter and discovery test
replaces ``gigabyte_fusion._hid`` with a backend that records each feature
report, so the exact bytes and their order can be asserted.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lumisync import devices
from lumisync.device import Device, as_device
from lumisync.drivers import gigabyte_fusion as fusion
from lumisync.drivers import hid_feature, pool, registry
from lumisync.drivers.hid_feature import HidDeviceInfo

# FusionTestCase replaces motherboard_product; tests of the real one use this.
ORIGINAL_MOTHERBOARD_PRODUCT = fusion.motherboard_product

# Identify reply captured from a B550 AORUS ELITE (IT5702, firmware 2.0.10.0).
GOLDEN_REPLY = bytes.fromhex(
    "cc 01 00 07 02 00 0a 00 00 00 00 00 49 54 35 37 30 32 2d 47 49 47 41 42 59 54 45 20"
    " 56 32 2e 30 2e 31 30 2e 30 00 00 00 00 01 02 00 02 00 01 00 02 00 01 00 00 01 02 00"
    " 00 01 02 57 00 00 00 00"
)

LIGHTING = HidDeviceInfo("fake\\vid_048d&pid_5702&col02", 0x048D, 0x5702, 0xFF89, 0xCC, 64)
OTHER_COLLECTION = HidDeviceInfo("fake\\vid_048d&pid_5702&col01", 0x048D, 0x5702, 0xFF89, 0x10, 17)

USB_DEVICE = {
    "device_id": "rgbfusion2-048d-5702",
    "name": "B550 AORUS ELITE lighting",
    "model": "RGB Fusion 2",
    "transport": "usb",
    "type": "rgb_fusion2",
    "device_kind": "motherboard",
}
# Colour correction off: the bytes sent are the sRGB values, which keeps the
# older byte-level expectations readable. Correction has its own tests.
RAW_DEVICE = {**USB_DEVICE, "color_correction": "none"}
ZONED_DEVICE = {**RAW_DEVICE, "motherboard": "B550 AORUS ELITE", "rgb_fusion_layout": "zones"}


def packet(hex_bytes: str) -> bytes:
    data = bytes.fromhex(hex_bytes)
    return data + bytes(64 - len(data))


class FakeHidDevice:
    def __init__(self, backend, path):
        self.backend = backend
        self.path = path
        self.is_open = False

    def open(self):
        self.backend.events.append("open")
        if self.backend.open_error is not None:
            raise self.backend.open_error
        self.is_open = True
        self.backend.opened += 1

    def send_feature_report(self, data):
        assert self.is_open, "report sent on a closed handle"
        if self.backend.send_failures:
            self.backend.send_failures -= 1
            raise OSError(22, "The device does not recognize the command")
        self.backend.reports.append(bytes(data))
        if self.backend.yield_between_reports:
            time.sleep(0.0005)  # Invite another thread in mid-frame.

    def get_feature_report(self, report_id, length):
        assert self.is_open
        self.backend.events.append(("get", report_id, length))
        return self.backend.reply

    def close(self):
        if self.is_open:
            self.backend.closed += 1
        self.is_open = False
        self.backend.events.append("close")


class FakeHid:
    """Records feature reports; never touches real hardware."""

    def __init__(self, infos=(LIGHTING, OTHER_COLLECTION), reply=GOLDEN_REPLY):
        self.infos = list(infos)
        self.reply = reply
        self.reports = []
        self.events = []
        self.enumerations = 0
        self.enumerate_error = None
        self.open_error = None
        self.send_failures = 0
        self.opened = 0
        self.closed = 0
        self.yield_between_reports = False

    def enumerate(self, vendor_id, product_id):
        self.enumerations += 1
        if self.enumerate_error is not None:
            raise self.enumerate_error
        return [info for info in self.infos if (info.vendor_id, info.product_id) == (vendor_id, product_id)]

    def HidDevice(self, path):  # noqa: N802 - mirrors hid_feature.HidDevice
        return FakeHidDevice(self, path)


class UntouchableHid:
    """Fails the test on any use: proves a code path does no HID access."""

    def __getattr__(self, name):
        raise AssertionError(f"HID backend touched: {name}")


def colour_of(report: bytes):
    """(r, g, b) of a zone packet."""
    return (report[16], report[15], report[14])


class FusionTestCase(unittest.TestCase):
    def setUp(self):
        self.hid = FakeHid()
        self.sleeps = []
        # A frozen clock: tests move it to make a sent frame fresh or stale.
        self.clock = [1000.0]
        patches = [
            patch.object(fusion, "_hid", self.hid),
            patch.object(fusion, "_sleep", self.sleeps.append),
            patch.object(fusion, "_monotonic", lambda: self.clock[0]),
            patch.dict(fusion._controllers, clear=True),
            patch.object(fusion, "motherboard_product", return_value="B550 AORUS ELITE"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def adapter(self, device=None):
        adapter = fusion.GigabyteFusionAdapter(dict(RAW_DEVICE if device is None else device))
        self.addCleanup(adapter.close)
        return adapter

    def register_frames(self, reports):
        """Split colour reports into frames of eight register colours."""
        self.assertEqual(len(reports) % 9, 0, "a frame was cut short")
        frames = []
        for start in range(0, len(reports), 9):
            chunk = reports[start:start + 9]
            self.assertEqual([r[1] for r in chunk], [0x20 + z for z in range(8)] + [0x28])
            self.assertEqual(chunk[8], packet("cc 28 ff"))
            frames.append(tuple(colour_of(r) for r in chunk[:8]))
        return frames

    def frames(self, reports):
        """Split colour reports into whole frames, checking their structure."""
        self.assertEqual(len(reports) % 9, 0, "a frame was cut short")
        frames = []
        for start in range(0, len(reports), 9):
            chunk = reports[start:start + 9]
            self.assertEqual([r[1] for r in chunk], [0x20 + z for z in range(8)] + [0x28])
            colours = {colour_of(r) for r in chunk[:8]}
            self.assertEqual(len(colours), 1, "zones of one frame disagree")
            frames.append(colours.pop())
        return frames


class PacketTests(unittest.TestCase):
    def test_identify_query_golden_bytes(self):
        self.assertEqual(fusion.build_identify_query(), packet("cc 60 00"))

    def test_zone_packet_golden_bytes(self):
        expected = packet("cc 20 01 00 00 00 00 00 00 00 00 01 ff 00 00 00 ff")
        self.assertEqual(fusion.build_zone_static(0, (255, 0, 0)), expected)
        self.assertEqual(len(expected), 64)

    def test_zone_packet_orders_blue_green_red_and_masks_its_zone(self):
        report = fusion.build_zone_static(5, (0x11, 0x22, 0x33))
        self.assertEqual(report[1], 0x25)
        self.assertEqual(report[2:6], (1 << 5).to_bytes(4, "little"))
        self.assertEqual(report[11:17], bytes([0x01, 0xFF, 0x00, 0x33, 0x22, 0x11]))
        with self.assertRaises(ValueError):
            fusion.build_zone_static(8, (0, 0, 0))

    def test_apply_and_mode_packets(self):
        self.assertEqual(fusion.build_apply(), packet("cc 28 ff"))
        self.assertEqual(fusion.build_audio_mode_off(), packet("cc 31 00"))
        self.assertEqual(fusion.build_addressable_mode(), packet("cc 32 00"))

    def test_full_frame_is_eight_zones_then_apply(self):
        frame = fusion.build_static_frame((1, 2, 3))
        self.assertEqual([r[1] for r in frame], [0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28])
        self.assertTrue(all(len(r) == 64 and r[0] == 0xCC for r in frame))

    def test_golden_identity_parse(self):
        identity = fusion.parse_identity(GOLDEN_REPLY)
        self.assertEqual(identity.product_string, "IT5702-GIGABYTE V2.0.10.0")
        self.assertEqual(identity.firmware, "2.0.10.0")
        self.assertEqual(identity.chip_id, 0x57020100)
        self.assertEqual(identity.product, 0x01)
        self.assertEqual(identity.strip_detect, 0x07)
        self.assertEqual(identity.mainboard_calibration, bytes.fromhex("00010200"))

    def test_bad_replies_are_rejected(self):
        wrong_report = bytes([0xCB]) + GOLDEN_REPLY[1:]
        wrong_product = GOLDEN_REPLY[:1] + bytes([0x02]) + GOLDEN_REPLY[2:]
        for reply in (wrong_report, wrong_product, GOLDEN_REPLY[:20], bytes(64)):
            with self.subTest(reply=reply[:2].hex()):
                with self.assertRaises(fusion.NotRgbFusionController):
                    fusion.parse_identity(reply)

    def test_forbidden_commands_cannot_be_built_or_sent(self):
        for command in fusion.NEVER_SEND_COMMANDS:
            with self.subTest(command=hex(command)):
                with self.assertRaises(ValueError):
                    fusion._report(command)
                sent = []
                forged = bytes([0xCC, command]) + bytes(62)
                with self.assertRaises(ValueError):
                    fusion._send(type("D", (), {"send_feature_report": sent.append})(), forged)
                self.assertEqual(sent, [])

    def test_brightness_scales_the_colour(self):
        self.assertEqual(fusion.scale_color((200, 100, 50), 50), (100, 50, 25))
        self.assertEqual(fusion.scale_color((255, 255, 255), 0), (0, 0, 0))
        self.assertEqual(fusion.scale_color((300, -5, 10), 100), (255, 0, 10))


class AdapterTests(FusionTestCase):
    def test_open_sequence_runs_once_in_order(self):
        adapter = self.adapter()
        adapter.set_color(255, 0, 0)
        self.assertEqual(self.hid.reports[0], fusion.build_identify_query())
        self.assertEqual(self.hid.events[:2], ["open", ("get", 0xCC, 64)])
        self.assertEqual(self.hid.reports[1], packet("cc 31 00"))
        self.assertEqual(self.hid.reports[2], packet("cc 32 00"))
        self.assertEqual(self.sleeps, [0.05])
        self.assertEqual(self.frames(self.hid.reports[3:]), [(255, 0, 0)])
        adapter.set_color(0, 255, 0)
        self.assertEqual(self.hid.opened, 1)
        self.assertEqual(self.hid.reports.count(fusion.build_identify_query()), 1)

    def test_set_color_sends_a_full_frame(self):
        adapter = self.adapter()
        adapter.set_color(255, 0, 0)
        frame = self.hid.reports[3:]
        self.assertEqual(frame[0], packet("cc 20 01 00 00 00 00 00 00 00 00 01 ff 00 00 00 ff"))
        self.assertEqual(frame[8], packet("cc 28 ff"))
        self.assertTrue(all(report[12] == 0xFF and report[13] == 0 for report in frame[:8]))

    def test_brightness_is_applied_in_software(self):
        adapter = self.adapter()
        adapter.set_color(200, 100, 50)
        adapter.set_brightness(50)
        frames = self.frames(self.hid.reports[3:])
        self.assertEqual(frames, [(200, 100, 50), (100, 50, 25)])
        self.assertTrue(all(r[12] == 0xFF for r in self.hid.reports[3:] if r[1] < 0x28))
        adapter.set_color(0, 0, 200)
        self.assertEqual(self.frames(self.hid.reports[3:])[-1], (0, 0, 100))

    def test_power_off_sends_black_and_on_restores_the_last_colour(self):
        adapter = self.adapter()
        adapter.set_color(10, 20, 30)
        adapter.set_brightness(50)
        adapter.set_power(False)
        adapter.set_color(200, 0, 0)  # Remembered while off, not shown.
        adapter.set_brightness(100)
        adapter.set_power(True)
        self.assertEqual(
            self.frames(self.hid.reports[3:]),
            [(10, 20, 30), (5, 10, 15), (0, 0, 0), (200, 0, 0)],
        )

    def test_power_on_without_a_colour_is_white_at_full_brightness(self):
        self.adapter().set_power(True)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(255, 255, 255)])

    def test_redundant_frames_are_skipped(self):
        adapter = self.adapter()
        adapter.set_color(1, 2, 3)
        sent = len(self.hid.reports)
        adapter.set_color(1, 2, 3)
        adapter.set_brightness(100)
        adapter.set_segments([(1, 2, 3)])
        adapter.set_power(True)
        self.assertEqual(len(self.hid.reports), sent)
        adapter.set_power(False)
        adapter.set_power(False)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(1, 2, 3), (0, 0, 0)])

    def test_an_identical_frame_is_sent_again_once_it_is_stale(self):
        # No readback: repeating the colour is the only way to restore it
        # after the board reset its own lighting (for example on resume).
        adapter = self.adapter()
        adapter.set_color(1, 2, 3)
        self.clock[0] += fusion.REPEAT_AFTER_SECONDS / 2
        adapter.set_color(1, 2, 3)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(1, 2, 3)])
        self.clock[0] += fusion.REPEAT_AFTER_SECONDS
        adapter.set_color(1, 2, 3)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(1, 2, 3), (1, 2, 3)])

    def test_set_segments_shows_the_frame_average_on_the_whole_board(self):
        adapter = self.adapter()
        adapter.set_brightness(50)
        adapter.set_segments([(200, 0, 0), (0, 0, 100)])
        adapter.set_segments([])  # Nothing to show: nothing sent.
        half_white = fusion.scale_color((255, 255, 255), 50)
        self.assertEqual(self.frames(self.hid.reports[3:]), [half_white, (50, 0, 25)])

    def test_set_segments_keeps_the_manual_colour_for_power_on(self):
        adapter = self.adapter()
        adapter.set_color(0, 255, 0)
        adapter.set_segments([(9, 9, 9)])
        adapter.set_power(False)
        adapter.set_power(True)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(0, 255, 0), (9, 9, 9), (0, 0, 0), (0, 255, 0)])

    def test_send_failure_drops_the_handle_and_the_next_command_recovers(self):
        adapter = self.adapter()
        adapter.set_color(1, 1, 1)
        self.hid.send_failures = 1
        with self.assertRaises(RuntimeError) as caught:
            adapter.set_color(2, 2, 2)
        self.assertEqual(str(caught.exception), fusion.LOST_MESSAGE)
        self.assertEqual(self.hid.closed, 1)
        before = len(self.hid.reports)
        adapter.set_color(2, 2, 2)
        self.assertEqual(self.hid.opened, 2)
        reopened = self.hid.reports[before:]
        self.assertEqual([r[1] for r in reopened[:3]], [0x60, 0x31, 0x32])
        self.assertEqual(self.frames(reopened[3:]), [(2, 2, 2)])

    def test_missing_controller_fails_clearly_and_recovers_when_back(self):
        self.hid.infos = [OTHER_COLLECTION]
        adapter = self.adapter()
        with self.assertRaises(RuntimeError) as caught:
            adapter.set_color(5, 5, 5)
        self.assertEqual(str(caught.exception), fusion.NOT_FOUND_MESSAGE)
        self.assertEqual(self.hid.reports, [])
        self.hid.infos = [LIGHTING]
        adapter.set_color(5, 5, 5)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(5, 5, 5)])

    # Access denied means "another program holds it" on Windows only; Linux
    # explains the udev rule instead (LinuxTests).
    @patch.object(sys, "platform", "win32")
    def test_open_denied_and_enumeration_errors_surface_as_runtime_errors(self):
        adapter = self.adapter()
        # OSError(13, ...) is a PermissionError; spelt out so the intent shows.
        self.hid.open_error = PermissionError(13, "Access is denied")
        with self.assertRaises(RuntimeError) as caught:
            adapter.set_power(True)
        self.assertEqual(str(caught.exception), fusion.OPEN_FAILED_MESSAGE)
        self.hid.open_error = None
        self.hid.enumerate_error = OSError(5, "enumeration failed")
        with self.assertRaises(RuntimeError):
            adapter.begin_stream()
        self.hid.enumerate_error = None
        adapter.begin_stream()
        self.assertEqual([r[1] for r in self.hid.reports], [0x60, 0x31, 0x32])

    def test_a_controller_that_does_not_identify_is_never_driven(self):
        self.hid.reply = bytes([0xCC, 0x07]) + GOLDEN_REPLY[2:]
        adapter = self.adapter()
        with self.assertRaises(RuntimeError) as caught:
            adapter.set_color(255, 255, 255)
        self.assertEqual(str(caught.exception), fusion.NOT_FUSION_MESSAGE)
        self.assertEqual(self.hid.reports, [fusion.build_identify_query()])
        self.assertEqual(self.hid.closed, 1)

    def test_two_adapters_share_one_handle_and_never_interleave_frames(self):
        first, second = self.adapter(), self.adapter()
        self.hid.yield_between_reports = True
        errors = []

        def drive(adapter, colours):
            try:
                for colour in colours:
                    adapter.set_color(*colour)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        red = [(255, 0, 0), (200, 0, 0)] * 15
        blue = [(0, 0, 255), (0, 0, 200)] * 15
        threads = [threading.Thread(target=drive, args=(first, red)),
                   threading.Thread(target=drive, args=(second, blue))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(self.hid.opened, 1)
        self.assertEqual([r[1] for r in self.hid.reports[:3]], [0x60, 0x31, 0x32])
        frames = self.frames(self.hid.reports[3:])  # Raises on any interleaving.
        self.assertTrue(set(frames) <= set(red) | set(blue))
        # The handle stays while one adapter still uses it.
        first.close()
        self.assertEqual(self.hid.closed, 0)
        second.close()
        self.assertEqual(self.hid.closed, 1)

    def test_lighting_state_is_shared_between_adapters(self):
        gui, sync = self.adapter(), self.adapter()
        gui.set_color(0, 100, 0)
        sync.set_brightness(50)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(0, 100, 0), (0, 50, 0)])

    def test_close_does_not_restore_or_send_anything(self):
        adapter = self.adapter()
        adapter.set_color(1, 2, 3)
        sent = list(self.hid.reports)
        adapter.end_stream()
        adapter.close()
        adapter.close()
        self.assertEqual(self.hid.reports, sent)
        self.assertEqual(self.hid.closed, 1)

    def test_every_public_method_emits_only_allowed_commands(self):
        adapter = self.adapter()
        adapter.begin_stream()
        adapter.keep_stream_alive()
        adapter.set_power(True)
        adapter.set_brightness(40)
        adapter.set_color(10, 200, 30)
        adapter.set_color_temperature(2700)
        adapter.set_segments([(1, 2, 3), (4, 5, 6)])
        adapter.set_power(False)
        self.assertIsNone(adapter.query_status())
        adapter.share_connection(self.adapter())
        adapter.end_stream()
        adapter.close()
        fusion.discover()
        self.assertTrue(self.hid.reports)
        commands = {report[1] for report in self.hid.reports}
        self.assertTrue(commands <= fusion.ALLOWED_COMMANDS, commands)
        self.assertFalse(commands & fusion.NEVER_SEND_COMMANDS)
        allowed = {0x60, 0x31, 0x32, 0x28, *range(0x20, 0x28)}
        self.assertEqual(fusion.ALLOWED_COMMANDS, frozenset(allowed))
        self.assertTrue(all(len(r) == 64 and r[0] == 0xCC for r in self.hid.reports))

    def test_unverified_controller_ids_are_refused(self):
        adapter = fusion.GigabyteFusionAdapter({**USB_DEVICE, "usb_product_id": 0x8297})
        with self.assertRaises(RuntimeError):
            adapter.set_color(1, 1, 1)
        self.assertEqual(self.hid.enumerations, 0)
        self.assertFalse(fusion.controller_present({**USB_DEVICE, "usb_product_id": 0x8297}))


class NoHardwareAccessTests(unittest.TestCase):
    def test_construction_and_capability_reads_touch_no_hid_or_subprocess(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("subprocess spawned")

        with patch.object(fusion, "_hid", UntouchableHid()), \
                patch.object(hid_feature, "_windows_api", side_effect=AssertionError("ctypes used")), \
                patch("subprocess.Popen", side_effect=forbidden):
            adapter = fusion.GigabyteFusionAdapter(dict(USB_DEVICE))
            cap = adapter.capabilities
            self.assertEqual(registry.capabilities_for_device(USB_DEVICE), cap)
            self.assertEqual(registry.capabilities_for_device({"type": "rgb_fusion2"}), cap)
            self.assertIsInstance(registry.create_adapter(USB_DEVICE), fusion.GigabyteFusionAdapter)
            adapter.close()  # Never opened: nothing to release.
        self.assertEqual(cap.transport, "usb")
        self.assertEqual(cap.segment_count, 1)
        self.assertTrue(cap.supports_power and cap.supports_brightness and cap.supports_color)
        self.assertTrue(cap.supports_segments and cap.supports_streaming)
        self.assertFalse(cap.supports_white)
        self.assertEqual(cap.max_update_hz, 30.0)


class DiscoveryTests(FusionTestCase):
    def test_nothing_present(self):
        self.hid.infos = []
        self.assertEqual(fusion.discover(), [])
        self.assertEqual(self.hid.reports, [])

    def test_matching_controller(self):
        found = fusion.discover()
        self.assertEqual(len(found), 1)
        descriptor = found[0]
        self.assertEqual(descriptor["device_id"], "rgbfusion2-048d-5702")
        self.assertEqual(descriptor["name"], "B550 AORUS ELITE lighting")
        self.assertEqual(descriptor["transport"], "usb")
        self.assertEqual(descriptor["type"], "rgb_fusion2")
        self.assertEqual(descriptor["device_kind"], "motherboard")
        self.assertEqual(descriptor["rgb_fusion_firmware"], "2.0.10.0")
        self.assertEqual(descriptor["rgb_fusion_product"], "IT5702-GIGABYTE V2.0.10.0")
        # Discovery only identifies; it never prepares the controller for colour.
        self.assertEqual(self.hid.reports, [fusion.build_identify_query()])
        self.assertEqual(self.hid.closed, 1)

    def test_fallback_name_without_a_board_name(self):
        with patch.object(fusion, "motherboard_product", return_value=""):
            self.assertEqual(fusion.discover()[0]["name"], "Gigabyte RGB Fusion lighting")

    def test_wrong_collection_only(self):
        self.hid.infos = [OTHER_COLLECTION]
        self.assertEqual(fusion.discover(), [])
        self.assertEqual(self.hid.events, [])

    # On Linux a denied open still lists the board (LinuxTests twin).
    @patch.object(sys, "platform", "win32")
    def test_backend_failures_never_raise(self):
        self.hid.enumerate_error = OSError(5, "boom")
        self.assertEqual(fusion.discover(), [])
        self.hid.enumerate_error = None
        self.hid.open_error = PermissionError(13, "denied")
        self.assertEqual(fusion.discover(), [])
        self.hid.open_error = None
        self.hid.reply = bytes(64)
        self.assertEqual(fusion.discover(), [])
        with patch.object(fusion, "_hid", UntouchableHid()):
            self.assertEqual(fusion.discover(), [])

    def test_discovery_during_a_connection_reuses_its_identity(self):
        adapter = self.adapter()
        adapter.set_color(3, 3, 3)
        sent = len(self.hid.reports)
        self.assertEqual(len(fusion.discover()), 1)
        self.assertEqual(len(self.hid.reports), sent)

    def test_presence_check_only_enumerates(self):
        self.assertTrue(fusion.controller_present(USB_DEVICE))
        self.hid.infos = [OTHER_COLLECTION]
        self.assertFalse(fusion.controller_present(USB_DEVICE))
        self.hid.enumerate_error = OSError(5, "boom")
        self.assertFalse(fusion.controller_present(USB_DEVICE))
        self.assertEqual(self.hid.events, [])

    def test_local_hardware_discovery_never_raises(self):
        with patch.object(fusion, "discover", side_effect=RuntimeError("boom")):
            self.assertEqual(devices.discover_local_hardware(), [])
        self.assertEqual(devices.discover_local_hardware()[0]["device_id"], "rgbfusion2-048d-5702")


class UnsupportedPlatformTests(unittest.TestCase):
    """Behaviour on a platform with no HID backend (neither Windows nor Linux)."""

    PLATFORM = "darwin"

    def test_hid_layer_is_inert_on_an_unsupported_platform(self):
        with patch.object(sys, "platform", self.PLATFORM):
            self.assertFalse(hid_feature.supported())
            self.assertEqual(hid_feature.enumerate(0x048D, 0x5702), [])
            device = hid_feature.HidDevice("anything")
            with self.assertRaises(hid_feature.HidUnsupportedPlatform) as caught:
                device.open()
            self.assertIn("not supported on this platform", str(caught.exception))
            self.assertIsInstance(caught.exception, OSError)
            device.close()
            self.assertEqual(fusion.motherboard_product(), "")

    def test_driver_reports_unsupported_platform_at_command_time(self):
        with patch.object(sys, "platform", self.PLATFORM), \
                patch.dict(fusion._controllers, clear=True), \
                patch.object(fusion, "_hid", hid_feature):
            self.assertEqual(fusion.discover(), [])
            self.assertFalse(fusion.controller_present(USB_DEVICE))
            adapter = fusion.GigabyteFusionAdapter(dict(USB_DEVICE))
            with self.assertRaises(RuntimeError) as caught:
                adapter.set_color(1, 2, 3)
            # Enumeration finds nothing there, so it reads as "not found".
            self.assertEqual(str(caught.exception), fusion.NOT_FOUND_MESSAGE)
            adapter.close()

    def test_open_on_unsupported_platform_maps_to_a_clear_error(self):
        class Unsupported(FakeHid):
            def HidDevice(self, path):  # noqa: N802
                return hid_feature.HidDevice(path)

        with patch.object(sys, "platform", self.PLATFORM), \
                patch.dict(fusion._controllers, clear=True), \
                patch.object(fusion, "_hid", Unsupported()):
            with self.assertRaises(RuntimeError) as caught:
                fusion.GigabyteFusionAdapter(dict(USB_DEVICE)).set_color(1, 2, 3)
        self.assertEqual(str(caught.exception), fusion.UNSUPPORTED_MESSAGE)
        self.assertIn("Windows and Linux", fusion.UNSUPPORTED_MESSAGE)


HIDRAW_LIGHTING = HidDeviceInfo("/dev/hidraw3", 0x048D, 0x5702, 0xFF89, 0xCC, 64)
HIDRAW_OTHER = HidDeviceInfo("/dev/hidraw3", 0x048D, 0x5702, 0xFF89, 0x10, 17)


class LinuxTests(FusionTestCase):
    """Linux behaviour of the driver, against the fake HID backend."""

    def setUp(self):
        super().setUp()
        # One hidraw node carries both collections on Linux.
        self.hid.infos = [HIDRAW_LIGHTING, HIDRAW_OTHER]
        item = patch.object(sys, "platform", "linux")
        item.start()
        self.addCleanup(item.stop)

    def test_permission_denied_explains_the_udev_rule_at_command_time(self):
        self.hid.open_error = PermissionError(13, "Permission denied", "/dev/hidraw3")
        adapter = self.adapter()
        with self.assertRaises(RuntimeError) as caught:
            adapter.set_color(1, 2, 3)
        message = str(caught.exception)
        self.assertEqual(message, fusion.permission_denied_message("/dev/hidraw3"))
        self.assertIn("/dev/hidraw3", message)
        self.assertIn(fusion.UDEV_RULE, message)
        self.assertIn(f"/etc/udev/rules.d/{fusion.UDEV_RULE_FILENAME}", message)
        self.assertIn("udevadm", message)
        self.assertEqual(self.hid.reports, [])
        # Once the rule is in place the next command connects normally.
        self.hid.open_error = None
        adapter.set_color(1, 2, 3)
        self.assertEqual([r[1] for r in self.hid.reports[:3]], [0x60, 0x31, 0x32])

    def test_other_open_failures_keep_the_generic_message(self):
        self.hid.open_error = OSError(16, "Device or resource busy")
        with self.assertRaises(RuntimeError) as caught:
            self.adapter().set_color(1, 2, 3)
        self.assertEqual(str(caught.exception), fusion.OPEN_FAILED_MESSAGE)

    def test_permission_denied_on_windows_keeps_the_generic_message(self):
        self.hid.open_error = PermissionError(13, "Access is denied")
        with patch.object(sys, "platform", "win32"):
            with self.assertRaises(RuntimeError) as caught:
                self.adapter().set_color(1, 2, 3)
        self.assertEqual(str(caught.exception), fusion.OPEN_FAILED_MESSAGE)

    def test_discovery_without_permission_lists_the_bare_board(self):
        self.hid.open_error = PermissionError(13, "Permission denied", "/dev/hidraw3")
        found = fusion.discover()
        self.assertEqual(len(found), 1)
        descriptor = found[0]
        self.assertEqual(descriptor["device_id"], "rgbfusion2-048d-5702")
        self.assertEqual(descriptor["name"], "B550 AORUS ELITE lighting")
        self.assertEqual(descriptor["type"], "rgb_fusion2")
        self.assertEqual((descriptor["usb_vendor_id"], descriptor["usb_product_id"]), (0x048D, 0x5702))
        for key in ("rgb_fusion_firmware", "rgb_fusion_product", "rgb_fusion_chip_id"):
            self.assertNotIn(key, descriptor)
        self.assertEqual(self.hid.reports, [])
        self.assertTrue(fusion.controller_present(USB_DEVICE))

    def test_discovery_with_permission_identifies_as_on_windows(self):
        found = fusion.discover()
        self.assertEqual(found[0]["rgb_fusion_firmware"], "2.0.10.0")
        self.assertEqual(self.hid.reports, [fusion.build_identify_query()])

    def test_discovery_still_skips_a_controller_that_is_not_rgb_fusion(self):
        self.hid.reply = bytes([0xCC, 0x07]) + GOLDEN_REPLY[2:]
        self.assertEqual(fusion.discover(), [])

    def test_backend_failures_never_raise(self):
        self.hid.enumerate_error = OSError(5, "boom")
        self.assertEqual(fusion.discover(), [])
        self.hid.enumerate_error = None
        # Denied: the udev rule is missing, so the bare board is listed.
        self.hid.open_error = PermissionError(13, "denied")
        found = fusion.discover()
        self.assertEqual([d["device_id"] for d in found], ["rgbfusion2-048d-5702"])
        self.assertNotIn("rgb_fusion_firmware", found[0])
        self.hid.open_error = OSError(16, "Device or resource busy")
        self.assertEqual(fusion.discover(), [])
        self.hid.open_error = None
        self.hid.reply = bytes(64)
        self.assertEqual(fusion.discover(), [])
        with patch.object(fusion, "_hid", UntouchableHid()):
            self.assertEqual(fusion.discover(), [])
        self.assertEqual(self.hid.reports, [fusion.build_identify_query()])

    def test_discovery_skips_other_open_failures(self):
        self.hid.open_error = OSError(16, "Device or resource busy")
        self.assertEqual(fusion.discover(), [])
        with patch.object(sys, "platform", "win32"):
            self.hid.open_error = PermissionError(13, "Access is denied")
            self.assertEqual(fusion.discover(), [])

    def test_board_name_comes_from_dmi(self):
        with tempfile.TemporaryDirectory() as folder:
            board_name = os.path.join(folder, "board_name")
            with open(board_name, "w", encoding="utf-8") as handle:
                handle.write("B550 AORUS ELITE\n")
            with patch.object(fusion, "_DMI_BOARD_NAME", board_name), \
                    patch.object(fusion, "motherboard_product", ORIGINAL_MOTHERBOARD_PRODUCT):
                self.assertEqual(fusion.motherboard_product(), "B550 AORUS ELITE")
            with patch.object(fusion, "_DMI_BOARD_NAME", os.path.join(folder, "missing")), \
                    patch.object(fusion, "motherboard_product", ORIGINAL_MOTHERBOARD_PRODUCT):
                self.assertEqual(fusion.motherboard_product(), "")


class UdevRuleFileTests(unittest.TestCase):
    def test_the_packaged_rules_file_carries_the_rule(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "packaging", "linux", fusion.UDEV_RULE_FILENAME)
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle]
        rules = [line for line in lines if line and not line.startswith("#")]
        # One file for all PC lighting: the board's, the mouse's and the keyboard's rule.
        from lumisync.drivers import logitech_hidpp, lorgar_keyboard

        self.assertEqual(rules, [fusion.UDEV_RULE, logitech_hidpp.UDEV_RULE, lorgar_keyboard.UDEV_RULE])
        self.assertEqual(fusion.UDEV_RULE_FILENAME, logitech_hidpp.UDEV_RULE_FILENAME)
        self.assertEqual(fusion.UDEV_RULE_FILENAME, lorgar_keyboard.UDEV_RULE_FILENAME)
        with open(path, "rb") as handle:
            self.assertNotIn(b"\r", handle.read())
        self.assertIn('ATTRS{idVendor}=="048d"', fusion.UDEV_RULE)
        self.assertIn('ATTRS{idProduct}=="5702"', fusion.UDEV_RULE)
        self.assertIn('TAG+="uaccess"', fusion.UDEV_RULE)


class IntegrationTests(FusionTestCase):
    def test_device_normalises_the_rgb_fusion_type_to_usb(self):
        device = Device.from_dict({"type": "rgb_fusion2", "device_id": "rgbfusion2-048d-5702"})
        self.assertEqual(device.transport, "usb")
        self.assertTrue(device.is_usb)
        self.assertTrue(device.is_pooled)
        self.assertFalse(device.is_cloud or device.is_ble)
        saved = device.to_dict()
        self.assertEqual(saved["transport"], "usb")
        self.assertEqual(saved["type"], "rgb_fusion2")
        self.assertEqual(device.identity, "rgbfusion2-048d-5702")

    def test_registry_dispatch(self):
        for descriptor in (USB_DEVICE, {"transport": "usb"}, {"type": "rgb_fusion2"}):
            with self.subTest(descriptor=descriptor):
                self.assertIsInstance(registry.create_adapter(descriptor), fusion.GigabyteFusionAdapter)
        self.assertFalse(registry.is_cloud(USB_DEVICE))

    def test_pool_hands_out_one_adapter_per_controller(self):
        self.addCleanup(pool.close, USB_DEVICE)
        first = pool.acquire(USB_DEVICE)
        renamed = pool.acquire({**USB_DEVICE, "name": "Renamed", "rgb_fusion_firmware": "2.0.11.0"})
        self.assertIs(first, renamed)
        self.assertIsInstance(first, fusion.GigabyteFusionAdapter)
        first.set_color(1, 1, 1)
        pool.close(USB_DEVICE)
        self.assertEqual(self.hid.closed, 1)

    def test_discovery_merge_updates_the_saved_board(self):
        saved = [{"mac": "AA:BB", "ip": "192.0.2.1", "model": "H6159"},
                 {**USB_DEVICE, "rgb_fusion_firmware": "2.0.9.0"}]
        merged = devices.merge_discovered_devices(saved, fusion.discover())
        self.assertEqual(len(merged), 2)
        board = as_device(merged[1])
        self.assertEqual(board.transport, "usb")
        self.assertEqual(board.get("rgb_fusion_firmware"), "2.0.10.0")
        fresh = devices.merge_discovered_devices([saved[0]], fusion.discover())
        self.assertEqual([as_device(d).transport for d in fresh], ["lan", "usb"])

    def test_discovery_worker_adds_usb_lighting_without_counting_it_as_a_lan_reply(self):
        from lumisync.gui.controllers.discovery import DeviceDiscoveryWorker

        lan = {"mac": "AA:BB", "ip": "192.0.2.1", "model": "H6159"}
        settings = {"devices": [lan], "selectedDevice": 0, "lastDiscoveryCount": 1,
                    "lastDiscoveryDevices": [lan]}
        worker = DeviceDiscoveryWorker()
        results = []
        worker.finished.connect(lambda found, selected, count: results.append((found, count)))
        with patch.object(devices, "discover_lan_devices", return_value=settings):
            worker.run()
        found, count = results[0]
        self.assertEqual(count, 1)
        self.assertEqual([as_device(d).transport for d in found], ["lan", "usb"])

    def test_status_worker_reports_presence_without_any_report_exchange(self):
        from lumisync.gui.controllers import discovery

        updates = []
        for present in (True, False):
            worker = discovery.DeviceStatusWorker([(0, dict(USB_DEVICE))])
            worker.state_updated.connect(lambda index, update: updates.append(update))
            with patch.object(fusion, "controller_present", return_value=present) as probe, \
                    patch.object(discovery.pool, "acquire", side_effect=AssertionError("adapter opened")), \
                    patch.object(discovery, "create_adapter", side_effect=AssertionError("adapter opened")), \
                    patch.object(discovery.connection, "create_lan_socket",
                                 side_effect=AssertionError("LAN socket opened")):
                worker.run()
            probe.assert_called_once()
        attached, missing = updates
        self.assertEqual(attached["status_source"], "attached")
        self.assertTrue(attached["online"])
        self.assertFalse(attached["readback_supported"])
        self.assertIsNone(attached["last_error"])
        self.assertEqual(missing["status_source"], "offline")
        self.assertFalse(missing["online"])
        self.assertFalse(missing["readback_supported"])
        self.assertEqual(missing["last_error"], fusion.NOT_FOUND_MESSAGE)
        self.assertEqual(self.hid.reports, [])


class PresentationTests(unittest.TestCase):
    def test_labels_read_as_motherboard_lighting_over_usb(self):
        from lumisync.gui.resources.icons import IconKey
        from lumisync.gui.utils.device_identity import device_identity
        from lumisync.gui.widgets.device_card import connection_name, device_subline
        from lumisync.gui.widgets.device_chip import light_summary

        self.assertEqual(device_identity(USB_DEVICE), ("Motherboard lighting", IconKey.MOTHERBOARD))
        self.assertEqual(connection_name(USB_DEVICE), "USB")
        self.assertEqual(device_subline(USB_DEVICE), "Motherboard lighting · USB")
        self.assertEqual(light_summary(USB_DEVICE), "Motherboard lighting · USB")

    def test_attached_status_shows_what_was_sent(self):
        from lumisync.gui.widgets.device_card import TONE_ONLINE, describe_device_status

        cap = fusion.GigabyteFusionAdapter.capabilities_for()
        status = describe_device_status({"status_source": "attached", "online": True,
                                         "readback_supported": False}, cap)
        self.assertEqual((status.text, status.tone), ("Connected", TONE_ONLINE))
        status = describe_device_status({"status_source": "attached", "online": True, "power_on": True,
                                         "brightness": 60, "readback_supported": False}, cap)
        self.assertEqual(status.text, "On · 60%")
        self.assertIn("can't report", status.tooltip)


class ControllerStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def controller(self):
        from lumisync.gui.controllers.device_controller import DeviceController

        with patch("lumisync.gui.controllers.device_controller.devices.get_data",
                   return_value={"devices": [], "selectedDevice": 0}):
            controller = DeviceController()
        self.addCleanup(controller.shutdown)
        controller.devices = [as_device(USB_DEVICE)]
        return controller

    def test_usb_lighting_has_no_readback_and_a_missing_board_reads_offline(self):
        controller = self.controller()
        state = controller.get_device_state_at(0)
        self.assertFalse(state["readback_supported"])
        self.assertEqual(state["status_source"], "unknown")
        controller._merge_device_state(0, {"status_source": "offline", "online": False, "stale": False,
                                           "readback_supported": False,
                                           "last_error": fusion.NOT_FOUND_MESSAGE})
        self.assertEqual(controller.get_device_state_at(0)["status_source"], "offline")
        controller._record_command_state(0, {"color": (1, 2, 3)})
        state = controller.get_device_state_at(0)
        self.assertEqual(state["status_source"], "commanded")
        self.assertFalse(state["readback_supported"])

    def test_commands_go_through_the_pooled_adapter_and_are_not_confirmed(self):
        controller = self.controller()
        calls = []

        class Adapter:
            def set_color(self, *rgb):
                calls.append(rgb)

        with patch("lumisync.gui.controllers.device_controller.pool.acquire", return_value=Adapter()):
            route = controller._run_adapter(controller.devices[0], lambda adapter: adapter.set_color(1, 2, 3))
        self.assertEqual(route, "USB")
        self.assertEqual(calls, [(1, 2, 3)])
        with patch.object(controller, "refresh_device_state_at") as refreshed:
            controller._schedule_status_refresh(0)
        refreshed.assert_not_called()
        self.assertNotIn(controller._device_key(controller.devices[0]), controller._confirmation_timers)

    def test_periodic_local_poll_includes_usb_lighting(self):
        controller = self.controller()
        with patch.object(controller, "_start_status_refresh") as started:
            controller.refresh_local_device_states()
        started.assert_called_once_with([0])


if __name__ == "__main__":
    unittest.main()
