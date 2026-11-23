"""The HID feature-report layer: descriptor parsing, sysfs scan, hidraw calls.

This module imports only the standard library and
``lumisync.drivers.hid_feature``, so it runs under a bare Linux Python with
no third-party packages. Nothing here opens a real device: the hidraw tests
mock ``os.open``, ``os.close`` and ``fcntl.ioctl``.
"""

import errno
import os
import random
import sys
import tempfile
import unittest
from unittest.mock import call, patch

from lumisync.drivers import hid_feature
from lumisync.drivers.hid_feature import HidCollection, HidDeviceInfo, parse_report_descriptor


def descriptor(hex_bytes: str) -> bytes:
    return bytes.fromhex(hex_bytes)


# A synthetic descriptor modelled on the ITE IT5702 (048D:5702) as Windows
# describes it: two application collections on vendor page 0xFF89, in
# Windows' order (Col01 is usage 0x10, Col02 is usage 0xCC). Usage 0x10 has
# feature report 0x5A with 16 data bytes (as 16 eight-bit fields); usage
# 0xCC has feature report 0xCC with 63 data bytes (as 504 one-bit fields,
# which Windows shows as buttons). The real descriptor bytes were not
# captured; this models the capabilities, not the exact encoding.
IT5702_DESCRIPTOR = descriptor(
    "06 89 ff"        # Usage Page (0xFF89)
    " 09 10"          # Usage (0x10)
    " a1 01"          # Collection (Application)
    " 85 5a"          #   Report ID (0x5A)
    " 09 01"          #   Usage (0x01)
    " 15 00 26 ff 00" #   Logical Minimum (0), Logical Maximum (255)
    " 75 08"          #   Report Size (8)
    " 95 10"          #   Report Count (16)
    " b1 02"          #   Feature (Data, Var, Abs)
    " c0"             # End Collection
    " 09 cc"          # Usage (0xCC)
    " a1 01"          # Collection (Application)
    " 85 cc"          #   Report ID (0xCC)
    " 09 01"          #   Usage (0x01)
    " 15 00 25 01"    #   Logical Minimum (0), Logical Maximum (1)
    " 75 01"          #   Report Size (1)
    " 96 f8 01"       #   Report Count (504)
    " b1 02"          #   Feature (Data, Var, Abs)
    " c0"             # End Collection
)

LIGHTING = HidCollection(0xFF89, 0xCC, 64)
OTHER = HidCollection(0xFF89, 0x10, 17)

# A synthetic HID++ interface like a Logitech G502 HERO's second USB
# interface: vendor page 0xFF00, usage 1 carries short reports (ID 0x10,
# 6 data bytes) and usage 2 long reports (ID 0x11, 19 data bytes), each as
# an input and an output report. Lengths match what Windows reports for the
# real mouse (7 and 20 bytes); the exact encoding is a model.
HIDPP_DESCRIPTOR = descriptor(
    "06 00 ff 09 01 a1 01"
    " 85 10 75 08 95 06 15 00 26 ff 00 09 01 81 00 09 01 91 00"
    " c0"
    " 06 00 ff 09 02 a1 01"
    " 85 11 75 08 95 13 15 00 26 ff 00 09 02 81 00 09 02 91 00"
    " c0"
)
HIDPP_SHORT = HidCollection(0xFF00, 0x01, 0, 7, 7)
HIDPP_LONG = HidCollection(0xFF00, 0x02, 0, 20, 20)

# The real descriptor, read from /sys/class/hidraw/hidraw0/device/report_descriptor
# on a B550 AORUS ELITE (IT5702, firmware 2.0.10.0) under Linux 6.18 on 2026-11-22.
IT5702_REAL_DESCRIPTOR = descriptor(
    "06 89 ff 09 10 a1 01 85 5a 09 01 15 00 26 ff 00 75 08 95 10 b1 00 c0"
    " 06 89 ff 09 cc a1 01 85 cc 09 01 15 00 26 ff 00 75 08 95 3f b1 00 c0"
)


