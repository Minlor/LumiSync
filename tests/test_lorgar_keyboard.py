"""Lorgar AZAR keyboard lighting, against a simulated keyboard only.

No test here talks to a real keyboard: ``lorgar_keyboard._hid`` is replaced
by :class:`FakeKeyboard`, which answers BEGIN, SET and END the way the
keyboard did in the USB capture, and can be told to stay silent, answer
with something else or fail. The rate limiter's clock and wait are replaced
by :class:`FakeClock`, so no test sleeps.
"""

import os
import sys
import threading
import time
import unittest
from collections import deque
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lumisync import devices
from lumisync.device import Device
from lumisync.drivers import lorgar_keyboard as lorgar
from lumisync.drivers import pool, registry
from lumisync.drivers.hid_feature import HidDeviceInfo

KEYBOARD = {
    "device_id": "lorgar-320f-5075",
    "name": "Lorgar AZAR",
    "model": "Lorgar AZAR",
    "transport": "usb",
    "type": "lorgar_keyboard",
    "device_kind": "keyboard",
    "usb_vendor_id": 0x320F,
    "usb_product_id": 0x5075,
}

PATH = "fake\\vid_320f&pid_5075&mi_02&col03"
COLLECTIONS = [
    HidDeviceInfo("fake\\vid_320f&pid_5075&mi_00", 0x320F, 0x5075, 0x0001, 0x0006, 0, 9, 0),
    HidDeviceInfo("fake\\vid_320f&pid_5075&mi_02&col01", 0x320F, 0x5075, 0xFF1C, 0x0091, 0, 64, 64),
    HidDeviceInfo(PATH, 0x320F, 0x5075, 0xFF1C, 0x0092, 0, 64, 64),
]

BEGIN = bytes.fromhex("04020001") + bytes(60)
END = bytes.fromhex("04030002") + bytes(60)
# The packet that showed solid green on the keyboard: static, level 4.
GREEN = bytes.fromhex("04a600a519000000" "00060402ff00" "00ff00") + bytes(47)
BEGIN_REPLY = bytes.fromhex("04020001000000ff") + bytes(56)
END_REPLY = bytes.fromhex("04030002000000ff") + bytes(56)


def set_packet(level, r, g, b):
    return bytes.fromhex("04a600a519000000") + bytes([0x00, 0x06, level, 0x02, 0xFF, 0x00, r, g, b]) + bytes(47)


class FakeStream:
    def __init__(self, keyboard, path):
        self.keyboard = keyboard
        self.path = path
        self.is_open = False

    def open(self):
        self.keyboard.events.append("open")
        if self.keyboard.open_error is not None:
            raise self.keyboard.open_error
        self.is_open = True

    def write(self, data, timeout=1.0):
        assert self.is_open, "report written on a closed handle"
        self.keyboard.receive(bytes(data))

    def read(self, size, timeout):
        assert self.is_open, "report read on a closed handle"
        if self.keyboard.queue:
            return self.keyboard.queue.popleft()
        return None

    def close(self):
        if self.is_open:
            self.keyboard.events.append("close")
        self.is_open = False


class FakeKeyboard:
    """The AZAR as captured: BEGIN and END answered with ``... 00 00 00 ff``,
    SET echoed byte for byte."""

    def __init__(self):
        self.infos = list(COLLECTIONS)
        self.writes = []
        self.events = []
        self.queue = deque()
        self.enumerations = 0
        self.enumerate_error = None
        self.open_error = None
        # Per upcoming request: None answers normally, "silent" drops the
        # reply, "mismatch" answers a SET with other bytes, an exception is
        # raised by the write, a list of reports is queued before the reply.
        self.script = []

    def enumerate(self, vendor_id, product_id):
        self.enumerations += 1
        if self.enumerate_error is not None:
            raise self.enumerate_error
        return [info for info in self.infos if (info.vendor_id, info.product_id) == (vendor_id, product_id)]

    def HidStream(self, path):  # noqa: N802 - mirrors hid_feature.HidStream
        self.events.append(("stream", path))
        return FakeStream(self, path)

    def receive(self, packet):
        action = self.script.pop(0) if self.script else None
        if isinstance(action, BaseException):
            raise action
        assert len(packet) == 64, packet.hex()
        self.writes.append(packet)
        if action == "silent":
            return
        if isinstance(action, list):
            self.queue.extend(action)
        if packet[:4] == BEGIN[:4]:
            self.queue.append(BEGIN_REPLY)
        elif packet[:4] == END[:4]:
            self.queue.append(END_REPLY)
        elif action == "mismatch":
            self.queue.append(packet[:14] + bytes([0x12, 0x34, 0x56]) + packet[17:])
        else:
            self.queue.append(packet)

    def sets(self):
        return [packet for packet in self.writes if packet[1] == 0xA6]


class FakeClock:
    """Monotonic time that only moves when the rate limiter waits."""

    def __init__(self):
        self.now = 1000.0
        self.waits = []
        self.on_wait = None

    def monotonic(self):
        return self.now

    def wait(self, event, seconds):
        self.waits.append(round(seconds, 6))
        if self.on_wait is not None:
            hook, self.on_wait = self.on_wait, None
            hook()
        if event.is_set():
            return True
        self.now += seconds
        return False


class LorgarTestCase(unittest.TestCase):
    def setUp(self):
        self.keyboard = FakeKeyboard()
        self.clock = FakeClock()
        for name, value in (("_hid", self.keyboard), ("_monotonic", self.clock.monotonic),
                            ("_wait", self.clock.wait)):
            patcher = patch.object(lorgar, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._reset)

    def _reset(self):
        lorgar.close_all()
        lorgar._controllers.clear()

    def adapter(self, device=None):
        adapter = lorgar.LorgarKeyboardAdapter(dict(device or KEYBOARD))
        self.addCleanup(adapter.close)
        return adapter


