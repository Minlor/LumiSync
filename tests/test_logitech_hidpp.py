"""Logitech HID++ mouse lighting, against a simulated mouse only.

No test here talks to a real mouse: ``logitech_hidpp._hid`` is replaced by
:class:`FakeMouse`, which answers each request the way the G502 HERO did
when it was measured (feature indexes, zone count, reply layout), and can be
told to stay silent, answer "busy", send unrelated reports or fail.
"""

import os
import sys
import unittest
from collections import deque
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lumisync import devices
from lumisync.device import Device
from lumisync.drivers import gigabyte_fusion as fusion
from lumisync.drivers import logitech_hidpp as hidpp
from lumisync.drivers import pool, registry
from lumisync.drivers.hid_feature import HidDeviceInfo

MOUSE = {
    "device_id": "hidpp-046d-c08b",
    "name": "G502 HERO",
    "model": "Logitech G502 HERO",
    "transport": "usb",
    "type": "logitech_hidpp",
    "device_kind": "mouse",
    "usb_vendor_id": 0x046D,
    "usb_product_id": 0xC08B,
}
# Colour correction off: the byte-level tests below check the protocol with
# the exact colours asked for; CorrectionTests covers the corrected bytes.
RAW = {**MOUSE, "color_correction": "none"}
BOARD = {
    "device_id": "rgbfusion2-048d-5702",
    "name": "B550 AORUS ELITE lighting",
    "model": "RGB Fusion 2",
    "transport": "usb",
    "type": "rgb_fusion2",
    "device_kind": "motherboard",
}

PATH = "fake\\vid_046d&pid_c08b&mi_01&col02"
COLLECTIONS = [
    HidDeviceInfo("fake\\vid_046d&pid_c08b&mi_00", 0x046D, 0xC08B, 0x0001, 0x0002, 0, 9, 0),
    HidDeviceInfo("fake\\vid_046d&pid_c08b&mi_01&col01", 0x046D, 0xC08B, 0xFF00, 0x0001, 0, 7, 7),
    HidDeviceInfo(PATH, 0x046D, 0xC08B, 0xFF00, 0x0002, 0, 20, 20),
]


def long_report(hex_bytes: str) -> bytes:
    data = bytes.fromhex(hex_bytes)
    return data + bytes(20 - len(data))


class FakeStream:
    def __init__(self, mouse, path):
        self.mouse = mouse
        self.path = path
        self.is_open = False

    def open(self):
        self.mouse.events.append("open")
        if self.mouse.open_error is not None:
            raise self.mouse.open_error
        self.is_open = True

    def write(self, data, timeout=1.0):
        assert self.is_open, "report written on a closed handle"
        self.mouse.receive(bytes(data))

    def read(self, size, timeout):
        assert self.is_open, "report read on a closed handle"
        self.mouse.read_timeouts.append(timeout)
        if self.mouse.queue:
            return self.mouse.queue.popleft()
        return None

    def close(self):
        if self.is_open:
            self.mouse.events.append("close")
        self.is_open = False


# Each zone's effect list as the G502 HERO reports it (getZoneEffectInfo):
# (effect id, period field) at indexes 0-3: off, fixed, breathing, colour cycle.
G502_EFFECTS = ((0x0000, 0), (0x0001, 0), (0x000A, 992), (0x0003, 992))


class FakeMouse:
    """A G502 HERO as measured: HID++ 4.2, 0x8070 at index 2, two zones
    (zone 0 at location 0x0001, the DPI lights; zone 1 at 0x0002, the logo),
    each listing :data:`G502_EFFECTS`. setZoneEffect takes the zone's effect
    index (an index past the list is error 0x02, as on the mouse)."""

    def __init__(self, led_index=2, zones=2, protocol=(4, 2), onboard_index=12, onboard_mode=2,
                 locations=(0x0001, 0x0002), effects=G502_EFFECTS):
        self.infos = list(COLLECTIONS)
        self.features = {0x0000: 0, 0x8100: onboard_index}
        if led_index:
            self.features[0x8070] = led_index
        self.zones = zones
        self.locations = tuple(locations)
        self.effects = tuple(effects)
        # Every setZoneEffect as (zone, the 12 bytes after the zone).
        self.set_params = []
        self.zone_effects = {}
        self.protocol = protocol
        self.onboard_mode = onboard_mode
        self.writes = []
        self.events = []
        self.queue = deque()
        self.read_timeouts = []
        self.zone_colours = {}
        self.enumerations = 0
        self.enumerate_error = None
        self.open_error = None
        self.write_error = None
        # One entry per upcoming request: None answers normally, "silent"
        # drops the reply, an int is a HID++ error code, a list of reports is
        # sent ahead of the normal reply.
        self.script = []

    # The hid_feature surface the driver uses.
    def enumerate(self, vendor_id, product_id):
        self.enumerations += 1
        if self.enumerate_error is not None:
            raise self.enumerate_error
        return [info for info in self.infos if (info.vendor_id, info.product_id) == (vendor_id, product_id)]

    def HidStream(self, path):  # noqa: N802 - mirrors hid_feature.HidStream
        self.events.append(("stream", path))
        return FakeStream(self, path)

    # The mouse.
    def receive(self, packet):
        if self.write_error is not None:
            raise self.write_error
        assert len(packet) == 20 and packet[0] == 0x11, packet.hex()
        self.writes.append(packet)
        action = self.script.pop(0) if self.script else None
        if action == "silent":
            return
        if isinstance(action, list):
            self.queue.extend(action)
            action = None
        if action is None:
            action = self.answer(packet[2], packet[3] >> 4, packet[4:])
        if isinstance(action, int):
            self.queue.append(long_report(f"11 ff ff {packet[2]:02x} {packet[3]:02x} {action:02x}"))
            return
        self.queue.append(packet[:4] + action.ljust(16, b"\0"))

    def answer(self, index, function, params):
        feature = {value: key for key, value in self.features.items()}[index]
        if feature == 0x0000 and function == 0:
            wanted = int.from_bytes(params[:2], "big")
            return bytes([self.features.get(wanted, 0), 0, 0])
        if feature == 0x0000 and function == 1:
            return bytes([self.protocol[0], self.protocol[1], params[2]])
        if feature == 0x8070 and function == 0:
            return bytes([self.zones])
        if feature == 0x8070 and function == 1:
            zone = params[0]
            location = self.locations[zone] if zone < len(self.locations) else 0
            return bytes([zone, *location.to_bytes(2, "big"), len(self.effects), 0])
        if feature == 0x8070 and function == 2:
            zone, index = params[0], params[1]
            if zone >= self.zones or index >= len(self.effects):
                return 0x02
            effect_id, period = self.effects[index]
            return bytes([zone, index, *effect_id.to_bytes(2, "big"), 0, 0, *period.to_bytes(2, "big")])
        if feature == 0x8070 and function == 3:
            zone, index = params[0], params[1]
            if zone >= self.zones or index >= len(self.effects):
                return 0x02
            self.set_params.append((zone, bytes(params[1:13])))
            self.zone_effects[zone] = bytes(params[1:13])
            if self.effects[index][0] == 0x0001:
                self.zone_colours[zone] = tuple(params[2:5])
            return b""
        if feature == 0x8070 and function == 0xE:
            return bytes([params[0], *self.zone_effects.get(params[0], bytes(12))])
        if feature == 0x8100 and function == 2:
            return bytes([self.onboard_mode])
        raise AssertionError(f"unexpected request: feature 0x{feature:04X} function {function}")

    def sets(self):
        """The setZoneEffect packets written so far."""
        return [w for w in self.writes if w[3] >> 4 == 3]


def zone_reads(zone, led_index=2, effects=4):
    """getZoneInfo(zone) then getZoneEffectInfo(zone, i) for each listed effect (all read only)."""
    return [long_report(f"11 ff {led_index:02x} 1b {zone:02x}")] + [
        long_report(f"11 ff {led_index:02x} 2b {zone:02x} {index:02x}") for index in range(effects)
    ]


OPEN_SEQUENCE = [
    long_report("11 ff 00 1b 00 00 5a"),   # ping
    long_report("11 ff 00 0b 80 70"),      # getFeature(0x8070)
    long_report("11 ff 02 0b"),            # 0x8070 getInfo
    *zone_reads(0),                        # 0x8070 getZoneInfo(0) and the zone's 4 effects
    *zone_reads(1),                        # the same for zone 1
    long_report("11 ff 00 0b 81 00"),      # getFeature(0x8100)
    long_report("11 ff 0c 2b"),            # 0x8100 getMode (read only)
]