class DescriptorParserTests(unittest.TestCase):
    def test_it5702_model_gives_the_windows_lengths(self):
        self.assertEqual(parse_report_descriptor(IT5702_DESCRIPTOR), [OTHER, LIGHTING])

    def test_the_real_it5702_descriptor(self):
        self.assertEqual(parse_report_descriptor(IT5702_REAL_DESCRIPTOR), [OTHER, LIGHTING])

    def test_without_report_ids_no_id_byte_is_counted(self):
        parsed = parse_report_descriptor(descriptor(
            "06 00 ff 09 01 a1 01 75 08 95 20 b1 02 c0"
        ))
        self.assertEqual(parsed, [HidCollection(0xFF00, 0x01, 32)])

    def test_the_longest_feature_report_of_a_collection_wins(self):
        parsed = parse_report_descriptor(descriptor(
            "06 00 ff 09 01 a1 01"
            " 85 01 75 08 95 04 b1 02"   # Report 1: 4 bytes
            " 85 02 75 08 95 09 b1 02"   # Report 2: 9 bytes
            " 85 01 75 08 95 02 b1 02"   # Report 1 again: 4 + 2 bytes
            " c0"
        ))
        self.assertEqual(parsed, [HidCollection(0xFF00, 0x01, 10)])

    def test_partial_bytes_round_up(self):
        parsed = parse_report_descriptor(descriptor(
            "06 00 ff 09 01 a1 01 75 01 95 03 b1 02 c0"
        ))
        self.assertEqual(parsed, [HidCollection(0xFF00, 0x01, 1)])

    def test_one_two_and_four_byte_items(self):
        parsed = parse_report_descriptor(descriptor(
            "07 89 ff 00 00"      # Usage Page, 4-byte form
            " 0a cc 00"           # Usage, 2-byte form
            " a1 01"
            " 87 cc 00 00 00"     # Report ID, 4-byte form
            " 77 08 00 00 00"     # Report Size, 4-byte form
            " 97 3f 00 00 00"     # Report Count, 4-byte form
            " b2 02 00"           # Feature, 2-byte form
            " c0"
        ))
        self.assertEqual(parsed, [LIGHTING])

    def test_extended_usage_carries_its_own_page(self):
        parsed = parse_report_descriptor(descriptor(
            "05 01"               # Usage Page (Generic Desktop)
            " 0b cc 00 89 ff"     # Usage (0xFF89:0x00CC), 4-byte extended form
            " a1 01 85 cc 75 08 95 3f b1 02 c0"
        ))
        self.assertEqual(parsed, [LIGHTING])

    def test_long_items_are_skipped(self):
        parsed = parse_report_descriptor(descriptor(
            "fe 03 10 aa bb cc"   # Long item, 3 data bytes, ahead of everything
            " 06 89 ff 09 cc a1 01 85 cc 75 08"
            " fe 00 20"           # Long item with no data, inside the collection
            " 95 3f b1 02 c0"
        ))
        self.assertEqual(parsed, [LIGHTING])

    def test_push_and_pop_restore_the_global_state(self):
        parsed = parse_report_descriptor(descriptor(
            "06 00 ff 09 01 a1 01"
            " 85 01 75 08 95 1e"  # Report 1, 8 x 30
            " a4"                 # Push
            " 85 02 95 0a b1 02"  # Report 2: 10 bytes
            " b4"                 # Pop: report 1, 8 x 30 again
            " b1 02"              # Report 1: 30 bytes
            " c0"
        ))
        # Without the Pop this would be report 2 at 20 data bytes (21).
        self.assertEqual(parsed, [HidCollection(0xFF00, 0x01, 31)])

    def test_nested_collections_count_toward_their_top_level_collection(self):
        parsed = parse_report_descriptor(descriptor(
            "06 89 ff 09 cc a1 01"
            " 09 02 a1 02"        # Logical collection with its own usage
            " 85 cc 75 08 95 20 b1 02"
            " 09 03 a1 00"        # Physical collection, nested deeper
            " 75 08 95 1f b1 02"
            " c0 c0"
            " c0"
        ))
        self.assertEqual(parsed, [LIGHTING])

    def test_input_and_output_only_collections_have_no_feature_length(self):
        parsed = parse_report_descriptor(descriptor(
            "05 01 09 06 a1 01 75 08 95 08 81 02 91 02 c0"
            " 06 89 ff 09 cc a1 01 85 cc 75 08 95 3f b1 02 c0"
        ))
        self.assertEqual(parsed, [HidCollection(0x01, 0x06, 0, 8, 8), LIGHTING])

    def test_input_and_output_lengths_are_tracked_per_report_id(self):
        # Modelled on a Logitech HID++ interface: short (0x10, 7 bytes) and
        # long (0x11, 20 bytes) reports in two vendor collections, each with
        # an input and an output report and no feature report.
        parsed = parse_report_descriptor(HIDPP_DESCRIPTOR)
        self.assertEqual(parsed, [HIDPP_SHORT, HIDPP_LONG])

    def test_non_application_top_level_collections_are_ignored(self):
        parsed = parse_report_descriptor(descriptor(
            "06 00 ff 09 01 a1 00 75 08 95 04 b1 02 c0"
        ) + IT5702_DESCRIPTOR)
        self.assertEqual(parsed, [OTHER, LIGHTING])

    def test_truncated_input_keeps_the_completed_collections(self):
        first_end = IT5702_DESCRIPTOR.index(b"\xc0") + 1
        for cut in range(len(IT5702_DESCRIPTOR)):
            with self.subTest(cut=cut):
                expected = [OTHER] if cut >= first_end else []
                self.assertEqual(parse_report_descriptor(IT5702_DESCRIPTOR[:cut]), expected)
        # Cut inside a multi-byte item and inside a long item.
        self.assertEqual(parse_report_descriptor(descriptor("06 89")), [])
        self.assertEqual(parse_report_descriptor(descriptor("fe 05 10 aa")), [])
        self.assertEqual(parse_report_descriptor(descriptor("fe")), [])

    def test_malformed_structure_stops_the_parse(self):
        # End Collection with nothing open, and Pop without Push.
        self.assertEqual(parse_report_descriptor(descriptor("c0") + IT5702_DESCRIPTOR), [])
        self.assertEqual(parse_report_descriptor(descriptor("b4") + IT5702_DESCRIPTOR), [])
        self.assertEqual(parse_report_descriptor(b""), [])

    def test_garbage_never_raises(self):
        rng = random.Random(5702)
        for _ in range(2000):
            blob = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 96)))
            result = parse_report_descriptor(blob)
            self.assertIsInstance(result, list)
            self.assertTrue(all(isinstance(item, HidCollection) for item in result))
        # Not even bytes.
        self.assertEqual(parse_report_descriptor(None), [])  # type: ignore[arg-type]