class PacketTests(unittest.TestCase):
    def test_begin_set_and_end_bytes(self):
        self.assertEqual(lorgar.build_begin(), BEGIN)
        self.assertEqual(lorgar.build_end(), END)
        self.assertEqual(lorgar.build_set_static(4, (0, 255, 0)), GREEN)
        self.assertEqual(
            GREEN[:17].hex(" "),
            "04 a6 00 a5 19 00 00 00 00 06 04 02 ff 00 00 ff 00",
        )
        self.assertEqual(len(GREEN), 64)
        self.assertEqual(lorgar.build_set_static(0, (0, 0, 0)), set_packet(0, 0, 0, 0))

    def test_colour_channels_are_clamped(self):
        self.assertEqual(lorgar.build_set_static(2, (300, -4, 17)), set_packet(2, 255, 0, 17))

    def test_out_of_range_brightness_is_refused_by_the_builder(self):
        for level in (-1, 5, 255):
            with self.assertRaises(ValueError):
                lorgar.build_set_static(level, (0, 0, 0))

    def test_allow_list_accepts_exactly_the_three_packets(self):
        for packet in (BEGIN, END, GREEN, set_packet(0, 0, 0, 0), set_packet(1, 255, 255, 255)):
            lorgar._check_packet(packet)

    def test_allow_list_rejects_every_deviation(self):
        def changed(packet, offset, value):
            data = bytearray(packet)
            data[offset] = value
            return bytes(data)

        bad = {
            "mode 0": changed(GREEN, 9, 0x00),
            "mode 17": changed(GREEN, 9, 17),
            "mode 19": changed(GREEN, 9, 19),
            "mode 0xff": changed(GREEN, 9, 0xFF),
            "brightness 5": changed(GREEN, 10, 5),
            "speed 5": changed(GREEN, 11, 0x05),
            "direction 0x01": changed(GREEN, 12, 0x01),
            "direction 0xfe": changed(GREEN, 12, 0xFE),
            "multicolour 2": changed(GREEN, 13, 0x02),
            "multicolour with a colour": changed(GREEN, 13, 0x01),
            "byte 8": changed(GREEN, 8, 0x01),
            "byte 4": changed(GREEN, 4, 0x18),
            "byte 7": changed(GREEN, 7, 0x01),
            "non-zero tail": changed(GREEN, 17, 0x01),
            "non-zero last byte": changed(GREEN, 63, 0x01),
            "command 0x08": bytes.fromhex("04080001") + bytes(60),
            "command 0x08 with data": changed(changed(GREEN, 1, 0x08), 3, 0x01),
            "other report id": changed(BEGIN, 0, 0x05),
            "other sub-command": changed(BEGIN, 3, 0x02),
            "begin with data": changed(BEGIN, 4, 0x01),
            "end with data": changed(END, 63, 0x01),
            "short": GREEN[:63],
            "long": GREEN + b"\0",
            "empty": b"",
        }
        for name, packet in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                lorgar._check_packet(packet)

    def test_send_point_checks_again_and_writes_nothing_bad(self):
        written = []

        class Device:
            def write(self, data, timeout=1.0):
                written.append(data)

        controller = lorgar._Controller(0x320F, 0x5075)
        controller.device = Device()
        with self.assertRaises(ValueError):
            lorgar._send(controller, bytes.fromhex("04080001") + bytes(60))
        with self.assertRaises(ValueError):
            lorgar._send(controller, set_packet(5, 0, 0, 0))
        self.assertEqual(written, [])
        lorgar._send(controller, BEGIN)
        self.assertEqual(written, [BEGIN])

    def test_brightness_levels(self):
        table = {0: 0, 1: 1, 10: 1, 25: 1, 37: 1, 38: 2, 50: 2, 62: 2, 63: 3, 75: 3,
                 87: 3, 88: 4, 100: 4, 150: 4, -5: 0}
        for percent, level in table.items():
            with self.subTest(percent=percent):
                self.assertEqual(lorgar.brightness_level(percent), level)