# The G502 HERO's effect indexes, per zone, as the driver reads them.
G502_INDEXES = ({0x0000: 0, 0x0001: 1, 0x000A: 2, 0x0003: 3},) * 2


def set_zone(zone, r, g, b, index=2):
    return long_report(f"11 ff {index:02x} 3b {zone:02x} 01 {r:02x} {g:02x} {b:02x} 02")


def set_breathing(zone, r, g, b, period, index=2, effect_index=2):
    return long_report(
        f"11 ff {index:02x} 3b {zone:02x} {effect_index:02x} {r:02x} {g:02x} {b:02x} "
        f"{period >> 8:02x} {period & 0xFF:02x} 00 64"
    )


def set_cycle(zone, period, index=2, effect_index=3):
    return long_report(
        f"11 ff {index:02x} 3b {zone:02x} {effect_index:02x} 00 00 00 00 00 {period >> 8:02x} {period & 0xFF:02x} 64"
    )


class HidppTestCase(unittest.TestCase):
    def setUp(self):
        self.mouse = FakeMouse()
        self.clock = [1000.0]
        for item in (
            patch.object(hidpp, "_hid", self.mouse),
            patch.object(hidpp, "_monotonic", lambda: self.clock[0]),
            patch.dict(hidpp._controllers, clear=True),
        ):
            item.start()
            self.addCleanup(item.stop)

    def adapter(self, device=None):
        adapter = hidpp.LogitechHidppAdapter(dict(RAW if device is None else device))
        self.addCleanup(adapter.close)
        return adapter


class PacketTests(unittest.TestCase):
    def test_set_zone_colour_bytes(self):
        request = hidpp.build_set_zone_color(2, 1, (255, 0, 255), G502_INDEXES)
        self.assertEqual(request.packet, set_zone(1, 255, 0, 255))
        self.assertEqual(request.packet[16], 0)
        self.assertEqual(request.feature_id, 0x8070)

    def test_breathing_and_cycle_bytes_match_the_verified_packets(self):
        # Blue breathing at 3 s on the logo, as confirmed by eye.
        breathing = hidpp.build_set_zone_breathing(2, 1, (0x00, 0x50, 0xFF), 3000, G502_INDEXES)
        self.assertEqual(breathing.packet[4:17], bytes.fromhex("01 02 00 50 ff 0b b8 00 64 00 00 00 00"))
        self.assertEqual(breathing.packet, set_breathing(1, 0x00, 0x50, 0xFF, 3000))
        # A 6 s colour cycle on the DPI lights, as confirmed by eye.
        cycle = hidpp.build_set_zone_colour_cycle(2, 0, 6000, G502_INDEXES)
        self.assertEqual(cycle.packet[4:17], bytes.fromhex("00 03 00 00 00 00 00 17 70 64 00 00 00"))
        self.assertEqual(cycle.packet[17:], bytes(3))

    def test_the_effect_index_comes_from_the_zone_list_not_the_id(self):
        # A mouse listing the effects in another order: the index follows the list.
        other = ({0x000A: 0, 0x0003: 1, 0x0001: 2},)
        self.assertEqual(hidpp.build_set_zone_color(2, 0, (1, 2, 3), other).packet[5], 2)
        self.assertEqual(hidpp.build_set_zone_breathing(2, 0, (1, 2, 3), 3000, other).packet[5], 0)
        self.assertEqual(hidpp.build_set_zone_colour_cycle(2, 0, 3000, other).packet[5], 1)
        # An effect the zone does not list, or an unknown zone, is refused.
        fixed_only = ({0x0001: 1},)
        for build in (lambda: hidpp.build_set_zone_breathing(2, 0, (1, 2, 3), 3000, fixed_only),
                      lambda: hidpp.build_set_zone_colour_cycle(2, 0, 3000, fixed_only),
                      lambda: hidpp.build_set_zone_color(2, 1, (1, 2, 3), fixed_only)):
            with self.assertRaises(ValueError):
                build()

    def test_requests_carry_the_software_id_and_device_index(self):
        for request in (hidpp.build_ping(), hidpp.build_get_feature(0x8070), hidpp.build_get_led_info(5),
                        hidpp.build_get_zone_effect(5, 1), hidpp.build_get_onboard_mode(12),
                        hidpp.build_get_zone_effect_info(5, 1, 3)):
            self.assertEqual(len(request.packet), 20)
            self.assertEqual(request.packet[:2], b"\x11\xff")
            self.assertEqual(request.packet[3] & 0x0F, 0x0B)
        self.assertEqual(hidpp.build_get_zone_effect(2, 1).packet, long_report("11 ff 02 eb 01"))
        self.assertEqual(hidpp.build_get_zone_effect_info(2, 1, 3).packet, long_report("11 ff 02 2b 01 03"))

    def test_only_allowed_functions_are_built(self):
        valid_set = hidpp.build_set_zone_color(3, 0, (1, 2, 3), G502_INDEXES).packet[4:]
        for feature, functions in hidpp.ALLOWED_FUNCTIONS.items():
            for function in range(16):
                with self.subTest(feature=feature, function=function):
                    index = 0 if feature == 0 else 3
                    if function in functions:
                        if feature == 0x8070 and function == 3:
                            hidpp.build_request(feature, index, function, valid_set, G502_INDEXES)
                        else:
                            hidpp.build_request(feature, index, function)
                    else:
                        with self.assertRaises(ValueError):
                            hidpp.build_request(feature, index, function)

    def test_never_send_features_are_refused(self):
        self.assertTrue(hidpp.NEVER_SEND_FEATURES)
        for first, last, reason in hidpp.NEVER_SEND_FEATURES:
            self.assertTrue(reason)
            for feature in range(first, last + 1):
                allowed = hidpp.ALLOWED_FUNCTIONS.get(feature, frozenset())
                self.assertLessEqual(allowed, {2})  # Onboard profiles: getMode only.
                for function in range(16):
                    if function in allowed:
                        continue
                    with self.subTest(feature=feature, function=function), self.assertRaises(ValueError):
                        hidpp.build_request(feature, 5, function, b"\x01")

    def test_the_effect_command_is_checked_whole(self):
        def params(text):
            return bytes.fromhex(text)

        allowed = {
            "fixed": "00 01 01 02 03 02",
            "breathing": "01 02 00 50 ff 0b b8 00 64",
            "breathing slowest": "01 02 00 50 ff 4e 20 00 64",
            "breathing fastest": "01 02 00 50 ff 03 e8 00 64",
            "cycle": "00 03 00 00 00 00 00 17 70 64",
        }
        for name, text in allowed.items():
            with self.subTest(allowed=name):
                hidpp.build_request(0x8070, 2, 3, params(text), G502_INDEXES)
        refused = {
            "persist byte": "00 01 01 02 03 02 00 00 00 00 00 00 01",
            "trailing junk": "00 01 01 02 03 02 00 00 00 00 00 00 00 05",
            "last byte": "00 01 01 02 03 02 00 00 00 00 00 00 00 00 00 01",
            "off (never sent)": "00 00",
            "unknown index": "00 05 01 02 03 02",
            "id 0x0A as index": "01 0a 00 50 ff 0b b8 00 64",
            "unknown zone": "02 01 01 02 03 02",
            "fixed without its tail": "00 01 01 02 03 00",
            "fixed with extra bytes": "00 01 01 02 03 02 07",
            "breathing period too short": "01 02 00 50 ff 03 e7 00 64",
            "breathing period too long": "01 02 00 50 ff 4e 21 00 64",
            "breathing waveform": "01 02 00 50 ff 0b b8 01 64",
            "breathing intensity": "01 02 00 50 ff 0b b8 00 32",
            "breathing extra bytes": "01 02 00 50 ff 0b b8 00 64 01",
            "cycle with a colour": "00 03 ff 00 00 00 00 17 70 64",
            "cycle period too short": "00 03 00 00 00 00 00 00 10 64",
            "cycle period too long": "00 03 00 00 00 00 00 4e 21 64",
            "cycle intensity": "00 03 00 00 00 00 00 17 70 63",
            "cycle extra bytes": "00 03 00 00 00 00 00 17 70 64 00 09",
        }
        for name, text in refused.items():
            with self.subTest(refused=name), self.assertRaises(ValueError):
                hidpp.build_request(0x8070, 2, 3, params(text), G502_INDEXES)
        # Without the zone effect list nothing can be set.
        with self.assertRaises(ValueError):
            hidpp.build_request(0x8070, 2, 3, params(allowed["fixed"]))
        # An index the zone lists for an id outside the allow-list is refused.
        with self.assertRaises(ValueError):
            hidpp.build_request(0x8070, 2, 3, params("00 04 01 02 03 02"), ({0x0001: 1, 0x0004: 4},))
        with self.assertRaises(ValueError):
            hidpp.build_set_zone_breathing(2, 0, (1, 2, 3), 999, G502_INDEXES)
        with self.assertRaises(ValueError):
            hidpp.build_set_zone_colour_cycle(2, 0, 20001, G502_INDEXES)

    def test_root_requests_must_use_index_zero_and_params_must_fit(self):
        with self.assertRaises(ValueError):
            hidpp.build_request(0x0000, 3, 0)
        with self.assertRaises(ValueError):
            hidpp.build_request(0x8070, 2, 0, bytes(17))

    def test_the_send_point_checks_again(self):
        controller = hidpp._Controller(0x046D, 0xC08B)
        written = []

        class Recorder:
            def write(self, data, timeout):
                written.append(data)

        controller.device = Recorder()
        controller.feature_ids = {0: 0x0000, 2: 0x8070}
        controller.effect_indexes = G502_INDEXES
        # A forged request that bypassed the builder: DPI, persist, wrong index.
        forged = [
            hidpp.Request(0x2201, long_report("11 ff 02 3b 00 01")),
            hidpp.Request(0x8070, long_report("11 ff 02 3b 00 01 ff 00 00 02 00 00 00 00 00 00 01")),
            hidpp.Request(0x8070, long_report("11 ff 02 3b 00 05 ff 00 00 02")),
            hidpp.Request(0x8070, long_report("11 ff 07 3b 00 01 ff 00 00 02")),  # unresolved index
            hidpp.Request(0x8100, long_report("11 ff 0c 3b")),  # onboard index never resolved
            hidpp.Request(0x8070, long_report("11 01 02 3b 00 01")),  # other device index
            hidpp.Request(0x8070, long_report("11 ff 02 3c 00 01")),  # other software id
            hidpp.Request(0x8070, long_report("11 ff 02 3b 01 0a 00 50 ff 0b b8 00 64")),  # id as index
            hidpp.Request(0x8070, long_report("11 ff 02 3b 01 02 00 50 ff 0b b8 01 64")),  # waveform
            hidpp.Request(0x8070, long_report("11 ff 02 3b 00 03 00 00 00 00 00 17 70 64 00 00 00 00 01")),
        ]
        for request in forged:
            with self.subTest(packet=request.packet.hex()), self.assertRaises(ValueError):
                hidpp._send(controller, request)
        self.assertEqual(written, [])
        hidpp._send(controller, hidpp.build_set_zone_color(2, 0, (1, 2, 3), G502_INDEXES))
        self.assertEqual(written, [set_zone(0, 1, 2, 3)])
        # The send point checks against the controller's own list: a request
        # built for another list is refused there.
        controller.effect_indexes = ({0x0001: 1},) * 2
        with self.assertRaises(ValueError):
            hidpp._send(controller, hidpp.build_set_zone_breathing(2, 0, (1, 2, 3), 3000, G502_INDEXES))
        self.assertEqual(len(written), 1)