class HidIdTests(unittest.TestCase):
    def test_parses_the_hid_id_line_of_a_uevent_file(self):
        uevent = (
            "DRIVER=hid-generic\n"
            "HID_ID=0003:0000048D:00005702\n"
            "HID_NAME=ITE Tech. Inc. GIGABYTE Device\n"
            "HID_PHYS=usb-0000:0a:00.3-5/input0\n"
            "MODALIAS=hid:b0003g0001v0000048Dp00005702\n"
        )
        self.assertEqual(hid_feature.parse_hid_id(uevent), (0x0003, 0x048D, 0x5702))
        self.assertEqual(hid_feature.parse_hid_id("HID_ID=0005:0000046d:0000b33c"), (5, 0x046D, 0xB33C))

    def test_bad_lines_give_none(self):
        for text in (
            "",
            "DRIVER=hid-generic\n",
            "HID_ID=0003:0000048D\n",
            "HID_ID=0003:0000048D:00005702:0001\n",
            "HID_ID=zz03:0000048D:00005702\n",
            "HID_ID=0003:0x00048D:00005702\n",
            "HID_ID=0003::00005702\n",
            "HID_ID=0003:000000048D:00005702\n",
            "XHID_ID=0003:0000048D:00005702\n",
        ):
            with self.subTest(text=text):
                self.assertIsNone(hid_feature.parse_hid_id(text))


