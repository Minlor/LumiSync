"""Tests for ASUS laptop keyboard lighting driver, against a simulated device only.

No test here touches real hardware: ``asus_laptop._acpi`` is replaced by
:class:`FakeAcpi`, which records packet transmissions and allows injecting
simulated Win32 and firmware errors.
"""

from __future__ import annotations

import struct
import unittest
from typing import List, Optional
from unittest.mock import patch

from lumisync.drivers import asus_laptop, registry

FAKE_DESCRIPTOR = {
    "device_id": "asus_laptop_keyboard",
    "name": "ASUS TUF Gaming A15 Lighting",
    "model": "ASUS TUF Gaming A15 FA506NCR_FA506NCR",
    "transport": "usb",
    "type": "asus_laptop",
    "device_kind": "keyboard",
    "vendor": "asus",
}


class FakeAcpi:
    """Simulated Win32 ATKACPI provider."""

    def __init__(self) -> None:
        self.packets: List[bytes] = []
        self.events: List[str] = []
        self.is_open = False
        self.open_error: Optional[Exception] = None
        self.send_error: Optional[Exception] = None
        self.query_error: Optional[Exception] = None
        # Default DSTS status: device present
        self.query_status_results: dict[int, int] = {
            asus_laptop.TUF_KB_DEVID: asus_laptop.DSTS_PRESENCE_BIT,
            asus_laptop.TUF_KB_DEVID_FALLBACK: 0,
        }

    def open_device(self) -> int:
        self.events.append("open")
        if self.open_error is not None:
            raise self.open_error
        self.is_open = True
        return 42

    def send_control(self, handle: int, packet: bytes) -> None:
        assert self.is_open, "send_control called on closed handle"
        assert handle == 42
        self.events.append("send")
        if self.send_error is not None:
            raise self.send_error
        self.packets.append(packet)

    def query_status(self, handle: int, dev_id: int) -> int:
        assert self.is_open, "query_status called on closed handle"
        assert handle == 42
        self.events.append(f"query_0x{dev_id:08X}")
        if self.query_error is not None:
            raise self.query_error
        return self.query_status_results.get(dev_id, 0)

    def close_device(self, handle: int) -> None:
        if self.is_open:
            self.events.append("close")
        self.is_open = False


class AsusLaptopProtocolTests(unittest.TestCase):
    """Test packet building, formatting, and strict validation."""

    def test_build_color_packet_volatile_vs_save(self) -> None:
        # Default is live/volatile (save=False -> opcode 0xB3)
        live_packet = asus_laptop.build_color_packet(255, 128, 0, save=False)
        self.assertEqual(len(live_packet), asus_laptop.DEVS_PACKET_LENGTH)
        magic, args_len, devid, opcode, mode, r, g, b, speed = struct.unpack("<IIIBBBBBB", live_packet)
        self.assertEqual(magic, asus_laptop.DEVS_MAGIC)
        self.assertEqual(args_len, asus_laptop.DEVS_PAYLOAD_LENGTH)
        self.assertEqual(devid, asus_laptop.TUF_KB_DEVID)
        self.assertEqual(opcode, asus_laptop.OPCODE_SET)  # 0xB3
        self.assertEqual(mode, 0)
        self.assertEqual(r, 255)
        self.assertEqual(g, 128)
        self.assertEqual(b, 0)
        self.assertEqual(speed, 0)

        # Explicit manual save (save=True -> opcode 0xB4)
        save_packet = asus_laptop.build_color_packet(10, 20, 30, save=True)
        self.assertEqual(save_packet[12], asus_laptop.OPCODE_SAVE)  # 0xB4

    def test_build_dsts_packet_structure(self) -> None:
        dsts_packet = asus_laptop.build_dsts_packet(asus_laptop.TUF_KB_DEVID)
        self.assertEqual(len(dsts_packet), asus_laptop.DSTS_PACKET_LENGTH)
        magic, args_len, devid = struct.unpack("<III", dsts_packet)
        self.assertEqual(magic, asus_laptop.DSTS_MAGIC)
        self.assertEqual(args_len, asus_laptop.DSTS_PAYLOAD_LENGTH)
        self.assertEqual(devid, asus_laptop.TUF_KB_DEVID)

    def test_packet_validator_rejects_invalid_length(self) -> None:
        with self.assertRaises(ValueError) as caught:
            asus_laptop._check_packet(b"\x00" * 15)
        self.assertIn("Invalid ASUS ACPI packet length", str(caught.exception))

    def test_packet_validator_rejects_bad_magic(self) -> None:
        packet = bytearray(asus_laptop.build_color_packet(255, 255, 255))
        packet[0] = 0xFF
        with self.assertRaises(ValueError) as caught:
            asus_laptop._check_packet(bytes(packet))
        self.assertIn("Invalid ASUS ACPI DEVS magic", str(caught.exception))

    def test_packet_validator_rejects_unauthorized_opcode(self) -> None:
        packet = bytearray(asus_laptop.build_color_packet(255, 255, 255))
        packet[12] = 0xAA  # unauthorized opcode
        with self.assertRaises(ValueError) as caught:
            asus_laptop._check_packet(bytes(packet))
        self.assertIn("Disallowed ASUS ACPI opcode", str(caught.exception))

    def test_packet_validator_rejects_unauthorized_device(self) -> None:
        with self.assertRaises(ValueError) as caught:
            asus_laptop.build_color_packet(255, 255, 255, dev_id=0x12345678)
        self.assertIn("Invalid ASUS ACPI target device", str(caught.exception))