class DiscoveryTests(HidppTestCase):
    def test_descriptor_from_the_table_without_any_io(self):
        self.assertEqual(hidpp.discover(), [MOUSE])
        self.assertEqual(self.mouse.events, [])
        self.assertEqual(self.mouse.writes, [])
        self.assertTrue(hidpp.device_present(MOUSE))
        self.assertEqual(self.mouse.events, [])

    def test_only_the_long_hidpp_collection_counts(self):
        self.mouse.infos = [info for info in COLLECTIONS if info.usage != 2 or info.usage_page != 0xFF00]
        self.assertEqual(hidpp.discover(), [])
        self.assertFalse(hidpp.device_present(MOUSE))

    def test_discovery_never_raises(self):
        self.mouse.enumerate_error = OSError(5, "boom")
        self.assertEqual(hidpp.discover(), [])
        self.assertFalse(hidpp.device_present(MOUSE))

    def test_unsupported_ids_are_not_present(self):
        self.assertFalse(hidpp.device_present({**MOUSE, "usb_product_id": 0xC332}))
        self.assertEqual(self.mouse.enumerations, 0)

    def test_local_hardware_discovery_lists_the_mouse(self):
        with patch.object(fusion, "discover", return_value=[dict(BOARD)]):
            found = devices.discover_local_hardware()
        self.assertEqual([d["device_id"] for d in found], ["rgbfusion2-048d-5702", "hidpp-046d-c08b"])
        self.assertEqual(self.mouse.writes, [])


class SuiteIsolationTests(unittest.TestCase):
    def test_the_suite_never_lists_or_opens_the_real_mouse_by_default(self):
        # tests/__init__.py swaps in an empty HID layer for this driver.
        self.assertIsNot(hidpp._hid, hidpp.hid_feature)
        self.assertEqual(hidpp.discover(), [])
        self.assertFalse(hidpp.device_present(MOUSE))
        with self.assertRaises(AssertionError):
            hidpp._hid.HidStream(PATH)


class OpenTests(HidppTestCase):
    def test_open_sequence_then_one_packet_per_zone(self):
        self.adapter().set_color(255, 0, 255)
        self.assertEqual(self.mouse.events, [("stream", PATH), "open"])
        self.assertEqual(self.mouse.writes, OPEN_SEQUENCE + [set_zone(0, 255, 0, 255), set_zone(1, 255, 0, 255)])
        self.assertEqual(self.mouse.zone_colours, {0: (255, 0, 255), 1: (255, 0, 255)})
        controller = hidpp._controllers[(0x046D, 0xC08B)]
        self.assertEqual((controller.protocol, controller.led_index, controller.zone_count, controller.onboard_mode),
                         ((4, 2), 2, 2, 2))

    def test_the_feature_index_is_looked_up_not_assumed(self):
        self.mouse = FakeMouse(led_index=7, zones=3)
        with patch.object(hidpp, "_hid", self.mouse):
            self.adapter().set_color(1, 2, 3)
        self.assertEqual(self.mouse.writes[2], long_report("11 ff 07 0b"))
        self.assertEqual(self.mouse.sets(), [set_zone(z, 1, 2, 3, index=7) for z in range(3)])

    def test_no_lighting_feature_is_a_clear_error(self):
        self.mouse = FakeMouse(led_index=0)
        with patch.object(hidpp, "_hid", self.mouse):
            with self.assertRaises(RuntimeError) as caught:
                self.adapter().set_color(1, 2, 3)
        self.assertEqual(str(caught.exception), hidpp.NO_LIGHTING_MESSAGE)
        self.assertIn("no supported lighting feature", str(caught.exception))
        self.assertEqual(self.mouse.events[-1], "close")
        self.assertEqual(self.mouse.sets(), [])

    def test_an_old_protocol_is_refused(self):
        self.mouse.protocol = (1, 0)
        with self.assertRaises(RuntimeError) as caught:
            self.adapter().set_color(1, 2, 3)
        self.assertIn("HID++ 2.0", str(caught.exception))
        self.assertEqual(self.mouse.writes, OPEN_SEQUENCE[:1])

    def test_missing_onboard_profiles_is_fine(self):
        del self.mouse.features[0x8100]
        self.adapter().set_color(1, 2, 3)
        self.assertEqual(len(self.mouse.sets()), 2)
        self.assertIsNone(hidpp._controllers[(0x046D, 0xC08B)].onboard_mode)

    def test_absent_mouse_and_open_failures(self):
        self.mouse.infos = []
        with self.assertRaises(RuntimeError) as caught:
            self.adapter().set_color(1, 2, 3)
        self.assertEqual(str(caught.exception), hidpp.NOT_FOUND_MESSAGE)
        self.mouse.infos = list(COLLECTIONS)
        self.mouse.open_error = OSError(32, "sharing violation")
        with self.assertRaises(RuntimeError) as caught:
            self.adapter().set_color(1, 2, 3)
        self.assertEqual(str(caught.exception), hidpp.OPEN_FAILED_MESSAGE)

    def test_linux_permission_error_explains_the_udev_rule(self):
        self.mouse.infos = [HidDeviceInfo("/dev/hidraw4", 0x046D, 0xC08B, 0xFF00, 2, 0, 20, 20)]
        self.mouse.open_error = PermissionError(13, "Permission denied", "/dev/hidraw4")
        with patch.object(sys, "platform", "linux"):
            with self.assertRaises(RuntimeError) as caught:
                self.adapter().set_color(1, 2, 3)
        message = str(caught.exception)
        self.assertEqual(message, hidpp.permission_denied_message("/dev/hidraw4"))
        self.assertIn(hidpp.UDEV_RULE, message)
        self.assertIn("/etc/udev/rules.d/60-lumisync-pc-lighting.rules", message)
        with patch.object(sys, "platform", "win32"):
            with self.assertRaises(RuntimeError) as caught:
                self.adapter().set_color(1, 2, 3)
        self.assertEqual(str(caught.exception), hidpp.OPEN_FAILED_MESSAGE)