class IoctlNumberTests(unittest.TestCase):
    def test_golden_request_numbers(self):
        self.assertEqual(hid_feature.hidiocsfeature(64), 0xC0404806)
        self.assertEqual(hid_feature.hidiocgfeature(64), 0xC0404807)
        self.assertEqual(hid_feature.hidiocsfeature(17), 0xC0114806)
        self.assertEqual(hid_feature.hidiocgfeature(17), 0xC0114807)

    def test_lengths_outside_the_size_field_are_refused(self):
        with self.assertRaises(ValueError):
            hid_feature.hidiocsfeature(1 << 14)
        with self.assertRaises(ValueError):
            hid_feature.hidiocgfeature(-1)


class SysfsScanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = os.path.join(temp.name, "sys", "class", "hidraw")
        os.makedirs(self.root)
        for item in (
            patch.object(hid_feature, "_SYSFS_HIDRAW_ROOT", self.root),
            patch.object(hid_feature, "_DEV_ROOT", "/dev"),
        ):
            item.start()
            self.addCleanup(item.stop)

    def node(self, name, uevent=None, report_descriptor=None):
        device_dir = os.path.join(self.root, name, "device")
        os.makedirs(device_dir)
        if uevent is not None:
            with open(os.path.join(device_dir, "uevent"), "w", encoding="utf-8") as handle:
                handle.write(uevent)
        if report_descriptor is not None:
            with open(os.path.join(device_dir, "report_descriptor"), "wb") as handle:
                handle.write(report_descriptor)

    def test_empty_tree(self):
        self.assertEqual(hid_feature._scan_hidraw(0x048D, 0x5702), [])

    def test_missing_root(self):
        with patch.object(hid_feature, "_SYSFS_HIDRAW_ROOT", os.path.join(self.root, "absent")):
            self.assertEqual(hid_feature._scan_hidraw(0x048D, 0x5702), [])

    def test_matching_node_yields_one_entry_per_collection_with_reports(self):
        keyboard = descriptor("05 01 09 06 a1 01 75 08 95 08 81 02 c0")
        empty = descriptor("06 00 ff 09 01 a1 01 c0")  # A collection without any report.
        self.node("hidraw0", "HID_ID=0003:0000046D:0000C31C\n", keyboard + IT5702_DESCRIPTOR)
        self.node("hidraw10", "HID_ID=0003:0000048D:00005702\n", IT5702_DESCRIPTOR)
        self.node("hidraw2", "HID_ID=0003:0000048D:00005702\n")  # No descriptor.
        self.node("hidraw3")  # No uevent.
        self.node("hidraw4", "HID_ID=0003:0000048D:00005702\n", descriptor("06 89"))  # Unparseable.
        self.node("hidraw5", "HID_ID=0003:0000048D:00005702\n", keyboard + empty)  # Input report only.
        os.makedirs(os.path.join(self.root, "hidraw6"))  # No device directory at all.
        os.makedirs(os.path.join(self.root, "not-hidraw", "device"))
        expected = [
            HidDeviceInfo("/dev/hidraw5", 0x048D, 0x5702, 0x01, 0x06, 0, 8, 0),
            HidDeviceInfo("/dev/hidraw10", 0x048D, 0x5702, 0xFF89, 0x10, 17),
            HidDeviceInfo("/dev/hidraw10", 0x048D, 0x5702, 0xFF89, 0xCC, 64),
        ]
        self.assertEqual(hid_feature._scan_hidraw(0x048D, 0x5702), expected)
        self.assertEqual(hid_feature._scan_hidraw(0x048D, 0x8297), [])
        with patch.object(sys, "platform", "linux"):
            self.assertEqual(hid_feature.enumerate(0x048D, 0x5702), expected)

    def test_a_hidpp_node_lists_its_output_and_input_collections(self):
        mouse = descriptor("05 01 09 02 a1 01 09 01 a1 00 75 08 95 08 81 02 c0 c0")
        self.node("hidraw7", "HID_ID=0003:0000046D:0000C08B\n", mouse)
        self.node("hidraw8", "HID_ID=0003:0000046D:0000C08B\n", HIDPP_DESCRIPTOR)
        self.assertEqual(hid_feature._scan_hidraw(0x046D, 0xC08B), [
            HidDeviceInfo("/dev/hidraw7", 0x046D, 0xC08B, 0x01, 0x02, 0, 8, 0),
            HidDeviceInfo("/dev/hidraw8", 0x046D, 0xC08B, 0xFF00, 0x01, 0, 7, 7),
            HidDeviceInfo("/dev/hidraw8", 0x046D, 0xC08B, 0xFF00, 0x02, 0, 20, 20),
        ])

    def test_nodes_are_listed_in_numeric_order(self):
        for name in ("hidraw11", "hidraw2", "hidraw1"):
            self.node(name, "HID_ID=0003:0000048D:00005702\n", IT5702_DESCRIPTOR)
        paths = [info.path for info in hid_feature._scan_hidraw(0x048D, 0x5702)]
        self.assertEqual(paths, ["/dev/hidraw1"] * 2 + ["/dev/hidraw2"] * 2 + ["/dev/hidraw11"] * 2)