class SequenceTests(LorgarTestCase):
    def test_green_goes_out_as_begin_set_end(self):
        self.adapter().set_color(0, 255, 0)
        self.assertEqual(self.keyboard.writes, [BEGIN, GREEN, END])
        self.assertEqual(self.keyboard.events, [("stream", PATH), "open"])

    def test_stale_input_is_drained_first(self):
        self.keyboard.queue.extend([GREEN, BEGIN_REPLY, bytes(64)])
        self.adapter().set_color(0, 255, 0)
        self.assertEqual(self.keyboard.writes, [BEGIN, GREEN, END])
        self.assertFalse(self.keyboard.queue)

    def test_unrelated_reports_before_a_reply_are_skipped(self):
        self.keyboard.script = [None, [bytes.fromhex("01") + bytes(63), END_REPLY]]
        self.adapter().set_color(0, 255, 0)
        self.assertEqual(self.keyboard.writes, [BEGIN, GREEN, END])

    def test_end_is_sent_when_set_gets_no_reply(self):
        self.keyboard.script = [None, "silent"]
        with self.assertLogs("lumisync_lorgar", level="WARNING"):
            with self.assertRaises(RuntimeError) as caught:
                self.adapter().set_color(0, 255, 0)
        self.assertEqual(str(caught.exception), lorgar.NO_REPLY_MESSAGE)
        self.assertEqual(self.keyboard.writes, [BEGIN, GREEN, END])
        self.assertEqual(self.keyboard.events[-1], "close")

    def test_end_is_sent_when_the_set_write_fails(self):
        self.keyboard.script = [None, OSError("gone")]
        with self.assertLogs("lumisync_lorgar", level="WARNING"):
            with self.assertRaises(RuntimeError) as caught:
                self.adapter().set_color(0, 255, 0)
        self.assertEqual(str(caught.exception), lorgar.LOST_MESSAGE)
        self.assertEqual(self.keyboard.writes, [BEGIN, END])

    def test_an_echo_mismatch_is_a_failure(self):
        self.keyboard.script = [None, "mismatch"]
        with self.assertLogs("lumisync_lorgar", level="WARNING") as logs:
            with self.assertRaises(RuntimeError) as caught:
                self.adapter().set_color(0, 255, 0)
        self.assertEqual(str(caught.exception), lorgar.MISMATCH_MESSAGE)
        self.assertEqual(self.keyboard.writes, [BEGIN, GREEN, END])
        self.assertTrue(any("instead of an echo" in line for line in logs.output))

    def test_no_begin_reply_sends_no_set_but_still_ends(self):
        self.keyboard.script = ["silent"]
        with self.assertLogs("lumisync_lorgar", level="WARNING"):
            with self.assertRaises(RuntimeError):
                self.adapter().set_color(0, 255, 0)
        self.assertEqual(self.keyboard.writes, [BEGIN, END])

    def test_failure_is_retried_on_the_next_command(self):
        adapter = self.adapter()
        self.keyboard.script = [None, "silent"]
        with self.assertLogs("lumisync_lorgar", level="WARNING"), self.assertRaises(RuntimeError):
            adapter.set_color(0, 255, 0)
        self.keyboard.writes.clear()
        adapter.set_color(0, 255, 0)  # Same colour, but it was never confirmed.
        self.assertEqual(self.keyboard.writes, [BEGIN, GREEN, END])

    def test_power_off_is_static_black_at_level_zero_and_on_restores(self):
        adapter = self.adapter()
        adapter.set_brightness(50)
        adapter.set_color(10, 20, 30)
        self.keyboard.writes.clear()
        adapter.set_power(False)
        self.assertEqual(self.keyboard.writes, [BEGIN, set_packet(0, 0, 0, 0), END])
        self.keyboard.writes.clear()
        adapter.set_brightness(100)  # Remembered while off; nothing is sent.
        adapter.set_color(1, 2, 3)
        self.assertEqual(self.keyboard.writes, [])
        adapter.set_power(True)
        self.assertEqual(self.keyboard.sets(), [set_packet(4, 1, 2, 3)])

    def test_zero_percent_is_level_zero_with_the_colour_kept(self):
        adapter = self.adapter()
        adapter.set_color(0, 255, 0)
        adapter.set_brightness(0)
        self.assertEqual(self.keyboard.sets()[-1], set_packet(0, 0, 255, 0))

    def test_colours_go_out_raw(self):
        self.adapter().set_color(128, 64, 200)
        self.assertEqual(self.keyboard.sets(), [set_packet(4, 128, 64, 200)])

    def test_linux_permission_error_explains_the_udev_rule(self):
        self.keyboard.open_error = PermissionError("denied")
        with patch.object(sys, "platform", "linux"):
            with self.assertRaises(RuntimeError) as caught:
                self.adapter().set_color(1, 2, 3)
        self.assertIn(lorgar.UDEV_RULE, str(caught.exception))
        self.assertEqual(self.keyboard.writes, [])

    def test_missing_keyboard_is_reported(self):
        self.keyboard.infos = []
        with self.assertRaises(RuntimeError) as caught:
            self.adapter().set_color(1, 2, 3)
        self.assertEqual(str(caught.exception), lorgar.NOT_FOUND_MESSAGE)

    def test_close_releases_the_handle_and_leaves_lighting_alone(self):
        adapter = self.adapter()
        adapter.set_color(0, 255, 0)
        writes = len(self.keyboard.writes)
        adapter.close()
        self.assertEqual(self.keyboard.events[-1], "close")
        self.assertEqual(len(self.keyboard.writes), writes)


class RateLimitTests(LorgarTestCase):
    def test_at_most_one_sequence_per_interval(self):
        adapter = self.adapter()
        adapter.set_color(255, 0, 0)
        self.assertEqual(self.clock.waits, [])
        self.clock.now += 0.125  # Binary fractions keep the fake clock exact.
        adapter.set_color(0, 0, 255)
        self.assertEqual(self.clock.waits, [0.125])
        self.assertEqual(self.keyboard.sets(), [set_packet(4, 255, 0, 0), set_packet(4, 0, 0, 255)])
        self.clock.now += 1.0
        adapter.set_color(0, 255, 0)
        self.assertEqual(self.clock.waits, [0.125])  # Long enough since the last one.

    def test_a_state_already_shown_is_not_sent_again(self):
        adapter = self.adapter()
        adapter.set_color(0, 255, 0)
        adapter.set_color(0, 255, 0)
        adapter.set_brightness(99)  # Still level 4.
        adapter.set_power(True)
        self.assertEqual(self.keyboard.writes, [BEGIN, GREEN, END])
        self.assertEqual(self.clock.waits, [])

    def test_a_newer_request_replaces_one_waiting_and_lands(self):
        adapter = self.adapter()
        adapter.set_color(255, 0, 0)
        controller = lorgar._controllers[(0x320F, 0x5075)]
        latecomer = threading.Thread(target=adapter.set_color, args=(0, 255, 0))

        def newer_request_arrives():
            latecomer.start()
            deadline = time.monotonic() + 5
            while controller.color != (0, 255, 0) and time.monotonic() < deadline:
                time.sleep(0.001)

        self.clock.on_wait = newer_request_arrives
        adapter.set_color(0, 0, 255)  # Waits; the green request comes in meanwhile.
        latecomer.join(5)
        self.assertFalse(latecomer.is_alive())
        # Blue was never sent: the waiting writer sent the newest state, and
        # the newer caller found it already shown.
        self.assertEqual(self.keyboard.sets(), [set_packet(4, 255, 0, 0), GREEN])
        self.assertEqual(self.clock.waits, [0.25])

    def test_close_wakes_a_waiting_writer_without_sending(self):
        adapter = self.adapter()
        adapter.set_color(255, 0, 0)
        errors = []
        waiting = threading.Event()

        def real_wait(event, seconds):
            waiting.set()
            return event.wait(30)

        def change():
            try:
                adapter.set_color(0, 0, 255)
            except RuntimeError as exc:
                errors.append(str(exc))

        with patch.object(lorgar, "_wait", real_wait):
            writer = threading.Thread(target=change)
            writer.start()
            self.assertTrue(waiting.wait(5))
            started = time.monotonic()
            adapter.close()
            writer.join(5)
        self.assertFalse(writer.is_alive())
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(errors, [lorgar.CLOSED_MESSAGE])
        self.assertEqual(self.keyboard.sets(), [set_packet(4, 255, 0, 0)])
        self.assertEqual(self.keyboard.events[-1], "close")

    def test_a_new_adapter_works_after_close(self):
        first = self.adapter()
        first.set_color(255, 0, 0)
        first.close()
        self.clock.now += 1
        self.adapter().set_color(0, 255, 0)
        self.assertEqual(self.keyboard.sets()[-1], GREEN)