class CommandTests(HidppTestCase):
    def test_brightness_scales_the_colour(self):
        adapter = self.adapter()
        adapter.set_color(200, 100, 0)
        adapter.set_brightness(50)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 100, 50, 0), set_zone(1, 100, 50, 0)])

    def test_power_off_is_fixed_black_and_on_restores(self):
        adapter = self.adapter()
        adapter.set_color(10, 20, 30)
        adapter.set_power(False)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 0, 0, 0), set_zone(1, 0, 0, 0)])
        adapter.set_color(40, 50, 60)  # Remembered while off, nothing sent.
        self.assertEqual(len(self.mouse.sets()), 4)
        adapter.set_power(True)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 40, 50, 60), set_zone(1, 40, 50, 60)])

    def test_a_sync_frame_is_averaged_and_scaled(self):
        adapter = self.adapter()
        adapter.set_brightness(50)
        adapter.set_segments([(255, 0, 0), (0, 0, 255)])
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 64, 0, 64), set_zone(1, 64, 0, 64)])
        self.assertEqual(adapter.capabilities.segment_count, 1)
        adapter.set_segments([])
        self.assertEqual(len(self.mouse.sets()), 4)

    def test_the_persist_byte_is_always_zero(self):
        adapter = self.adapter()
        for colour in ((255, 255, 255), (1, 2, 3), (0, 0, 0)):
            adapter.set_color(*colour)
        adapter.set_power(False)
        adapter.set_segments([(9, 9, 9)])
        self.assertTrue(self.mouse.sets())
        self.assertTrue(all(packet[16] == 0 for packet in self.mouse.writes))

    def test_unrelated_input_reports_are_skipped(self):
        adapter = self.adapter()
        adapter.set_color(1, 1, 1)
        noise = [
            long_report("11 ff 02 3c 00 01 ff 00 00 02"),  # Same request from another program (swid 0xC).
            long_report("11 ff 02 00 01"),                  # A notification on the lighting feature.
            long_report("11 ff 05 00 aa"),                  # Another feature's notification.
            bytes.fromhex("10 ff 02 3b 00 00 00"),          # A short report.
            long_report("11 01 02 3b"),                     # Another device index.
            long_report("11 ff ff 02 3c 02"),               # Another program's error.
            b"\x11\xff",                                    # Truncated.
        ]
        self.mouse.script = [noise]
        adapter.set_color(2, 2, 2)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 2, 2, 2), set_zone(1, 2, 2, 2)])
        self.assertEqual(len(self.mouse.sets()), 4)  # No retry was needed.

    def test_stale_reports_are_drained_before_a_request(self):
        adapter = self.adapter()
        adapter.set_color(1, 1, 1)
        self.mouse.queue.extend([set_zone(0, 9, 9, 9), set_zone(1, 9, 9, 9)])  # Late replies.
        self.clock[0] += 5
        adapter.set_color(1, 1, 1)
        self.assertEqual(len(self.mouse.sets()), 4)
        self.assertFalse(self.mouse.queue)

    def test_a_hidpp_error_is_a_clear_error_without_a_retry(self):
        adapter = self.adapter()
        adapter.set_color(1, 1, 1)
        self.mouse.script = [0x02]
        with self.assertRaises(hidpp.HidppError) as caught:
            adapter.set_color(2, 2, 2)
        self.assertEqual(caught.exception.code, 0x02)
        self.assertIn("invalid argument", str(caught.exception))
        self.assertIn("0x8070", str(caught.exception))
        self.assertEqual(len(self.mouse.sets()), 3)
        # The handle is kept: the mouse answered.
        self.assertNotIn("close", self.mouse.events)

    def test_busy_is_retried_once(self):
        adapter = self.adapter()
        adapter.set_color(1, 1, 1)
        self.mouse.script = [0x08]
        adapter.set_color(2, 2, 2)
        self.assertEqual(self.mouse.sets()[-3:], [set_zone(0, 2, 2, 2)] * 2 + [set_zone(1, 2, 2, 2)])
        self.mouse.script = [0x08, 0x08]
        with self.assertRaises(hidpp.HidppError) as caught:
            adapter.set_color(3, 3, 3)
        self.assertEqual(caught.exception.code, 0x08)
        self.assertIn("busy", str(caught.exception))

    def test_silence_is_retried_once_then_an_error_and_a_fresh_handle(self):
        adapter = self.adapter()
        adapter.set_color(1, 1, 1)
        self.mouse.script = ["silent"]
        adapter.set_color(2, 2, 2)
        self.assertEqual(len(self.mouse.sets()), 5)
        self.assertNotIn("close", self.mouse.events)

        self.mouse.script = ["silent", "silent"]
        with self.assertRaises(RuntimeError) as caught:
            adapter.set_color(3, 3, 3)
        self.assertEqual(str(caught.exception), hidpp.NO_REPLY_MESSAGE)
        self.assertEqual(self.mouse.events[-1], "close")
        self.assertIn(hidpp.REPLY_TIMEOUT_SECONDS, self.mouse.read_timeouts)
        before = len(self.mouse.writes)
        adapter.set_color(3, 3, 3)
        self.assertEqual(self.mouse.writes[before:], OPEN_SEQUENCE + [set_zone(0, 3, 3, 3), set_zone(1, 3, 3, 3)])

    def test_an_io_error_drops_the_handle_and_the_next_command_reopens(self):
        adapter = self.adapter()
        adapter.set_color(1, 1, 1)
        self.mouse.write_error = OSError(1167, "The device is not connected.")
        with self.assertRaises(RuntimeError) as caught:
            adapter.set_color(2, 2, 2)
        self.assertEqual(str(caught.exception), hidpp.LOST_MESSAGE)
        self.assertEqual(self.mouse.events[-1], "close")
        self.mouse.write_error = None
        before = len(self.mouse.writes)
        adapter.set_color(2, 2, 2)
        self.assertEqual(self.mouse.writes[before:], OPEN_SEQUENCE + [set_zone(0, 2, 2, 2), set_zone(1, 2, 2, 2)])
        self.assertEqual(self.mouse.events.count("open"), 2)

    def test_identical_frames_are_skipped_for_a_second(self):
        adapter = self.adapter()
        adapter.set_segments([(5, 5, 5)])
        adapter.set_segments([(5, 5, 5)])
        self.clock[0] += 0.5
        adapter.set_segments([(5, 5, 5)])
        self.assertEqual(len(self.mouse.sets()), 2)
        adapter.set_segments([(6, 6, 6)])
        self.assertEqual(len(self.mouse.sets()), 4)
        self.clock[0] += hidpp.REPEAT_AFTER_SECONDS
        adapter.set_segments([(6, 6, 6)])
        self.assertEqual(len(self.mouse.sets()), 6)

    def test_adapters_share_one_handle_and_the_last_closes_it(self):
        gui, sync = self.adapter(), self.adapter()
        gui.set_color(1, 2, 3)
        sync.begin_stream()
        sync.set_segments([(1, 2, 3)])
        self.assertEqual(self.mouse.events.count("open"), 1)
        gui.close()
        self.assertNotIn("close", self.mouse.events)
        sync.close()
        self.assertEqual(self.mouse.events[-1], "close")

    def test_begin_stream_fails_clearly_without_the_mouse(self):
        self.mouse.infos = []
        with self.assertRaises(RuntimeError) as caught:
            self.adapter().begin_stream()
        self.assertEqual(str(caught.exception), hidpp.NOT_FOUND_MESSAGE)

    def test_construction_and_capabilities_never_touch_the_mouse(self):
        adapter = self.adapter()
        self.assertEqual(adapter.capabilities.segment_count, 1)
        self.assertEqual(adapter.capabilities.transport, "usb")
        self.assertEqual(self.mouse.events, [])
        self.assertEqual(self.mouse.enumerations, 0)