@unittest.skipUnless(sys.platform == "linux", "hidraw system calls exist on Linux only")
class LinuxHidrawTests(unittest.TestCase):
    PATH = "/dev/hidraw7"

    def setUp(self):
        import fcntl

        self.fcntl = fcntl
        patches = {
            "open": patch.object(os, "open", return_value=42),
            "close": patch.object(os, "close"),
            "ioctl": patch.object(fcntl, "ioctl", return_value=64),
        }
        self.mocks = {}
        for name, item in patches.items():
            self.mocks[name] = item.start()
            self.addCleanup(item.stop)

    def test_supported(self):
        self.assertTrue(hid_feature.supported())

    def test_open_send_get_close(self):
        device = hid_feature.HidDevice(self.PATH)
        device.open()
        self.mocks["open"].assert_called_once_with(self.PATH, os.O_RDWR | os.O_CLOEXEC)
        self.assertTrue(device.is_open)
        device.open()  # Already open: no second open.
        self.assertEqual(self.mocks["open"].call_count, 1)

        report = bytes([0xCC, 0x60]) + bytes(62)
        device.send_feature_report(report)
        fd, request, buffer, mutate = self.mocks["ioctl"].call_args.args
        self.assertEqual((fd, request, mutate), (42, 0xC0404806, True))
        self.assertIsInstance(buffer, bytearray)
        self.assertEqual(bytes(buffer), report)

        seen = []

        def fill(fd, request, buffer, mutate):
            seen.append((fd, request, bytes(buffer), mutate))
            buffer[1:4] = b"\x01\x02\x03"
            return len(buffer)

        self.mocks["ioctl"].side_effect = fill
        reply = device.get_feature_report(0xCC, 64)
        self.assertEqual(seen, [(42, 0xC0404807, bytes([0xCC]) + bytes(63), True)])
        self.assertEqual(reply, bytes([0xCC, 1, 2, 3]) + bytes(60))

        device.close()
        device.close()
        self.assertEqual(self.mocks["close"].call_args_list, [call(42)])
        self.assertFalse(device.is_open)

    def test_permission_denied_surfaces_as_permission_error(self):
        self.mocks["open"].side_effect = OSError(errno.EACCES, "Permission denied", self.PATH)
        device = hid_feature.HidDevice(self.PATH)
        with self.assertRaises(PermissionError) as caught:
            device.open()
        self.assertEqual(caught.exception.errno, errno.EACCES)
        self.assertFalse(device.is_open)
        self.mocks["close"].assert_not_called()

    def test_ioctl_failures_surface_as_os_errors(self):
        device = hid_feature.HidDevice(self.PATH)
        device.open()
        self.mocks["ioctl"].side_effect = OSError(errno.EPIPE, "Broken pipe")
        with self.assertRaises(OSError) as caught:
            device.send_feature_report(bytes([0xCC]) + bytes(63))
        self.assertEqual(caught.exception.errno, errno.EPIPE)
        with self.assertRaises(OSError):
            device.get_feature_report(0xCC, 64)

    def test_reports_on_a_closed_device_fail(self):
        device = hid_feature.HidDevice(self.PATH)
        with self.assertRaises(OSError):
            device.send_feature_report(bytes(64))
        self.mocks["ioctl"].assert_not_called()

    def test_stream_writes_output_and_reads_input_reports(self):
        import select

        request = bytes([0x11, 0xFF, 0x00, 0x1B]) + bytes(16)
        reply = bytes([0x11, 0xFF, 0x00, 0x1B, 4, 2]) + bytes(14)
        with patch.object(os, "write", return_value=20) as written, \
                patch.object(select, "select", side_effect=[([42], [], []), ([], [], [])]) as waited, \
                patch.object(os, "read", return_value=reply) as read:
            stream = hid_feature.HidStream(self.PATH)
            stream.open()
            self.mocks["open"].assert_called_once_with(self.PATH, os.O_RDWR | os.O_CLOEXEC)
            stream.write(request)
            written.assert_called_once_with(42, request)
            self.assertEqual(stream.read(64, 0.5), reply)
            read.assert_called_once_with(42, 64)
            self.assertIsNone(stream.read(64, 0.25))  # Nothing within the timeout.
            self.assertEqual([c.args for c in waited.call_args_list], [([42], [], [], 0.5), ([42], [], [], 0.25)])
            self.assertEqual(read.call_count, 1)
            stream.close()
            stream.close()
        self.assertEqual(self.mocks["close"].call_args_list, [call(42)])
        self.mocks["ioctl"].assert_not_called()

    def test_stream_short_write_and_closed_handle_fail(self):
        with patch.object(os, "write", return_value=5):
            stream = hid_feature.HidStream(self.PATH)
            with self.assertRaises(OSError):
                stream.write(bytes(20))  # Not open yet.
            stream.open()
            with self.assertRaises(OSError):
                stream.write(bytes(20))