FIXED = "04a600a519000000" "00"
# Wave, level 4, UI speed 3 (wire 2), default direction, colour (0, 80, 255).
WAVE_BLUE = bytes.fromhex(FIXED + "010402ff00" "0050ff") + bytes(47)
# Wave, level 4, fastest, reversed, multicolour (colour black).
WAVE_REVERSED_FAST_RAINBOW = bytes.fromhex(FIXED + "0104000001" "000000") + bytes(47)


def wave(**fields):
    return {**KEYBOARD, "lighting_effect": "wave", **fields}


class EffectPacketTests(unittest.TestCase):
    def test_static_bytes_are_unchanged(self):
        self.assertEqual(lorgar.build_set_static(4, (0, 255, 0)), GREEN)
        self.assertEqual(lorgar.build_set(6, 4, 2, 0xFF, 0, (0, 255, 0)), GREEN)
        state = lorgar.effect_wire_state(lorgar.EffectSettings(), 4, (0, 255, 0))
        self.assertEqual(lorgar.build_set(*state), GREEN)

    def test_wave_blue_at_the_default_speed(self):
        self.assertEqual(lorgar.build_set(1, 4, 2, 0xFF, 0, (0, 80, 255)), WAVE_BLUE)
        state = lorgar.effect_wire_state(lorgar.EffectSettings(mode=1, speed=3), 4, (0, 80, 255))
        self.assertEqual(lorgar.build_set(*state), WAVE_BLUE)
        self.assertEqual(WAVE_BLUE[9:17].hex(" "), "01 04 02 ff 00 00 50 ff")

    def test_wave_reversed_fastest_multicolour(self):
        settings = lorgar.EffectSettings(mode=1, speed=5, reversed=True, multicolor=True)
        state = lorgar.effect_wire_state(settings, 4, (0, 80, 255))
        self.assertEqual(lorgar.build_set(*state), WAVE_REVERSED_FAST_RAINBOW)
        self.assertEqual(WAVE_REVERSED_FAST_RAINBOW[9:17].hex(" "), "01 04 00 00 01 00 00 00")

    def test_every_effect_builds_and_passes_the_allow_list(self):
        self.assertEqual([effect.mode for effect in lorgar.EFFECTS], [*range(1, 17), 18])
        self.assertEqual(len(lorgar.EFFECTS), 17)
        for effect in lorgar.EFFECTS:
            for reversed_ in (False, True):
                for multicolor in (False, True):
                    settings = lorgar.EffectSettings(effect.mode, 1, reversed_, multicolor)
                    with self.subTest(effect=effect.key, reversed=reversed_, multicolor=multicolor):
                        lorgar._check_packet(lorgar.build_set(*lorgar.effect_wire_state(settings, 2, (9, 8, 7))))

    def test_effects_without_colour_always_go_out_multicolour_and_black(self):
        no_colour = [effect for effect in lorgar.EFFECTS if not effect.has_color]
        self.assertEqual([effect.mode for effect in no_colour], [4, 10, 11, 12, 18])
        for effect in no_colour:
            for multicolor in (False, True):
                state = lorgar.effect_wire_state(lorgar.EffectSettings(effect.mode, 3, False, multicolor), 4, (10, 20, 30))
                with self.subTest(effect=effect.key, multicolor=multicolor):
                    self.assertEqual(state[4:], (1, (0, 0, 0)))

    def test_colour_effects_use_the_colour_unless_multicolour(self):
        state = lorgar.effect_wire_state(lorgar.EffectSettings(5, 3, False, False), 3, (10, 20, 30))
        self.assertEqual(state, (5, 3, 2, 0xFF, 0, (10, 20, 30)))
        state = lorgar.effect_wire_state(lorgar.EffectSettings(5, 3, False, True), 3, (10, 20, 30))
        self.assertEqual(state, (5, 3, 2, 0xFF, 1, (0, 0, 0)))

    def test_static_never_uses_speed_direction_or_multicolour(self):
        settings = lorgar.EffectSettings(mode=6, speed=5, reversed=True, multicolor=True)
        self.assertEqual(lorgar.effect_wire_state(settings, 4, (1, 2, 3)), (6, 4, 2, 0xFF, 0, (1, 2, 3)))

    def test_no_speed_sends_0x02_and_no_direction_sends_0xff(self):
        palette = lorgar.effect_wire_state(lorgar.EffectSettings(4, 5, True, False), 4, (0, 0, 0))
        self.assertEqual(palette, (4, 4, 2, 0x00, 1, (0, 0, 0)))  # Colour palette: no speed, has direction.
        breathing = lorgar.effect_wire_state(lorgar.EffectSettings(5, 1, True, False), 4, (1, 1, 1))
        self.assertEqual(breathing, (5, 4, 4, 0xFF, 0, (1, 1, 1)))  # Breathing: no direction.

    def test_speed_mapping(self):
        for step, wire in {1: 4, 2: 3, 3: 2, 4: 1, 5: 0, 0: 4, 9: 0}.items():
            with self.subTest(step=step):
                self.assertEqual(lorgar.speed_byte(step), wire)

    def test_builder_refuses_what_the_allow_list_refuses(self):
        bad = {
            "mode 0": (0, 4, 2, 0xFF, 0, (0, 0, 0)),
            "mode 17": (17, 4, 2, 0xFF, 0, (0, 0, 0)),
            "mode 19": (19, 4, 2, 0xFF, 0, (0, 0, 0)),
            "speed 5": (1, 4, 5, 0xFF, 0, (0, 0, 0)),
            "direction 0x01": (1, 4, 2, 0x01, 0, (0, 0, 0)),
            "multicolour 2": (1, 4, 2, 0xFF, 2, (0, 0, 0)),
            "multicolour with a colour": (1, 4, 2, 0xFF, 1, (0, 0, 1)),
            "level 5": (1, 5, 2, 0xFF, 0, (0, 0, 0)),
            "negative speed": (1, 4, -1, 0xFF, 0, (0, 0, 0)),
        }
        for name, args in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                lorgar.build_set(*args)

    def test_allow_list_accepts_the_effect_bytes(self):
        for packet in (WAVE_BLUE, WAVE_REVERSED_FAST_RAINBOW,
                       lorgar.build_set(18, 0, 4, 0x00, 1, (0, 0, 0))):
            lorgar._check_packet(packet)