class CorrectionTests(HidppTestCase):
    """Colour correction: on by default, sRGB curve then green at 88%."""

    def test_the_curve_and_white_balance_are_the_shared_ones(self):
        from lumisync.drivers import led_color

        self.assertEqual(hidpp.LOGITECH_WHITE_BALANCE, (1.0, 0.88, 1.0))
        self.assertIs(fusion.SRGB_TO_LINEAR, led_color.SRGB_TO_LINEAR)
        self.assertEqual(fusion.WHITE_BALANCE, (1.0, 0.84, 0.59))  # The board keeps its own.

    def test_white_loses_its_green_tint_and_hot_pink_is_corrected(self):
        adapter = self.adapter(MOUSE)
        adapter.set_color(255, 255, 255)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 255, 224, 255), set_zone(1, 255, 224, 255)])
        adapter.set_color(255, 105, 180)
        # sRGB curve: (255, 36, 116); green x 0.88: 32.
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 255, 32, 116), set_zone(1, 255, 32, 116)])
        self.assertEqual(self.mouse.zone_colours, {0: (255, 32, 116), 1: (255, 32, 116)})

    def test_brightness_is_applied_before_the_curve_and_the_peak_is_kept(self):
        adapter = self.adapter(MOUSE)
        adapter.set_color(200, 100, 50)
        adapter.set_brightness(50)
        # Brightness first: (100, 50, 25). The curve and green x 0.88 give the
        # colour mix; it is then scaled so the strongest channel stays at 100.
        curve = fusion.SRGB_TO_LINEAR[100], fusion.SRGB_TO_LINEAR[50] * 0.88, fusion.SRGB_TO_LINEAR[25]
        scale = 100 / curve[0]
        expected = 100, round(curve[1] * scale), round(curve[2] * scale)
        self.assertEqual(expected, (100, 22, 6))
        self.assertEqual(hidpp.wire_color((200, 100, 50), 50, "srgb"), expected)
        self.assertEqual(self.mouse.sets()[-1], set_zone(1, *expected))

    def test_a_mid_colour_is_not_darkened_by_the_curve(self):
        # Confirmed by eye: soft purple keeps its level (peak 200), unlike the plain curve (147).
        self.assertEqual(hidpp.wire_color((180, 120, 200), 100, "srgb"), (158, 57, 200))
        self.assertEqual(hidpp.wire_color((128, 128, 128), 100, "srgb"), (128, 113, 128))
        self.assertEqual(hidpp.wire_color((0, 0, 0), 100, "srgb"), (0, 0, 0))

    def test_the_board_keeps_the_plain_curve(self):
        from lumisync.drivers import led_color

        self.assertEqual(
            led_color.correct_color((180, 120, 200)),
            (led_color.SRGB_TO_LINEAR[180], led_color.SRGB_TO_LINEAR[120], led_color.SRGB_TO_LINEAR[200]),
        )
        self.assertEqual(led_color.correct_color((180, 120, 200)), (116, 48, 147))

    def test_switching_correction_off_sends_the_colour_raw(self):
        adapter = self.adapter({**MOUSE, "color_correction": "none"})
        adapter.set_color(255, 105, 180)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 255, 105, 180), set_zone(1, 255, 105, 180)])

    def test_sync_frames_and_power_off_are_corrected_too(self):
        adapter = self.adapter(MOUSE)
        adapter.set_segments([(255, 255, 255), (255, 255, 255)])
        self.assertEqual(self.mouse.sets()[-1], set_zone(1, 255, 224, 255))
        adapter.set_power(False)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 0, 0, 0), set_zone(1, 0, 0, 0)])
        self.assertTrue(all(packet[16] == 0 for packet in self.mouse.writes))


class ZoneLayoutTests(HidppTestCase):
    """``lighting_layout: "zones"``: one colour per zone during sync."""

    ZONED = {**RAW, "lighting_layout": "zones"}

    def test_capabilities_before_the_first_open_come_from_the_table(self):
        self.assertEqual(registry.capabilities_for_device(MOUSE).segment_count, 1)
        self.assertEqual(registry.capabilities_for_device(self.ZONED).segment_count, 2)
        self.assertEqual(hidpp.zone_names(MOUSE), ("DPI lights", "Logo"))
        self.assertEqual(hidpp.layout(MOUSE), "whole")
        self.assertEqual(hidpp.layout(self.ZONED), "zones")
        self.assertEqual(hidpp.layout({**MOUSE, "lighting_layout": "diagonal"}), "whole")
        self.assertEqual(self.mouse.events, [])
        self.assertEqual(self.mouse.enumerations, 0)

    def test_a_sync_frame_is_one_colour_per_zone_in_zone_order(self):
        adapter = self.adapter(self.ZONED)
        adapter.begin_stream()
        self.assertEqual(adapter.capabilities.segment_count, 2)
        adapter.set_segments([(255, 0, 0), (0, 0, 255)])
        self.assertEqual(self.mouse.sets(), [set_zone(0, 255, 0, 0), set_zone(1, 0, 0, 255)])
        self.assertEqual(self.mouse.zone_colours, {0: (255, 0, 0), 1: (0, 0, 255)})
        # Any frame length is resized to the zones.
        adapter.set_segments([(10, 0, 0), (10, 0, 0), (0, 0, 20), (0, 0, 20)])
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 10, 0, 0), set_zone(1, 0, 0, 20)])

    def test_per_zone_frames_are_scaled_and_corrected(self):
        adapter = self.adapter({**MOUSE, "lighting_layout": "zones"})
        adapter.set_segments([(255, 255, 255), (255, 105, 180)])
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 255, 224, 255), set_zone(1, 255, 32, 116)])

    def test_manual_colour_power_and_brightness_paint_every_zone(self):
        adapter = self.adapter(self.ZONED)
        adapter.set_color(200, 100, 0)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 200, 100, 0), set_zone(1, 200, 100, 0)])
        adapter.set_brightness(50)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 100, 50, 0), set_zone(1, 100, 50, 0)])
        adapter.set_power(False)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 0, 0, 0), set_zone(1, 0, 0, 0)])

    def test_the_zone_count_and_names_come_from_the_mouse_once_open(self):
        self.mouse = FakeMouse(zones=3, locations=(0x0002, 0x0001, 0x0009))
        with patch.object(hidpp, "_hid", self.mouse):
            adapter = self.adapter(self.ZONED)
            adapter.begin_stream()
            self.assertEqual(adapter.capabilities.segment_count, 3)
            self.assertEqual(hidpp.zone_names(self.ZONED), ("Logo", "DPI lights", "Zone 3"))
            adapter.set_segments([(1, 0, 0), (0, 2, 0), (0, 0, 3)])
        self.assertEqual(self.mouse.sets(), [set_zone(0, 1, 0, 0), set_zone(1, 0, 2, 0), set_zone(2, 0, 0, 3)])

    def test_a_zone_without_location_info_is_still_usable(self):
        self.mouse.script = [None, None, None, 0x02]  # ping, getFeature, getInfo, then getZoneInfo(0) fails
        adapter = self.adapter(self.ZONED)
        adapter.begin_stream()
        self.assertEqual(hidpp.zone_names(self.ZONED), ("Zone 1", "Logo"))

    def test_only_reads_and_fixed_colours_are_ever_written(self):
        adapter = self.adapter(self.ZONED)
        adapter.set_segments([(1, 2, 3), (4, 5, 6)])
        adapter.set_power(False)
        for packet in self.mouse.writes:
            function = packet[3] >> 4
            self.assertIn(function, {0, 1, 2, 3})
            if packet[2] == 2 and function == 3:
                self.assertIn(packet[5], (0x00, 0x01))
                self.assertEqual(packet[16], 0)

    def test_options_reach_a_pooled_adapter_live_without_reopening(self):
        self.addCleanup(pool.close, MOUSE)
        adapter = pool.acquire(RAW)
        adapter.set_color(255, 105, 180)
        self.assertEqual(self.mouse.sets()[-1], set_zone(1, 255, 105, 180))
        self.assertTrue(pool.refresh({**MOUSE, "lighting_layout": "zones"}))
        self.assertIs(pool.acquire({**MOUSE, "lighting_layout": "zones"}), adapter)
        self.assertEqual(adapter.capabilities.segment_count, 2)
        adapter.reapply()  # Correction is back on: the colour is shown corrected at once.
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 255, 32, 116), set_zone(1, 255, 32, 116)])
        adapter.set_segments([(255, 0, 0), (0, 0, 255)])
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 255, 0, 0), set_zone(1, 0, 0, 255)])
        self.assertEqual(self.mouse.events.count("open"), 1)

    def test_reapply_never_opens_the_mouse_or_lights_it_when_off(self):
        adapter = self.adapter()
        adapter.reapply()
        self.assertEqual(self.mouse.events, [])
        adapter.set_color(1, 2, 3)
        adapter.set_power(False)
        before = len(self.mouse.writes)
        adapter.reapply()
        self.assertEqual(len(self.mouse.writes), before)

    def test_lighting_options_describe_the_mouse(self):
        options = registry.lighting_options(MOUSE)
        self.assertEqual(options.title, "Mouse lighting")
        self.assertTrue(options.color_correction)
        self.assertEqual((options.layout_field, options.layout_whole, options.layout_zones),
                         ("lighting_layout", "whole", "zones"))
        self.assertEqual((options.whole_label, options.zones_label), ("Whole mouse", "Per zone (2)"))
        self.assertEqual(options.whole_description, "One colour on both lights")
        self.assertEqual(options.zones_description, "DPI lights and logo, one colour each")
        self.assertFalse(options.per_zone)
        self.assertTrue(registry.lighting_options(self.ZONED).per_zone)
        board = registry.lighting_options(BOARD)
        self.assertEqual((board.title, board.layout_field, board.layout_whole),
                         ("Board lighting", "rgb_fusion_layout", "board"))
        self.assertIsNone(registry.lighting_options({"mac": "AA", "transport": "lan"}))

    def test_summaries_show_the_zones(self):
        from lumisync.gui.widgets.device_card import device_subline
        from lumisync.gui.widgets.device_chip import light_summary

        self.assertEqual(light_summary(self.ZONED), "Mouse lighting · USB · 2 zones")
        self.assertEqual(device_subline(self.ZONED), "Mouse lighting · USB · 2 zones")
        self.assertEqual(light_summary(MOUSE), "Mouse lighting · USB")