@unittest.skipUnless(sys.platform == "win32", "overlapped IO exists on Windows only")
class WindowsStreamTests(unittest.TestCase):
    """The overlapped read/write logic, with the kernel32 calls replaced."""

    def setUp(self):
        import ctypes

        self.ctypes = ctypes
        self.api = hid_feature._WindowsHid()
        self.calls = []
        self.api._reset_event = lambda event: self.calls.append(("reset", event)) or True
        self.api._cancel_io = lambda handle: self.calls.append(("cancel", handle)) or True
        self.stream = hid_feature._WinStream(7, 9)

    def pending(self, *, wait_result, result_ok, transferred=0, result_error=0, fill=b""):
        """A ReadFile/WriteFile that goes pending, and how it ends."""
        ctypes = self.ctypes

        def start(handle, buffer, length, _count, _overlapped):
            self.calls.append(("start", handle, length))
            if fill:
                ctypes.memmove(buffer, fill, len(fill))
            ctypes.set_last_error(997)  # ERROR_IO_PENDING
            return False

        def wait(event, milliseconds):
            self.calls.append(("wait", event, milliseconds))
            return wait_result

        def result(handle, _overlapped, done, block):
            self.calls.append(("result", handle, bool(block)))
            done._obj.value = transferred
            if not result_ok:
                ctypes.set_last_error(result_error)
            return result_ok

        self.api._wait = wait
        self.api._overlapped_result = result
        return start

    def test_a_completed_read_returns_the_report(self):
        reply = bytes([0x11, 0xFF, 2, 0x3B]) + bytes(16)
        self.api._read_file = self.pending(wait_result=0, result_ok=True, transferred=20, fill=reply)
        self.assertEqual(self.api.read_report(self.stream, 64, 0.5), reply)
        self.assertEqual(self.calls, [("reset", 9), ("start", 7, 64), ("wait", 9, 500), ("result", 7, True)])

    def test_a_read_timeout_cancels_and_waits_for_the_cancel(self):
        self.api._read_file = self.pending(wait_result=0x102, result_ok=False, result_error=995)
        self.assertIsNone(self.api.read_report(self.stream, 64, 0.01))
        self.assertEqual(self.calls[-3:], [("wait", 9, 10), ("cancel", 7), ("result", 7, True)])

    def test_a_report_that_lands_during_the_cancel_still_counts(self):
        reply = bytes([0x11, 0xFF]) + bytes(18)
        self.api._read_file = self.pending(wait_result=0x102, result_ok=True, transferred=20, fill=reply)
        self.assertEqual(self.api.read_report(self.stream, 64, 0.0), reply)

    def test_a_write_timeout_raises_timeout_error(self):
        self.api._write_file = self.pending(wait_result=0x102, result_ok=False, result_error=995)
        with self.assertRaises(TimeoutError):
            self.api.write_report(self.stream, bytes(20), 0.2)
        self.assertIn(("start", 7, 20), self.calls)

    def test_a_failed_request_raises_os_error_with_the_win32_code(self):
        ctypes = self.ctypes

        def refuse(*_args):
            ctypes.set_last_error(1167)  # ERROR_DEVICE_NOT_CONNECTED
            return False

        self.api._write_file = refuse
        with self.assertRaises(OSError) as caught:
            self.api.write_report(self.stream, bytes(20), 0.5)
        self.assertEqual(caught.exception.winerror, 1167)  # type: ignore[attr-defined]
        self.api._read_file = self.pending(wait_result=0, result_ok=False, result_error=1167)
        with self.assertRaises(OSError):
            self.api.read_report(self.stream, 64, 0.5)