class AsusLaptopDiscoveryTests(unittest.TestCase):
    """Test system gating, model verification, and DSTS presence checks."""

    def setUp(self) -> None:
        self.fake_acpi = FakeAcpi()
        self.orig_acpi = asus_laptop._acpi
        asus_laptop._acpi = self.fake_acpi

    def tearDown(self) -> None:
        asus_laptop._acpi = self.orig_acpi

    @patch("lumisync.drivers.asus_laptop._get_system_info")
    def test_non_asus_machine_returns_empty_discovery(self, mock_info) -> None:
        mock_info.return_value = (False, "")
        devs = asus_laptop.discover()
        self.assertEqual(devs, [])
        self.assertFalse(asus_laptop.device_present())
        # ACPI handle must never be touched on non-ASUS machines
        self.assertEqual(self.fake_acpi.events, [])

    @patch("lumisync.drivers.asus_laptop._get_system_info")
    def test_unverified_asus_models_rejected(self, mock_info) -> None:
        # ASUS ZenBook without RGB keyboard
        mock_info.return_value = (True, "ZenBook UX425EA")
        self.assertFalse(asus_laptop.is_verified_model("ZenBook UX425EA"))
        self.assertEqual(asus_laptop.discover(), [])
        self.assertFalse(asus_laptop.device_present())

        # ASUS Desktop
        mock_info.return_value = (True, "ROG Strix G35DX")
        self.assertFalse(asus_laptop.is_verified_model("ROG Strix G35DX"))
        self.assertEqual(asus_laptop.discover(), [])

        # ACPI must not be opened for unverified models
        self.assertEqual(self.fake_acpi.events, [])

    @patch("lumisync.drivers.asus_laptop._get_system_info")
    def test_verified_asus_machine_discovery(self, mock_info) -> None:
        mock_info.return_value = (True, "ASUS TUF Gaming A15 FA506NCR_FA506NCR")
        devs = asus_laptop.discover()
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["type"], "asus_laptop")
        self.assertEqual(devs[0]["vendor"], "asus")
        self.assertEqual(devs[0]["model"], "ASUS TUF Gaming A15 FA506NCR_FA506NCR")
        self.assertTrue(asus_laptop.device_present())

    @patch("lumisync.drivers.asus_laptop._get_system_info")
    def test_verified_asus_machine_dsts_fallback(self, mock_info) -> None:
        mock_info.return_value = (True, "ASUS TUF Gaming A15 FA506NCR")
        # Primary fails, fallback succeeds
        self.fake_acpi.query_status_results[asus_laptop.TUF_KB_DEVID] = 0
        self.fake_acpi.query_status_results[asus_laptop.TUF_KB_DEVID_FALLBACK] = asus_laptop.DSTS_PRESENCE_BIT
        self.assertTrue(asus_laptop.device_present())

    @patch("lumisync.drivers.asus_laptop._get_system_info")
    def test_missing_dsts_presence_rejects_device(self, mock_info) -> None:
        mock_info.return_value = (True, "ASUS TUF Gaming A15 FA506NCR")
        # Neither primary nor fallback reports presence
        self.fake_acpi.query_status_results[asus_laptop.TUF_KB_DEVID] = 0
        self.fake_acpi.query_status_results[asus_laptop.TUF_KB_DEVID_FALLBACK] = 0
        self.assertFalse(asus_laptop.device_present())
        self.assertEqual(asus_laptop.discover(), [])

    @patch("lumisync.drivers.asus_laptop._get_system_info")
    def test_asus_discovery_handles_atkacpi_failure(self, mock_info) -> None:
        mock_info.return_value = (True, "ASUS TUF Gaming A15 FA506NCR")
        self.fake_acpi.open_error = OSError("ATKACPI not found")
        devs = asus_laptop.discover()
        self.assertEqual(devs, [])
        self.assertFalse(asus_laptop.device_present())