BREATHING = {**RAW, "lighting_effect": "breathing"}
CYCLE = {**RAW, "lighting_effect": "colour_cycle"}


def effect_reads(writes):
    """The getZoneEffectInfo requests among ``writes``."""
    return [w for w in writes if w[2] == 2 and w[3] >> 4 == 2]


class EffectTests(HidppTestCase):
    """Fixed colour, breathing and colour cycle, by the zones' effect indexes."""

    def test_the_effect_indexes_are_read_from_each_zone_when_opening(self):
        self.adapter().set_color(1, 2, 3)
        controller = hidpp._controllers[(0x046D, 0xC08B)]
        self.assertEqual(controller.effect_indexes, G502_INDEXES)
        # Index 4 is past the list: the mouse would answer 0x02, so it is never asked.
        self.assertEqual(len(effect_reads(self.mouse.writes)), 8)

    def test_a_mouse_listing_the_effects_in_another_order_still_works(self):
        self.mouse = FakeMouse(effects=((0x000A, 992), (0x0003, 992), (0x0000, 0), (0x0001, 0)))
        with patch.object(hidpp, "_hid", self.mouse):
            adapter = self.adapter()
            adapter.set_color(1, 2, 3)
            self.assertEqual([p[0] for _zone, p in self.mouse.set_params], [3, 3])  # fixed at index 3
            adapter.update_device(dict(BREATHING))
            adapter.reapply()
            self.assertEqual([p[0] for _zone, p in self.mouse.set_params[-2:]], [0, 0])
            adapter.update_device(dict(CYCLE))
            adapter.reapply()
            self.assertEqual([p[0] for _zone, p in self.mouse.set_params[-2:]], [1, 1])
        self.assertEqual(self.mouse.zone_colours, {0: (1, 2, 3), 1: (1, 2, 3)})

    def test_breathing_bytes_blue_at_speed_three(self):
        adapter = self.adapter(BREATHING)
        adapter.set_color(0, 0, 255)
        self.assertEqual(self.mouse.sets(), [set_breathing(0, 0, 0, 255, 3000), set_breathing(1, 0, 0, 255, 3000)])
        self.assertEqual(self.mouse.set_params[-1], (1, bytes.fromhex("02 00 00 ff 0b b8 00 64 00 00 00 00")))

    def test_each_speed_sends_its_period(self):
        expected = {1: 8000, 2: 5000, 3: 3000, 4: 2000, 5: 1000}
        for speed, period in expected.items():
            for device in (BREATHING, CYCLE):
                with self.subTest(speed=speed, effect=device["lighting_effect"]):
                    adapter = self.adapter({**device, "lighting_effect_speed": speed})
                    adapter.set_color(10, 20, 30)
                    if device is CYCLE:
                        self.assertEqual(self.mouse.sets()[-2:], [set_cycle(0, period), set_cycle(1, period)])
                    else:
                        self.assertEqual(self.mouse.sets()[-1], set_breathing(1, 10, 20, 30, period))
                    adapter.close()

    def test_colour_cycle_ignores_colour_logo_colour_and_brightness(self):
        adapter = self.adapter({**CYCLE, "lighting_logo_color_enabled": True, "lighting_logo_color": [255, 0, 0]})
        adapter.set_color(0, 255, 0)
        adapter.set_brightness(30)
        self.assertEqual(self.mouse.sets()[-2:], [set_cycle(0, 3000), set_cycle(1, 3000)])
        self.assertEqual(self.mouse.set_params[-1], (1, bytes.fromhex("03 00 00 00 00 00 0b b8 64 00 00 00")))

    def test_brightness_scales_the_breathing_colour(self):
        adapter = self.adapter(BREATHING)
        adapter.set_color(200, 100, 0)
        adapter.set_brightness(50)
        self.assertEqual(self.mouse.sets()[-2:], [set_breathing(0, 100, 50, 0, 3000), set_breathing(1, 100, 50, 0, 3000)])

    def test_power_off_is_fixed_black_and_on_brings_the_effect_back(self):
        adapter = self.adapter(BREATHING)
        adapter.set_color(0, 0, 255)
        adapter.set_power(False)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 0, 0, 0), set_zone(1, 0, 0, 0)])
        adapter.set_power(True)
        self.assertEqual(self.mouse.sets()[-2:], [set_breathing(0, 0, 0, 255, 3000), set_breathing(1, 0, 0, 255, 3000)])
        self.assertTrue(all(packet[16] == 0 for packet in self.mouse.writes))

    def test_logo_colour_on_and_off(self):
        logo = {**RAW, "lighting_logo_color_enabled": True, "lighting_logo_color": [255, 0, 0]}
        adapter = self.adapter(logo)
        adapter.set_color(0, 0, 255)
        self.assertEqual(self.mouse.zone_colours, {0: (0, 0, 255), 1: (255, 0, 0)})
        adapter.update_device({**logo, "lighting_logo_color_enabled": None})
        adapter.reapply()
        self.assertEqual(self.mouse.zone_colours, {0: (0, 0, 255), 1: (0, 0, 255)})
        # A saved colour stays unused while the switch is off; on without a
        # colour, the logo follows the main colour.
        adapter.update_device({**RAW, "lighting_logo_color_enabled": True})
        adapter.reapply()
        self.assertEqual(self.mouse.zone_colours, {0: (0, 0, 255), 1: (0, 0, 255)})
        adapter.update_device({**BREATHING, "lighting_logo_color_enabled": True, "lighting_logo_color": [0, 80, 255]})
        adapter.set_color(255, 0, 0)
        self.assertEqual(self.mouse.sets()[-2:], [set_breathing(0, 255, 0, 0, 3000), set_breathing(1, 0, 80, 255, 3000)])

    def test_the_logo_zone_is_found_by_its_location(self):
        self.mouse = FakeMouse(locations=(0x0002, 0x0001))
        with patch.object(hidpp, "_hid", self.mouse):
            self.adapter({**RAW, "lighting_logo_color_enabled": True, "lighting_logo_color": [255, 0, 0]}).set_color(
                0, 0, 255)
        self.assertEqual(self.mouse.zone_colours, {0: (255, 0, 0), 1: (0, 0, 255)})

    def test_colour_correction_applies_to_both_colours(self):
        adapter = self.adapter({**MOUSE, "lighting_logo_color_enabled": True, "lighting_logo_color": [255, 105, 180]})
        adapter.set_color(255, 255, 255)
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 255, 224, 255), set_zone(1, 255, 32, 116)])
        adapter.update_device({**MOUSE, "lighting_effect": "breathing", "lighting_logo_color_enabled": True,
                               "lighting_logo_color": [255, 105, 180]})
        adapter.reapply()
        self.assertEqual(self.mouse.sets()[-2:], [set_breathing(0, 255, 224, 255, 3000),
                                                  set_breathing(1, 255, 32, 116, 3000)])

    def test_streaming_sends_fixed_frames_only_and_reads_nothing(self):
        for device in (BREATHING, {**BREATHING, "lighting_layout": "zones"}, RAW):
            with self.subTest(layout=device.get("lighting_layout", "whole"), effect=device.get("lighting_effect")):
                adapter = self.adapter(device)
                adapter.set_color(0, 0, 255)
                adapter.begin_stream()
                before = len(self.mouse.writes)
                for step in range(5):
                    self.clock[0] += 0.1
                    adapter.set_segments([(step * 10, 0, 0), (0, step * 10, 0)])
                frames = self.mouse.writes[before:]
                self.assertEqual(len(frames), 10)  # Two packets a frame, as before.
                self.assertEqual(effect_reads(frames), [])
                self.assertTrue(all(packet[3] >> 4 == 3 and packet[5] == 1 for packet in frames))
                adapter.close()

    def test_stream_end_restores_the_manual_effect(self):
        adapter = self.adapter(BREATHING)
        adapter.set_color(0, 0, 255)
        adapter.begin_stream()
        adapter.set_segments([(255, 0, 0)])
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 255, 0, 0), set_zone(1, 255, 0, 0)])
        before = len(self.mouse.writes)
        adapter.reapply()  # Not while streaming: the stream's end shows it.
        self.assertEqual(len(self.mouse.writes), before)
        adapter.end_stream()
        self.assertEqual(self.mouse.writes[before:], [set_breathing(0, 0, 0, 255, 3000),
                                                      set_breathing(1, 0, 0, 255, 3000)])
        adapter.end_stream()  # A second end does nothing.
        self.assertEqual(len(self.mouse.writes), before + 2)
        # The next stream's first frame goes out even if it matches the last fixed frame.
        adapter.begin_stream()
        adapter.set_segments([(255, 0, 0)])
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 255, 0, 0), set_zone(1, 255, 0, 0)])

    def test_stream_end_restores_a_manual_fixed_colour_too(self):
        adapter = self.adapter()
        adapter.set_color(0, 255, 0)
        adapter.begin_stream()
        adapter.set_segments([(255, 0, 0)])
        adapter.end_stream()
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 0, 255, 0), set_zone(1, 0, 255, 0)])

    def test_stream_end_without_manual_state_keeps_the_last_sync_colour(self):
        adapter = self.adapter(BREATHING)
        adapter.begin_stream()
        adapter.set_segments([(255, 0, 0)])
        before = len(self.mouse.writes)
        adapter.end_stream()
        self.assertEqual(len(self.mouse.writes), before)
        self.assertEqual(self.mouse.zone_colours, {0: (255, 0, 0), 1: (255, 0, 0)})

    def test_a_missing_effect_falls_back_to_fixed_and_warns_once(self):
        self.mouse = FakeMouse(effects=((0x0000, 0), (0x0001, 0), (0x0003, 992)))  # No breathing.
        with patch.object(hidpp, "_hid", self.mouse):
            self.assertIn("breathing", [c.key for c in registry.lighting_options(BREATHING).effects.choices])
            adapter = self.adapter(BREATHING)
            with self.assertLogs("lumisync_logitech", level="WARNING") as logs:
                adapter.set_color(0, 0, 255)
                adapter.set_color(0, 0, 200)
            self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 0, 0, 200), set_zone(1, 0, 0, 200)])
            self.assertEqual(sum("breathing effect" in line for line in logs.output), 1)
            # Once opened, the options leave it out (fixed colour stays).
            options = registry.lighting_options(BREATHING)
            self.assertEqual([c.key for c in options.effects.choices], ["fixed", "colour_cycle"])
            self.assertEqual(options.effects.effect, "fixed")

    def test_a_zone_whose_effects_cannot_be_read_keeps_fixed_colour_only(self):
        self.mouse.script = [None, None, None, 0x02]  # ping, getFeature, getInfo, then getZoneInfo(0) fails
        adapter = self.adapter(BREATHING)
        with self.assertLogs("lumisync_logitech", level="WARNING"):
            adapter.set_color(0, 0, 255)
        controller = hidpp._controllers[(0x046D, 0xC08B)]
        self.assertEqual(controller.effect_indexes[0], {0x0001: 1})
        self.assertEqual(self.mouse.sets()[-2:], [set_zone(0, 0, 0, 255), set_zone(1, 0, 0, 255)])

    def test_descriptor_fields_and_fallbacks(self):
        self.assertEqual(hidpp.effect_key(MOUSE), "fixed")
        self.assertEqual(hidpp.effect_key({**MOUSE, "lighting_effect": " Breathing "}), "breathing")
        self.assertEqual(hidpp.effect_key({**MOUSE, "lighting_effect": "wave"}), "fixed")
        for value, step in ((None, 3), (1, 1), (5, 5), ("4", 4), (0, 3), (6, 3), (True, 3), ("fast", 3)):
            with self.subTest(speed=value):
                self.assertEqual(hidpp.effect_speed({**MOUSE, "lighting_effect_speed": value}), step)
        self.assertFalse(hidpp.logo_color_enabled(MOUSE))
        self.assertFalse(hidpp.logo_color_enabled({**MOUSE, "lighting_logo_color_enabled": "yes"}))
        self.assertTrue(hidpp.logo_color_enabled({**MOUSE, "lighting_logo_color_enabled": True}))
        for value in (None, [1, 2], [1, 2, 300], ["1", 2, 3], [True, 0, 0], "red"):
            with self.subTest(colour=value):
                self.assertIsNone(hidpp.logo_color({**MOUSE, "lighting_logo_color": value}))
        self.assertEqual(hidpp.logo_color({**MOUSE, "lighting_logo_color": [1, 2, 3]}), (1, 2, 3))
        # A descriptor saved and loaded keeps the settings.
        saved = Device.from_dict({**MOUSE, "lighting_effect": "colour_cycle", "lighting_effect_speed": 5,
                                  "lighting_logo_color_enabled": True, "lighting_logo_color": [9, 8, 7]}).to_dict()
        self.assertEqual((hidpp.effect_key(saved), hidpp.effect_period_ms(saved), hidpp.logo_color(saved)),
                         ("colour_cycle", 1000, (9, 8, 7)))

    def test_lighting_options_offer_the_effects_and_the_logo_colour(self):
        options = registry.lighting_options(MOUSE)
        effects = options.effects
        self.assertEqual([(c.key, c.name, c.has_speed, c.has_color) for c in effects.choices], [
            ("fixed", "Fixed colour", False, True),
            ("breathing", "Breathing", True, True),
            ("colour_cycle", "Colour cycle", True, False),
        ])
        self.assertTrue(all(not c.has_direction and not c.multicolor for c in effects.choices))
        self.assertEqual((effects.effect, effects.speed, effects.default_effect, effects.default_speed,
                          effects.speed_steps), ("fixed", 3, "fixed", 3, 5))
        secondary = options.secondary_color
        self.assertEqual((secondary.title, secondary.description, secondary.color_title),
                         ("Separate logo colour", "Give the logo its own colour. The DPI lights keep the main colour.",
                          "Logo colour"))
        self.assertEqual((secondary.enabled_field, secondary.color_field),
                         ("lighting_logo_color_enabled", "lighting_logo_color"))
        self.assertFalse(secondary.enabled)
        self.assertIsNone(secondary.color)
        self.assertEqual(self.mouse.events, [])  # Built from the table, nothing opened.