class UnsupportedPlatformTests(unittest.TestCase):
    def test_inert_on_an_unsupported_platform(self):
        with patch.object(sys, "platform", "darwin"):
            self.assertFalse(hid_feature.supported())
            self.assertEqual(hid_feature.enumerate(0x048D, 0x5702), [])
            device = hid_feature.HidDevice("anything")
            with self.assertRaises(hid_feature.HidUnsupportedPlatform) as caught:
                device.open()
            self.assertIsInstance(caught.exception, OSError)
            self.assertIn("Windows and Linux", str(caught.exception))
            with self.assertRaises(hid_feature.HidUnsupportedPlatform):
                device.send_feature_report(bytes(64))
            with self.assertRaises(hid_feature.HidUnsupportedPlatform):
                device.get_feature_report(0xCC, 64)
            device.close()
            stream = hid_feature.HidStream("anything")
            with self.assertRaises(hid_feature.HidUnsupportedPlatform):
                stream.open()
            with self.assertRaises(hid_feature.HidUnsupportedPlatform):
                stream.write(bytes(20))
            with self.assertRaises(hid_feature.HidUnsupportedPlatform):
                stream.read(20, 0.1)
            stream.close()

    def test_supported_platforms(self):
        for platform, expected in (("win32", True), ("linux", True), ("darwin", False), ("freebsd14", False)):
            with self.subTest(platform=platform), patch.object(sys, "platform", platform):
                self.assertEqual(hid_feature.supported(), expected)


if __name__ == "__main__":
    unittest.main()