class AsusLaptopAdapterTests(unittest.TestCase):
    """Test runtime control, volatile streaming safety, and state tracking."""

    def setUp(self) -> None:
        self.fake_acpi = FakeAcpi()
        self.orig_acpi = asus_laptop._acpi
        asus_laptop._acpi = self.fake_acpi
        # Reset controller state
        ctrl = asus_laptop._get_shared_controller()
        ctrl.users.clear()
        ctrl.handle = None
        ctrl.power_on = None
        ctrl.brightness = 100
        ctrl.color = (255, 255, 255)
        ctrl.manual_known = False
        ctrl.last_stream_color = None

    def tearDown(self) -> None:
        asus_laptop._acpi = self.orig_acpi

    def test_manual_set_color_sends_0xb4_save(self) -> None:
        adapter = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        ctrl = asus_laptop._get_shared_controller()
        self.assertFalse(ctrl.manual_known)

        adapter.set_color(0, 255, 128)
        self.assertTrue(ctrl.manual_known)
        self.assertEqual(len(self.fake_acpi.packets), 1)

        packet = self.fake_acpi.packets[0]
        self.assertEqual(packet[12], asus_laptop.OPCODE_SAVE)  # Opcode 0xB4 on manual set
        self.assertEqual(packet[14], 0)    # R
        self.assertEqual(packet[15], 255)  # G
        self.assertEqual(packet[16], 128)  # B
        adapter.close()

    def test_streaming_strictly_emits_0xb3_never_0xb4(self) -> None:
        adapter = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        adapter.begin_stream()

        # Send multiple distinct frames
        adapter.set_segments([(255, 0, 0)])
        adapter.set_segments([(0, 255, 0)])
        adapter.set_segments([(0, 0, 255)])
        adapter.set_segments([(100, 100, 100)])

        self.assertTrue(len(self.fake_acpi.packets) >= 4)
        for i, pkt in enumerate(self.fake_acpi.packets):
            opcode = pkt[12]
            self.assertEqual(
                opcode,
                asus_laptop.OPCODE_SET,  # Must be 0xB3 (volatile RAM/EC)
                f"Packet {i} emitted opcode 0x{opcode:02X} instead of 0xB3 during streaming!",
            )
            self.assertNotEqual(opcode, asus_laptop.OPCODE_SAVE)

        adapter.end_stream()
        adapter.close()

    def test_stream_thresholding_drops_minor_changes(self) -> None:
        adapter = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        adapter.begin_stream()

        # First frame goes through
        adapter.set_segments([(100, 100, 100)])
        self.assertEqual(len(self.fake_acpi.packets), 1)

        # Identical frame is dropped
        adapter.set_segments([(100, 100, 100)])
        self.assertEqual(len(self.fake_acpi.packets), 1)

        # Frame differing by less than threshold (1) is dropped
        adapter.set_segments([(101, 100, 100)])
        self.assertEqual(len(self.fake_acpi.packets), 1)

        # Frame differing by >= threshold goes through
        adapter.set_segments([(110, 100, 100)])
        self.assertEqual(len(self.fake_acpi.packets), 2)

        adapter.end_stream()
        adapter.close()

    def test_power_off_sends_0xb3_black_never_0xb4(self) -> None:
        adapter = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        adapter.set_power(False)

        self.assertEqual(len(self.fake_acpi.packets), 1)
        packet = self.fake_acpi.packets[0]
        # Must be 0xB3 volatile black so off does not persist across reboots
        self.assertEqual(packet[12], asus_laptop.OPCODE_SET)  # 0xB3
        self.assertEqual(packet[14], 0)
        self.assertEqual(packet[15], 0)
        self.assertEqual(packet[16], 0)
        adapter.close()

    def test_end_stream_skips_restore_when_manual_color_unknown(self) -> None:
        adapter = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        ctrl = asus_laptop._get_shared_controller()
        self.assertFalse(ctrl.manual_known)

        adapter.begin_stream()
        adapter.set_segments([(200, 100, 50)])
        packet_count_after_sync = len(self.fake_acpi.packets)
        self.assertEqual(packet_count_after_sync, 1)

        # When stream ends with no prior manual color, NO restore packet should be sent
        adapter.end_stream()
        self.assertEqual(len(self.fake_acpi.packets), packet_count_after_sync)
        adapter.close()

    def test_end_stream_restores_color_when_manual_color_known(self) -> None:
        adapter = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        ctrl = asus_laptop._get_shared_controller()

        # Set manual color first
        adapter.set_color(50, 100, 150)
        self.assertTrue(ctrl.manual_known)
        self.assertEqual(len(self.fake_acpi.packets), 1)

        # Stream a different color
        adapter.begin_stream()
        adapter.set_segments([(255, 0, 0)])
        self.assertEqual(len(self.fake_acpi.packets), 2)

        # Ending stream restores the manual color using volatile 0xB3
        adapter.end_stream()
        self.assertEqual(len(self.fake_acpi.packets), 3)
        restore_pkt = self.fake_acpi.packets[2]
        self.assertEqual(restore_pkt[12], asus_laptop.OPCODE_SET)  # 0xB3
        self.assertEqual(restore_pkt[14], 50)
        self.assertEqual(restore_pkt[15], 100)
        self.assertEqual(restore_pkt[16], 150)
        adapter.close()

    def test_handle_cleanup_on_last_adapter_close(self) -> None:
        adapter1 = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        adapter2 = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        adapter1.set_color(255, 255, 255)
        self.assertTrue(self.fake_acpi.is_open)

        adapter1.close()
        self.assertTrue(self.fake_acpi.is_open)  # adapter2 still open

        adapter2.close()
        self.assertFalse(self.fake_acpi.is_open)  # closed on last user

    def test_io_failure_propagates_exception(self) -> None:
        adapter = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        self.fake_acpi.send_error = OSError("Hardware communication failed")
        with self.assertRaises(OSError):
            adapter.set_color(255, 0, 0)
        adapter.close()

    def test_firmware_status_rejection_propagates_exception(self) -> None:
        adapter = asus_laptop.AsusLaptopAdapter(FAKE_DESCRIPTOR)
        self.fake_acpi.send_error = OSError("ASUS ACPI call rejected by firmware (status 0x00000000)")
        with self.assertRaises(OSError) as caught:
            adapter.set_color(255, 0, 0)
        self.assertIn("rejected by firmware", str(caught.exception))
        adapter.close()