class WiringTests(HidppTestCase):
    def test_both_usb_types_reach_their_own_driver(self):
        self.assertIsInstance(registry.create_adapter(MOUSE), hidpp.LogitechHidppAdapter)
        self.assertIsInstance(registry.create_adapter({"type": "logitech_hidpp"}), hidpp.LogitechHidppAdapter)
        self.assertIsInstance(registry.create_adapter(BOARD), fusion.GigabyteFusionAdapter)
        self.assertEqual(registry.capabilities_for_device(MOUSE).segment_count, 1)
        self.assertTrue(registry.is_rgb_fusion(BOARD))
        self.assertFalse(registry.is_rgb_fusion(MOUSE))
        self.assertEqual(self.mouse.events, [])

    def test_the_type_alone_means_usb(self):
        device = Device.from_dict({"type": "logitech_hidpp", "device_id": "hidpp-046d-c08b"})
        self.assertEqual(device.transport, "usb")
        self.assertTrue(device.is_local_hardware and device.is_pooled)

    def test_pool_keeps_one_mouse_adapter_beside_the_board(self):
        self.addCleanup(pool.close, MOUSE)
        self.addCleanup(pool.close, BOARD)
        mouse = pool.acquire(MOUSE)
        self.assertIs(pool.acquire({**MOUSE, "name": "Renamed"}), mouse)
        self.assertIsInstance(mouse, hidpp.LogitechHidppAdapter)
        with patch.object(fusion, "_hid", object()):
            board = pool.acquire(BOARD)
        self.assertIsInstance(board, fusion.GigabyteFusionAdapter)
        self.assertIn("usb:hidpp-046d-c08b", pool._pooled_adapters)
        mouse.set_color(1, 2, 3)
        pool.close(MOUSE)
        self.assertEqual(self.mouse.events[-1], "close")

    def test_status_worker_asks_each_driver_for_presence(self):
        from lumisync.gui.controllers import discovery

        updates = {}
        worker = discovery.DeviceStatusWorker([(0, dict(MOUSE)), (1, dict(BOARD))])
        worker.state_updated.connect(lambda index, update: updates.setdefault(index, update))
        with patch.object(hidpp, "device_present", return_value=False) as mouse_probe, \
                patch.object(fusion, "controller_present", return_value=True) as board_probe, \
                patch.object(discovery.pool, "acquire", side_effect=AssertionError("adapter opened")), \
                patch.object(discovery, "create_adapter", side_effect=AssertionError("adapter opened")):
            worker.run()
        mouse_probe.assert_called_once()
        board_probe.assert_called_once()
        self.assertFalse(updates[0]["online"])
        self.assertEqual(updates[0]["last_error"], hidpp.NOT_FOUND_MESSAGE)
        self.assertFalse(updates[0]["readback_supported"])
        self.assertTrue(updates[1]["online"])
        self.assertIsNone(updates[1]["last_error"])
        self.assertEqual(self.mouse.writes, [])

    def test_labels(self):
        from lumisync.gui.resources.icons import IconKey
        from lumisync.gui.utils.device_identity import device_identity, is_pc_hardware
        from lumisync.gui.widgets.device_card import device_subline
        from lumisync.gui.widgets.device_chip import light_icon, light_summary

        self.assertTrue(is_pc_hardware(MOUSE))
        self.assertEqual(device_identity(MOUSE), ("Mouse lighting", IconKey.MOUSE))
        self.assertEqual(device_subline(MOUSE), "Mouse lighting · USB")
        self.assertEqual(light_summary(MOUSE), "Mouse lighting · USB")
        self.assertEqual(light_icon(MOUSE), "mouse.svg")