class EffectSettingsTests(unittest.TestCase):
    def test_missing_fields_are_the_defaults(self):
        self.assertEqual(lorgar.effect_settings(KEYBOARD), lorgar.EffectSettings(6, 3, False, False))
        self.assertEqual(lorgar.effect_settings(None), lorgar.EffectSettings())

    def test_saved_fields_are_read(self):
        device = wave(lighting_effect_speed=5, lighting_effect_reversed=True, lighting_effect_multicolor=True)
        self.assertEqual(lorgar.effect_settings(device), lorgar.EffectSettings(1, 5, True, True))
        self.assertEqual(lorgar.effect_settings({**KEYBOARD, "lighting_effect": " Colour_Palette "}).mode, 4)
        self.assertEqual(lorgar.effect_settings({**KEYBOARD, "lighting_effect": "acceleration"}).mode, 18)

    def test_invalid_values_fall_back(self):
        device = {**KEYBOARD, "lighting_effect": "disco", "lighting_effect_speed": 9,
                  "lighting_effect_reversed": "yes", "lighting_effect_multicolor": 1}
        self.assertEqual(lorgar.effect_settings(device), lorgar.EffectSettings())
        for speed in (0, 6, -1, True, "fast", None, 2.5, [3]):
            with self.subTest(speed=speed):
                self.assertEqual(lorgar.effect_settings(wave(lighting_effect_speed=speed)).speed, 3)
        self.assertEqual(lorgar.effect_settings(wave(lighting_effect_speed="4")).speed, 4)

    def test_descriptor_round_trips_through_device_and_options(self):
        saved = Device.from_dict(wave(lighting_effect_speed=1, lighting_effect_reversed=True))
        reloaded = Device.from_dict(saved.to_dict())
        self.assertEqual(lorgar.effect_settings(reloaded), lorgar.EffectSettings(1, 1, True, False))
        options = lorgar.lighting_options(reloaded)
        assert options.effects is not None
        self.assertEqual((options.effects.effect, options.effects.speed, options.effects.reversed,
                          options.effects.multicolor), ("wave", 1, True, False))

    def test_options_list_the_effects_in_mode_order(self):
        effects = lorgar.lighting_options(KEYBOARD).effects
        assert effects is not None
        self.assertEqual(effects.effect, "static")
        self.assertEqual((effects.default_effect, effects.default_speed, effects.speed_steps), ("static", 3, 5))
        self.assertEqual([choice.name for choice in effects.choices], [
            "Wave", "Cloud", "Vortex", "Colour palette", "Breathing", "Static", "Reaction", "Stone",
            "Traverse", "Stars", "Firework", "Scrolling", "Wave motion", "Cartoon", "Rain", "Scanning",
            "Acceleration",
        ])
        by_key = {choice.key: choice for choice in effects.choices}
        self.assertFalse(by_key["static"].multicolor or by_key["static"].has_speed or by_key["static"].has_direction)
        self.assertEqual(by_key["wave"].directions, ("Left to right", "Right to left"))
        self.assertTrue(by_key["wave"].multicolor and by_key["wave"].has_speed)
        self.assertFalse(by_key["stars"].multicolor or by_key["stars"].has_direction)
        self.assertTrue(by_key["stars"].has_speed)
        self.assertFalse(by_key["colour_palette"].has_speed or by_key["colour_palette"].multicolor)
        self.assertEqual(by_key["colour_palette"].directions, ("Outside", "Inside"))


class EffectSequenceTests(LorgarTestCase):
    def test_a_descriptor_effect_is_used_from_the_first_command(self):
        self.adapter(wave()).set_color(0, 80, 255)
        self.assertEqual(self.keyboard.writes, [BEGIN, WAVE_BLUE, END])

    def test_each_option_change_sends_one_sequence_and_a_repeat_none(self):
        adapter = self.adapter()
        adapter.set_color(0, 80, 255)
        steps = [
            ({"lighting_effect": "wave"}, WAVE_BLUE),
            ({"lighting_effect": "wave", "lighting_effect_speed": 5},
             bytes.fromhex(FIXED + "010400ff00" "0050ff") + bytes(47)),
            ({"lighting_effect": "wave", "lighting_effect_speed": 5, "lighting_effect_reversed": True},
             bytes.fromhex(FIXED + "0104000000" "0050ff") + bytes(47)),
            ({"lighting_effect": "wave", "lighting_effect_speed": 5, "lighting_effect_reversed": True,
              "lighting_effect_multicolor": True}, WAVE_REVERSED_FAST_RAINBOW),
        ]
        for fields, packet in steps:
            with self.subTest(fields=fields):
                self.keyboard.writes.clear()
                adapter.update_device({**KEYBOARD, **fields})
                self.assertEqual(self.keyboard.writes, [])  # Taking the descriptor sends nothing.
                adapter.reapply()
                self.assertEqual(self.keyboard.writes, [BEGIN, packet, END])
                adapter.reapply()
                adapter.update_device({**KEYBOARD, **fields})
                adapter.reapply()
                self.assertEqual(self.keyboard.writes, [BEGIN, packet, END])

    def test_reapply_never_opens_the_keyboard(self):
        adapter = self.adapter()
        adapter.update_device(wave())
        adapter.reapply()
        self.assertEqual(self.keyboard.events, [])
        self.assertEqual(self.keyboard.writes, [])
        adapter.set_color(0, 80, 255)  # The first command shows the chosen effect.
        self.assertEqual(self.keyboard.sets(), [WAVE_BLUE])

    def test_power_off_is_static_black_and_on_restores_the_effect(self):
        adapter = self.adapter(wave())
        adapter.set_color(0, 80, 255)
        self.keyboard.writes.clear()
        adapter.set_power(False)
        self.assertEqual(self.keyboard.sets(), [set_packet(0, 0, 0, 0)])
        adapter.reapply()  # Off: an option re-apply sends nothing.
        self.assertEqual(len(self.keyboard.sets()), 1)
        adapter.set_power(True)
        self.assertEqual(self.keyboard.sets(), [set_packet(0, 0, 0, 0), WAVE_BLUE])

    def test_back_to_static_restores_todays_packet(self):
        adapter = self.adapter(wave())
        adapter.set_color(0, 255, 0)
        adapter.update_device(dict(KEYBOARD))
        adapter.reapply()
        self.assertEqual(self.keyboard.sets()[-1], GREEN)

    def test_an_edited_descriptor_for_another_keyboard_is_ignored(self):
        adapter = self.adapter()
        adapter.update_device({**KEYBOARD, "usb_product_id": 0x1234, "lighting_effect": "wave"})
        adapter.set_color(0, 255, 0)
        self.assertEqual(self.keyboard.sets(), [GREEN])

    def test_still_manual_only(self):
        adapter = self.adapter(wave())
        self.assertFalse(adapter.capabilities.supports_streaming)
        with self.assertRaises(RuntimeError):
            adapter.begin_stream()
        with self.assertRaises(RuntimeError):
            adapter.set_segments([(1, 2, 3)])
        self.assertEqual(self.keyboard.writes, [])