class AsusLaptopWin32AcpiTests(unittest.TestCase):
    """Test low-level Win32 ACPI implementation details."""

    def test_win32_acpi_error_handling(self) -> None:
        from unittest.mock import MagicMock
        win32 = asus_laptop._Win32Acpi()
        mock_k32 = MagicMock()
        win32._k32 = mock_k32

        # Test open failure
        mock_k32.CreateFileW.return_value = 0xFFFFFFFFFFFFFFFF  # INVALID_HANDLE_VALUE
        with self.assertRaises(OSError) as caught:
            win32.open_device()
        self.assertIn("Could not open", str(caught.exception))

        # Test DeviceIoControl failure
        mock_k32.DeviceIoControl.side_effect = None
        mock_k32.DeviceIoControl.return_value = 0
        with self.assertRaises(OSError) as caught:
            win32.send_control(123, asus_laptop.build_color_packet(255, 255, 255))
        self.assertIn("DeviceIoControl to \\\\.\\ATKACPI failed", str(caught.exception))

        # Test firmware rejection (status != 1)
        def fake_ioctl_reject(handle, code, in_buf, in_len, out_buf, out_len, bytes_ret, ov):
            bytes_ret._obj.value = 16
            out_buf.raw = struct.pack("<I", 0) + b"\x00" * 12
            return 1

        mock_k32.DeviceIoControl.side_effect = fake_ioctl_reject
        with self.assertRaises(OSError) as caught:
            win32.send_control(123, asus_laptop.build_color_packet(255, 255, 255))
        self.assertIn("rejected by firmware", str(caught.exception))

        # Test DSTS query status return
        def fake_ioctl_dsts(handle, code, in_buf, in_len, out_buf, out_len, bytes_ret, ov):
            bytes_ret._obj.value = 16
            out_buf.raw = struct.pack("<I", asus_laptop.DSTS_PRESENCE_BIT) + b"\x00" * 12
            return 1

        mock_k32.DeviceIoControl.side_effect = fake_ioctl_dsts
        status = win32.query_status(123, asus_laptop.TUF_KB_DEVID)
        self.assertEqual(status, asus_laptop.DSTS_PRESENCE_BIT)


class AsusLaptopRegistryTests(unittest.TestCase):
    """Test registry and driver wiring."""

    def test_registry_integration(self) -> None:
        self.assertTrue(registry.is_supported_device(FAKE_DESCRIPTOR))
        adapter = registry.create_adapter(FAKE_DESCRIPTOR)
        self.assertIsInstance(adapter, asus_laptop.AsusLaptopAdapter)
        caps = registry.capabilities_for_device(FAKE_DESCRIPTOR)
        self.assertTrue(caps.supports_streaming)
        self.assertEqual(caps.segment_count, 1)
        self.assertEqual(caps.max_update_hz, 30.0)
        adapter.close()


if __name__ == "__main__":
    unittest.main()