class GuiTests(unittest.TestCase):
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
        controller.devices = [dict(BOARD), dict(MOUSE)]
        for name in ("refresh_device_states", "refresh_pc_hardware_states",
                     "refresh_local_device_states", "refresh_account_device_states"):
            patcher = patch.object(controller, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(controller, "get_groups", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        return controller

    def test_the_inspector_shows_each_drivers_lighting_card(self):
        from lumisync.gui.views.devices_view import DevicesView

        controller = self.controller()
        view = DevicesView(controller, scope="pc")
        self.addCleanup(view.deleteLater)
        view.resize(1280, 900)
        view._apply_device_list()
        self.assertEqual([card._index for card in view._cards], [0, 1])

        view._open_inspector(1)
        inspector = view.inspector
        self.assertFalse(inspector.usb_options_panel.isHidden())
        self.assertEqual(inspector.usb_options_label.text(), "Mouse lighting")
        self.assertTrue(inspector.color_correction_switch.isChecked())
        combo = inspector.layout_combo
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], ["Whole mouse", "Per zone (2)"])
        self.assertEqual([combo.itemData(i) for i in range(combo.count())], ["whole", "zones"])
        self.assertEqual(combo.currentData(), "whole")
        self.assertEqual(inspector.layout_hint.text(), "One colour on both lights")
        self.assertEqual(inspector.zones_value.text(), "Whole mouse · one colour")
        self.assertFalse(inspector._info_rows["Layout"].isHidden())
        self.assertTrue(inspector._info_rows["Address"].isHidden())
        self.assertEqual(inspector.readback_value.text(), "Last command only")
        self.assertEqual(inspector.type_label.text(), "Mouse lighting")
        self.assertFalse(inspector.brightness_controls.isHidden())
        self.assertFalse(inspector.color_controls.isHidden())

        inspector.set_device(1, {**MOUSE, "lighting_layout": "zones", "color_correction": "none"}, {}, primary=False)
        self.assertFalse(inspector.color_correction_switch.isChecked())
        self.assertEqual(combo.currentData(), "zones")
        self.assertEqual(inspector.layout_hint.text(), "DPI lights and logo, one colour each")
        self.assertEqual(inspector.zones_value.text(), "2 zones · per zone")
        self.assertIn("DPI lights, Logo", combo.toolTip())

        view._open_inspector(0)
        self.assertFalse(inspector.usb_options_panel.isHidden())
        self.assertEqual(inspector.usb_options_label.text(), "Board lighting")
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], ["Whole board", "Per zone (8)"])  # Generic board: 8 registers.
        self.assertEqual(combo.currentData(), "board")
        self.assertFalse(inspector._info_rows["Layout"].isHidden())
        self.assertEqual(inspector.zones_value.text(), "Whole board · one colour")

    def test_mouse_options_are_saved_with_the_mouse_fields(self):
        controller = self.controller()
        updated = []
        with patch.object(controller, "_write_settings", return_value=True) as saved,                 patch.object(pool, "refresh", side_effect=lambda d: updated.append(dict(d)) or False):
            self.assertTrue(controller.set_color_correction_at(1, False))
            self.assertEqual(controller.devices[1]["color_correction"], "none")
            self.assertTrue(controller.set_lighting_layout_at(1, "zones"))
            self.assertEqual(controller.devices[1]["lighting_layout"], "zones")
            self.assertNotIn("rgb_fusion_layout", controller.devices[1])
            self.assertEqual(controller.get_capabilities_at(1).segment_count, 2)
            # The board's values are not the mouse's.
            self.assertFalse(controller.set_lighting_layout_at(1, "board"))
            self.assertTrue(controller.set_lighting_layout_at(1, "whole"))
            self.assertIsNone(controller.devices[1].get("lighting_layout"))  # Default: stored as absent.
        self.assertEqual(saved.call_count, 3)
        self.assertEqual([d.get("lighting_layout") for d in updated], [None, "zones", None])

    def test_layout_waits_for_a_running_output(self):
        controller = self.controller()
        with patch.object(controller, "_write_settings", return_value=True) as saved,                 patch.object(controller, "is_device_busy", return_value=True),                 patch.object(pool, "refresh", return_value=True):
            self.assertFalse(controller.set_lighting_layout_at(1, "zones"))
            self.assertTrue(controller.set_color_correction_at(1, False))
        self.assertEqual(saved.call_count, 1)

    def test_lan_lights_have_no_lighting_options(self):
        controller = self.controller()
        controller.devices = [{"mac": "fixture-lamp", "transport": "lan", "ip": "192.0.2.5"}]
        with patch.object(controller, "_write_settings") as saved:
            self.assertFalse(controller.set_color_correction_at(0, False))
            self.assertFalse(controller.set_lighting_layout_at(0, "zones"))
        saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