class DiscoveryTests(LorgarTestCase):
    def test_discover_lists_the_lighting_collection_without_opening_it(self):
        self.assertEqual(lorgar.discover(), [{
            "device_id": "lorgar-320f-5075",
            "name": "Lorgar AZAR",
            "model": "Lorgar AZAR",
            "transport": "usb",
            "type": "lorgar_keyboard",
            "device_kind": "keyboard",
            "usb_vendor_id": 0x320F,
            "usb_product_id": 0x5075,
        }])
        self.assertTrue(lorgar.device_present(KEYBOARD))
        self.assertEqual(self.keyboard.events, [])
        self.assertEqual(self.keyboard.writes, [])

    def test_only_the_ff1c_0092_collection_counts(self):
        self.keyboard.infos = [info for info in COLLECTIONS if info.path != PATH]
        self.assertEqual(lorgar.discover(), [])
        self.assertFalse(lorgar.device_present(KEYBOARD))
        self.assertEqual(self.keyboard.events, [])

    def test_opening_uses_the_ff1c_0092_collection(self):
        self.adapter().set_color(0, 255, 0)
        self.assertIn(("stream", PATH), self.keyboard.events)

    def test_enumeration_errors_are_not_fatal(self):
        self.keyboard.enumerate_error = OSError("no HID")
        with self.assertLogs("lumisync_lorgar", level="WARNING"):
            self.assertEqual(lorgar.discover(), [])
        self.assertFalse(lorgar.device_present(KEYBOARD))

    def test_unknown_product_is_never_present(self):
        self.assertFalse(lorgar.device_present({**KEYBOARD, "usb_product_id": 0x1234}))
        with self.assertRaises(RuntimeError):
            self.adapter({**KEYBOARD, "usb_product_id": 0x1234}).set_color(1, 2, 3)
        self.assertEqual(self.keyboard.events, [])

    def test_scan_this_pc_finds_the_keyboard(self):
        self.assertIn("lorgar_keyboard", devices.LOCAL_HARDWARE_DRIVERS)
        # Only this driver: the others would list the real PC's HID devices.
        with patch.object(devices, "LOCAL_HARDWARE_DRIVERS", ("lorgar_keyboard",)):
            found = devices.discover_local_hardware()
        self.assertIn(KEYBOARD["device_id"], [device.get("device_id") for device in found])
        self.assertEqual(self.keyboard.events, [])


class RegistryTests(LorgarTestCase):
    def test_registry_builds_the_keyboard_adapter(self):
        adapter = registry.create_adapter(KEYBOARD)
        self.addCleanup(adapter.close)
        self.assertIsInstance(adapter, lorgar.LorgarKeyboardAdapter)
        self.assertTrue(registry.is_supported_device(KEYBOARD))
        self.assertTrue(registry.is_supported_device({"type": "lorgar_keyboard"}))
        options = registry.lighting_options(KEYBOARD)
        self.assertIsNotNone(options)
        assert options is not None
        self.assertEqual(options.title, "Keyboard lighting")
        self.assertFalse(options.color_correction)
        self.assertFalse(options.has_layout)
        self.assertTrue(options.has_effects)
        self.assertEqual(registry.LORGAR_KEYBOARD_TYPE, "lorgar_keyboard")

    def test_type_alone_means_usb(self):
        device = Device.from_dict({"type": "lorgar_keyboard", "device_id": "lorgar-320f-5075"})
        self.assertEqual(device.transport, "usb")
        self.assertTrue(device.is_local_hardware)

    def test_presence_only_enumerates(self):
        self.assertEqual(registry.local_hardware_presence(KEYBOARD), (True, lorgar.NOT_FOUND_MESSAGE))
        self.keyboard.infos = []
        self.assertEqual(registry.local_hardware_presence(KEYBOARD), (False, lorgar.NOT_FOUND_MESSAGE))
        self.assertEqual(self.keyboard.events, [])

    def test_pool_shares_one_adapter(self):
        self.addCleanup(pool.close, KEYBOARD)
        first = pool.acquire(KEYBOARD)
        self.assertIs(pool.acquire(dict(KEYBOARD, name="Renamed")), first)
        self.assertIsInstance(first, lorgar.LorgarKeyboardAdapter)


class SyncExclusionTests(LorgarTestCase):
    def test_capabilities_say_manual_only(self):
        cap = registry.capabilities_for_device(KEYBOARD)
        self.assertFalse(cap.supports_streaming)
        self.assertFalse(cap.supports_segments)
        self.assertFalse(cap.supports_white)
        self.assertTrue(cap.supports_power and cap.supports_brightness and cap.supports_color)
        self.assertEqual(cap.segment_count, 1)

    def test_stream_entry_points_refuse_without_touching_the_keyboard(self):
        adapter = self.adapter()
        with self.assertRaises(RuntimeError) as caught:
            adapter.begin_stream()
        self.assertEqual(str(caught.exception), lorgar.NO_SYNC_MESSAGE)
        with self.assertRaises(RuntimeError):
            adapter.set_segments([(0, 255, 0)])
        self.assertEqual(self.keyboard.events, [])
        self.assertEqual(self.keyboard.writes, [])

    def test_sync_pickers_leave_the_keyboard_out(self):
        strip = {"mac": "fixture-strip", "name": "Desk strip", "model": "H619C", "transport": "lan",
                 "ip": "192.0.2.10"}
        inventory = [strip, KEYBOARD]
        # The filter the Monitor and Music pages, groups and the sync start use.
        streamable = [d for d in inventory if registry.capabilities_for_device(d).supports_streaming]
        self.assertEqual(streamable, [strip])


SYNC = {**KEYBOARD, "lighting_sync_enabled": True}
RED = (255, 0, 0)


class SyncOptInTests(LorgarTestCase):
    def test_only_a_true_flag_turns_sync_on(self):
        for value in (None, False, "yes", 1, "true"):
            with self.subTest(value=value):
                device = {**KEYBOARD, "lighting_sync_enabled": value}
                self.assertFalse(lorgar.sync_enabled(device))
                self.assertFalse(registry.capabilities_for_device(device).supports_streaming)
                adapter = self.adapter(device)
                self.assertFalse(adapter.capabilities.supports_streaming)
                with self.assertRaises(RuntimeError) as caught:
                    adapter.begin_stream()
                self.assertEqual(str(caught.exception), lorgar.NO_SYNC_MESSAGE)
                with self.assertRaises(RuntimeError):
                    adapter.set_segments([(0, 255, 0)])
        self.assertEqual(self.keyboard.events, [])
        self.assertIn("Monitor and Music sync", lorgar.NO_SYNC_MESSAGE)

    def test_flag_on_makes_a_one_colour_sync_target(self):
        adapter = self.adapter(SYNC)
        for cap in (adapter.capabilities, registry.capabilities_for_device(SYNC),
                    lorgar.LorgarKeyboardAdapter.capabilities_for(SYNC)):
            self.assertTrue(cap.supports_streaming)
            self.assertFalse(cap.supports_segments)
            self.assertEqual(cap.segment_count, 1)
        strip = {"mac": "fixture-strip", "name": "Desk strip", "model": "H619C", "transport": "lan",
                 "ip": "192.0.2.10"}
        inventory = [strip, SYNC]
        streamable = [d for d in inventory if registry.capabilities_for_device(d).supports_streaming]
        self.assertEqual(streamable, [strip, SYNC])
        # An edited descriptor switches it back and forth on the same adapter.
        adapter.update_device(dict(KEYBOARD))
        self.assertFalse(adapter.capabilities.supports_streaming)
        adapter.update_device(dict(SYNC))
        self.assertTrue(adapter.capabilities.supports_streaming)

    def test_interval_keys(self):
        expected = {"10s": 10.0, "2s": 2.0, "fast": 0.25, None: 10.0, "nonsense": 10.0, " 2S ": 2.0}
        for key, seconds in expected.items():
            with self.subTest(key=key):
                self.assertEqual(lorgar.sync_interval_seconds({**SYNC, "lighting_sync_interval": key}), seconds)
        self.assertEqual(lorgar.sync_interval_key({**SYNC, "lighting_sync_interval": "nonsense"}), "10s")

    def test_options_carry_the_switch_and_the_warning(self):
        options = registry.lighting_options({**SYNC, "lighting_sync_interval": "fast"})
        assert options is not None and options.sync is not None
        sync = options.sync
        self.assertTrue(sync.enabled)
        self.assertEqual(sync.interval, "fast")
        self.assertEqual([c.label for c in sync.choices], [
            "Every 10 seconds (least wear)", "Every 2 seconds", "Up to 4 a second (most wear)"])
        self.assertEqual(sync.confirm_title, "Allow sync for this keyboard?")
        self.assertEqual((sync.confirm_accept, sync.confirm_reject), ("Allow sync", "Cancel"))
        self.assertTrue(sync.description.startswith("Off by default."))
        off = registry.lighting_options(KEYBOARD)
        assert off is not None and off.sync is not None
        self.assertFalse(off.sync.enabled)
        self.assertEqual(off.sync.interval, "10s")


class StreamTests(LorgarTestCase):
    def stream(self, device=None, *, manual=None):
        adapter = self.adapter(device or SYNC)
        if manual is not None:
            adapter.set_color(*manual)
            self.clock.now += 100  # Well past any interval.
        self.keyboard.writes.clear()
        adapter.begin_stream()
        return adapter

    def test_begin_and_end_without_frames_never_touch_the_keyboard(self):
        adapter = self.stream()
        self.assertEqual(self.keyboard.events, [])
        with self.assertLogs("lumisync_lorgar", level="INFO") as logs:
            adapter.end_stream()
        self.assertEqual(self.keyboard.events, [])
        self.assertIn("Lorgar keyboard: 0 lighting writes during sync", logs.output[0])

    def test_first_frame_writes_the_average_as_static(self):
        adapter = self.stream()
        adapter.set_segments([(200, 0, 0), (0, 0, 100)])
        self.assertEqual(self.keyboard.writes, [BEGIN, set_packet(4, 100, 0, 50), END])
        self.assertEqual(self.clock.waits, [])

    def test_first_frame_equal_to_what_the_keyboard_shows_is_skipped(self):
        adapter = self.stream(manual=(0, 255, 0))
        adapter.set_segments([(0, 255, 0)])
        self.assertEqual(self.keyboard.writes, [])

    def test_frames_inside_the_interval_are_dropped_without_waiting(self):
        adapter = self.stream()
        adapter.set_segments([RED])
        for _ in range(20):
            self.clock.now += 0.45
            adapter.set_segments([(0, 0, 255)])
        self.assertEqual(self.keyboard.sets(), [set_packet(4, 255, 0, 0)])
        self.assertEqual(self.clock.waits, [])
        self.clock.now += 1.5  # Past 10 s since the first write.
        adapter.set_segments([(0, 0, 255)])
        self.assertEqual(self.keyboard.sets()[-1], set_packet(4, 0, 0, 255))
        self.assertEqual(self.clock.waits, [])

    def test_small_colour_changes_never_write(self):
        adapter = self.stream()
        adapter.set_segments([(100, 100, 100)])
        self.clock.now += 10
        adapter.set_segments([(123, 77, 100)])  # 23 on two channels.
        adapter.set_segments([(100, 100, 100)])  # Identical.
        self.assertEqual(len(self.keyboard.sets()), 1)
        adapter.set_segments([(100, 124, 100)])  # 24 on one channel.
        self.assertEqual(self.keyboard.sets()[-1], set_packet(4, 100, 124, 100))

    def test_each_interval_is_respected(self):
        for key, seconds in (("10s", 10.0), ("2s", 2.0), ("fast", 0.25)):
            with self.subTest(key=key):
                self._reset()
                self.keyboard.writes.clear()
                adapter = self.stream({**SYNC, "lighting_sync_interval": key})
                adapter.set_segments([RED])
                self.clock.now += seconds - 0.01
                adapter.set_segments([(0, 0, 255)])
                self.assertEqual(len(self.keyboard.sets()), 1)
                self.clock.now += 0.02
                adapter.set_segments([(0, 0, 255)])
                self.assertEqual(len(self.keyboard.sets()), 2)
                self.assertEqual(self.clock.waits, [])
                adapter.close()

    def test_a_failed_write_is_not_retried_before_the_interval(self):
        adapter = self.stream()
        self.keyboard.script = ["silent"]  # BEGIN gets no answer.
        with self.assertRaises(RuntimeError) as caught:
            adapter.set_segments([RED])
        self.assertEqual(str(caught.exception), lorgar.NO_REPLY_MESSAGE)
        attempts = len(self.keyboard.writes)
        for _ in range(10):
            self.clock.now += 0.5
            adapter.set_segments([RED])
        self.assertEqual(len(self.keyboard.writes), attempts)
        self.clock.now += 5.0
        adapter.set_segments([RED])
        self.assertEqual(self.keyboard.sets()[-1], set_packet(4, 255, 0, 0))

    def test_power_off_during_a_stream_writes_black_and_stops_frames(self):
        adapter = self.stream()
        adapter.set_segments([RED])
        self.clock.now += 1
        adapter.set_power(False)
        self.assertEqual(self.keyboard.sets()[-1], set_packet(0, 0, 0, 0))
        count = len(self.keyboard.writes)
        for _ in range(5):
            self.clock.now += 20
            adapter.set_segments([(0, 0, 255)])
        self.assertEqual(len(self.keyboard.writes), count)
        adapter.set_power(True)  # Not written: the next due frame shows it.
        self.assertEqual(len(self.keyboard.writes), count)
        adapter.set_segments([(0, 0, 255)])
        self.assertEqual(self.keyboard.sets()[-1], set_packet(4, 0, 0, 255))

    def test_stream_end_restores_the_manual_effect_with_one_sequence(self):
        adapter = self.stream(wave(lighting_sync_enabled=True), manual=(0, 80, 255))
        adapter.set_segments([RED])
        self.clock.now += 10
        adapter.set_segments([(0, 255, 0)])
        self.assertEqual(self.keyboard.sets(), [set_packet(4, 255, 0, 0), GREEN])
        self.keyboard.writes.clear()
        with self.assertLogs("lumisync_lorgar", level="INFO") as logs:
            adapter.end_stream()
        self.assertEqual(self.keyboard.writes, [BEGIN, WAVE_BLUE, END])
        self.assertTrue(any("Lorgar keyboard: 2 lighting writes during sync" in line for line in logs.output))
        adapter.end_stream()  # A second end does nothing.
        self.assertEqual(len(self.keyboard.writes), 3)

    def test_stream_end_without_manual_state_keeps_the_last_sync_colour(self):
        # No manual colour, brightness or power since the app started: the
        # remembered state is only the white default, so nothing is restored.
        adapter = self.stream()
        adapter.set_segments([RED])
        self.assertEqual(self.keyboard.sets(), [set_packet(4, 255, 0, 0)])
        self.keyboard.writes.clear()
        self.clock.now += 10
        with self.assertLogs("lumisync_lorgar", level="INFO") as logs:
            adapter.end_stream()
        self.assertEqual(self.keyboard.writes, [])
        self.assertTrue(any("Lorgar keyboard: 1 lighting writes during sync" in line for line in logs.output))

    def test_stream_end_restores_a_manual_colour_given_during_the_stream(self):
        adapter = self.stream()
        adapter.set_segments([RED])
        adapter.set_color(0, 255, 0)  # Held until the stream ends.
        self.keyboard.writes.clear()
        self.clock.now += 10
        adapter.end_stream()
        self.assertEqual(self.keyboard.writes, [BEGIN, GREEN, END])

    def test_manual_changes_during_a_stream_wait_for_its_end(self):
        adapter = self.stream(manual=(0, 255, 0))
        adapter.set_segments([RED])
        self.keyboard.writes.clear()
        adapter.set_color(0, 0, 255)
        adapter.set_brightness(50)
        adapter.update_device(wave(lighting_sync_enabled=True))
        adapter.reapply()
        self.assertEqual(self.keyboard.writes, [])
        self.clock.now += 1
        adapter.end_stream()
        self.assertEqual(self.keyboard.writes, [BEGIN, bytes.fromhex(FIXED + "010202ff00" "0000ff") + bytes(47), END])

    def test_turning_sync_off_mid_stream_gives_the_keyboard_back(self):
        adapter = self.stream(manual=(0, 255, 0))
        adapter.set_segments([RED])
        self.clock.now += 1
        adapter.update_device(dict(KEYBOARD))
        with self.assertRaises(RuntimeError) as caught:
            adapter.set_segments([RED])
        self.assertEqual(str(caught.exception), lorgar.NO_SYNC_MESSAGE)
        self.assertEqual(self.keyboard.sets()[-1], GREEN)
        count = len(self.keyboard.writes)
        with self.assertRaises(RuntimeError):
            adapter.set_segments([RED])
        adapter.end_stream()
        self.assertEqual(len(self.keyboard.writes), count)

    def test_manual_controls_work_as_before_outside_a_stream(self):
        adapter = self.adapter(SYNC)
        adapter.set_color(0, 255, 0)
        self.assertEqual(self.keyboard.sets(), [GREEN])


if __name__ == "__main__":
    unittest.main()
