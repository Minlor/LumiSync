import base64
import json
import unittest
import unittest.mock as mock

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
from lumisync.drivers import tuya_aoi, tuya_presence
from lumisync.drivers.tuya_lan import (
    PAINT_MAX_PAYLOAD,
    TuyaLightAdapter,
    build_color_command,
    encode_brightness,
    encode_colour,
    encode_paint_all,
    encode_paint_clear,
    encode_paint_single,
    encode_paint_strokes,
    encode_temperature,
    paint_zone_capacity,
    paint_zone_hint,
    reduce_palette,
    rgb_to_hsv_tuya,
    status_enrichment,
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

    def test_razer_payload_declares_its_variable_data_length(self):
        for count in (3, 4, 10, 14):
            with self.subTest(count=count):
                adapter = self._adapter(
                    {"ip": "192.168.0.10", "segment_count_override": count}
                )
                adapter.set_segments([(index, index + 1, index + 2) for index in range(count)])
                payload = json.loads(adapter.server.sent[0][0])
                blob = base64.b64decode(payload["msg"]["data"]["pt"])

                declared_length = int.from_bytes(blob[1:3], "big")
                self.assertEqual(declared_length, 2 + count * 3)
                self.assertEqual(declared_length, len(blob) - 5)

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

    def test_end_stream_disables_razer_mode_with_b1_off(self):
        adapter = self._adapter()

        adapter.end_stream()

        payload = json.loads(adapter.server.sent[0][0])
        blob = base64.b64decode(payload["msg"]["data"]["pt"])
        self.assertEqual(blob, bytes([0xBB, 0x00, 0x01, 0xB1, 0x00, 0x0B]))

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

    def test_tuya_adapter_survives_discovery_field_changes(self):
        from unittest.mock import patch

        class Fake:
            def __init__(self, device, *a, **k):
                self.device = dict(device)

            def close(self):
                pass

        with patch("lumisync.drivers.pool.create_adapter", side_effect=lambda d, server=None: Fake(d)):
            device = {"transport": "tuya", "device_id": "dev1", "ip": "1.2.3.4",
                      "local_key_ref": "tuya-local/dev1"}
            first = self.pool.acquire(device)
            refreshed = dict(device, colour_format="hsv", tuya_paint_dp=61,
                             tuya_paint_zones=20)
            self.assertIs(self.pool.acquire(refreshed), first)

    def test_tuya_adapter_recreates_when_the_connection_target_changes(self):
        from unittest.mock import patch

        closed = {"v": 0}

        class Fake:
            def __init__(self, device, *a, **k):
                self.device = dict(device)

            def close(self):
                closed["v"] += 1

        with patch("lumisync.drivers.pool.create_adapter", side_effect=lambda d, server=None: Fake(d)):
            device = {"transport": "tuya", "device_id": "dev1", "ip": "1.2.3.4"}
            first = self.pool.acquire(device)
            moved = dict(device, ip="5.6.7.8")
            second = self.pool.acquire(moved)
            self.assertIsNot(first, second)
            self.assertEqual(closed["v"], 1)


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
        self.echo = None  # optional dict of DP values surfaced via status()

    def set_value(self, dp, value, nowait=False):
        self.single.append((dp, value))

    def set_multiple_values(self, mapping):
        self.multiple.append(dict(mapping))

    def status(self):
        if self.echo is None:
            return {"Error": "no cached state"}
        return {"dps": dict(self.echo)}


class FakeTinytuyaDevice(FakeTuyaHandle):
    """Stands in for tinytuya.Device: remembers where it was opened."""

    def __init__(self, dev_id, address=None, local_key=None):
        super().__init__()
        self.address = address
        self.closed = False

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

    def close(self):
        self.closed = True


class TuyaEncoderTests(unittest.TestCase):
    def test_hsv_units_are_h360_s1000_v1000(self):
        self.assertEqual(rgb_to_hsv_tuya(255, 0, 0), (0, 1000, 1000))
        self.assertEqual(rgb_to_hsv_tuya(0, 255, 0), (120, 1000, 1000))
        self.assertEqual(rgb_to_hsv_tuya(0, 0, 255), (240, 1000, 1000))
        self.assertEqual(rgb_to_hsv_tuya(255, 255, 255), (0, 0, 1000))

    def test_colour_data_hsv_golden(self):
        self.assertEqual(encode_colour(255, 0, 0), "000003e803e8")
        self.assertEqual(encode_colour(0, 255, 0), "007803e803e8")
        self.assertEqual(encode_colour(0, 0, 255), "00f003e803e8")
        self.assertEqual(encode_colour(255, 255, 255), "0000000003e8")

    def test_colour_data_legacy_golden(self):
        self.assertEqual(encode_colour(255, 0, 0, schema="legacy"), "ff00000000ffff")
        self.assertEqual(encode_colour(0, 255, 0, schema="legacy"), "00ff000078ffff")
        self.assertEqual(encode_colour(255, 255, 255, schema="legacy"), "ffffff000000ff")
        # The historic v1/v2 aliases keep working.
        self.assertEqual(encode_colour(255, 0, 0, schema="v1"), "ff00000000ffff")
        self.assertEqual(encode_colour(0, 0, 255, schema="v2"), "00f003e803e8")

    def test_brightness_scaling(self):
        self.assertEqual([encode_brightness(p) for p in (0, 50, 100)], [10, 505, 1000])
        self.assertEqual(
            [encode_brightness(p, schema="legacy") for p in (0, 50, 100)], [25, 140, 255]
        )

    def test_temperature_scaling(self):
        self.assertEqual(encode_temperature(2700, 2700, 6500), 0)
        self.assertEqual(encode_temperature(6500, 2700, 6500), 1000)
        self.assertEqual(encode_temperature(6500, 2700, 6500, schema="legacy"), 255)
        self.assertEqual(encode_temperature(4000, 2700, 2700), 0)  # degenerate range

    def test_build_color_command_switches_to_colour_mode(self):
        self.assertEqual(
            build_color_command({}, 255, 0, 0), {21: "colour", 24: "000003e803e8"}
        )
        self.assertEqual(
            build_color_command({"dp_schema": "v1"}, 0, 255, 0),
            {2: "colour", 5: "00ff000078ffff"},
        )

    def test_dd_family_layout_detects_modern_devices_by_dp_id(self):
        # LSC "dd"-family strips keep the legacy code names at modern DP ids.
        device = {"tuya_functions": {code: {"dp_id": dp} for code, dp in (
            ("switch_led", 20), ("work_mode", 21), ("bright_value", 22),
            ("temp_value", 23), ("colour_data", 24))}}
        self.assertEqual(
            build_color_command(device, 255, 0, 0), {21: "colour", 24: "000003e803e8"}
        )


class EchoTuyaHandle(FakeTuyaHandle):
    """FakeTuyaHandle that mirrors one DP (the paint DP) back via status()."""

    def __init__(self):
        super().__init__()
        self.echo = {}

    def set_value(self, dp, value, nowait=False):
        super().set_value(dp, value)
        if dp == self.paint_dp:
            self.echo[str(dp)] = value

    paint_dp = 61


class TuyaPaintTests(unittest.TestCase):
    def test_paint_all_golden_matches_battletron_state(self):
        # A Battletron controller reports DP 61 = AAEAFAAAAAPoA+g= for a full
        # red smear (version 0, colour, 20 zones 0x14, H0 S1000 V1000).
        self.assertEqual(encode_paint_all(255, 0, 0), "AAEAFAAAAAPoA+g=")

    def test_paint_single_golden_matches_the_lsc_app(self):
        # Byte-verified against the LSC-app capture (frame I1): one stroke
        # paints 7 zones with singleData 0x87. The sat field lands at 808
        # instead of the app's 809 because of the RGB<->HSV round trip (±1).
        frame = encode_paint_single([17, 18, 7, 4, 12, 14, 5],
                                    *rgb_of(1, 809, 1000), zones=20)
        self.assertEqual(
            base64.b64decode(frame),
            bytes.fromhex("0001011401" "0001032803e8" "87" "12130805 0d0f06".replace(" ", "")),
        )

    def test_paint_clear_golden_matches_the_lsc_app(self):
        # App frame G5: clear 8 zones (singleData 0x88).
        frame = encode_paint_clear([18, 17, 16, 15, 13, 14, 7, 3], zones=20)
        self.assertEqual(
            base64.b64decode(frame),
            bytes.fromhex("0001011402" "000000000000" "88" "13121110 0e0f0804".replace(" ", "")),
        )

    def test_strokes_merge_adjacent_zones_sharing_a_colour(self):
        strokes = encode_paint_strokes([(255, 0, 0), (255, 0, 0), (0, 255, 0)])
        self.assertEqual(len(strokes), 2)
        first, second = (base64.b64decode(s) for s in strokes)
        self.assertEqual(first[11], 0x82)  # 2 zones merge into one stroke
        self.assertEqual(first[12:], bytes([1, 2]))
        self.assertEqual(second[12:], bytes([3]))

    def test_paint_strokes_respects_the_128_byte_dp_payload_cap(self):
        self.assertEqual(paint_zone_capacity(), 20)
        colors = [rgb_of((i * 137) % 360, 500 + (i * 37) % 500, 1000) for i in range(30)]
        strokes = encode_paint_strokes(colors)
        self.assertTrue(strokes)
        for stroke in strokes:
            self.assertLessEqual(len(base64.b64decode(stroke)), PAINT_MAX_PAYLOAD)

    def test_reduce_palette_limits_distinct_colours(self):
        colors = [(i * 12 % 256, 40, 200) for i in range(20)]
        reduced = reduce_palette(colors, 4)
        self.assertEqual(len(reduced), 20)
        self.assertLessEqual(len(set(reduced)), 4)
        self.assertTrue(set(reduced) <= set(colors))

    def test_reduce_palette_keeps_small_frames_untouched(self):
        colors = [(255, 0, 0), (0, 255, 0)]
        self.assertEqual(reduce_palette(colors, 5), colors)


    def test_black_zones_use_clear_frames_like_the_app(self):
        # The LSC app has no black paint; it clears. A zero-value stroke was
        # left showing the old colour on the real strip.
        strokes = encode_paint_strokes([(255, 0, 0), (0, 0, 0), (0, 0, 0)], zones=3)
        self.assertEqual(len(strokes), 2)
        self.assertEqual(base64.b64decode(strokes[1]),
                         base64.b64decode(encode_paint_clear([1, 2], zones=3)))

    def test_previous_frame_skips_unchanged_zones(self):
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        self.assertEqual(encode_paint_strokes(colors, zones=3, previous=colors), [])
        strokes = encode_paint_strokes([(255, 0, 0), (0, 0, 0), (0, 0, 255)],
                                       zones=3, previous=colors)
        # Only the blackened middle zone changes; it is cleared, not painted.
        self.assertEqual(len(strokes), 1)
        self.assertEqual(base64.b64decode(strokes[0]),
                         base64.b64decode(encode_paint_clear([1], zones=3)))


def rgb_of(h, s, v):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h / 360, s / 1000, v / 1000)
    return round(r * 255), round(g * 255), round(b * 255)


class TuyaStatusDiscoveryTests(unittest.TestCase):
    """Devices self-describe in status replies; LumiSync reads that and stops
    shipping per-device tables."""

    def test_stroke_and_clear_frames_advertise_their_zone_count(self):
        stroke = encode_paint_single([0, 1, 2], 255, 0, 0)  # zones grid 0x14 by default
        self.assertEqual(paint_zone_hint(stroke), 20)
        self.assertEqual(paint_zone_hint(encode_paint_clear([4])), 20)

    def test_combination_frames_still_hint_a_zone_count(self):
        # Other RGBIC families paint on dimmerMode 3; keep them recognized.
        combo = base64.b64encode(bytes.fromhex("0003000a00" + "000003e803e8" * 10)).decode()
        self.assertEqual(paint_zone_hint(combo), 10)

    def test_non_paint_values_fail_the_signature(self):
        self.assertIsNone(paint_zone_hint(None))
        self.assertIsNone(paint_zone_hint(22))  # numeric DPs, e.g. brightness
        self.assertIsNone(paint_zone_hint("007803e801f4"))  # colour hex string
        self.assertIsNone(paint_zone_hint("ARgBVVXgAABkAABkAAAA"))  # rgbic scene (version 1)
        self.assertIsNone(paint_zone_hint("AAIACgEBLAGQA9SBBw=="))  # colour card (dimmerMode 2)
        self.assertIsNone(paint_zone_hint("not@base64!"))  # undecodable

    def test_status_snapshot_reads_an_lsc_dd_strip(self):
        # Battletron-style state: colour DP 24 plus paint DP 61 announcing 20 zones.
        fields = status_enrichment(
            {"20": True, "21": "colour", "22": 1000, "24": "007803e801f4",
             "61": "AAEAFAAAAAPoA+g="}
        )
        self.assertEqual(fields["dp_schema"], "v2")
        self.assertEqual(fields["colour_format"], "hsv")
        self.assertEqual(fields["tuya_paint_dp"], 61)
        self.assertEqual(fields["tuya_paint_zones"], 20)

    def test_status_snapshot_reads_a_legacy_bulb(self):
        fields = status_enrichment({"1": True, "5": "ff00000000ffff"})
        self.assertEqual(fields["dp_schema"], "v1")
        self.assertEqual(fields["colour_format"], "legacy")
        self.assertNotIn("tuya_paint_dp", fields)

    def test_unknown_paint_dp_is_found_by_its_value_shape(self):
        # The paint DP number varies per product; any data point whose current
        # value has the paint shape is treated as the paint control. The zone
        # hint is the grid size the frame carries (20), not the stroke width.
        frame = encode_paint_single([0, 1, 2], 0, 255, 0)
        fields = status_enrichment({"20": True, "24": "007803e801f4", "62": frame})
        self.assertEqual(fields["tuya_paint_dp"], 62)
        self.assertEqual(fields["tuya_paint_zones"], 20)

    def test_known_candidates_win_over_the_signature_scan(self):
        fields = status_enrichment(
            {"61": encode_paint_all(255, 0, 0), "103": encode_paint_single([0], 0, 0, 255)}
        )
        self.assertEqual(fields["tuya_paint_dp"], 61)
        self.assertEqual(fields["tuya_paint_zones"], 20)


@mock.patch.object(tuya_aoi, "AOI_FAST_STREAM", False)
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

    def test_capabilities_default_to_one_zone_without_paint(self):
        a = self._adapter()
        self.assertEqual(a.capabilities.segment_count, 1)
        self.assertTrue(a.capabilities.supports_segments)

    def test_declared_paint_dp_unlocks_zone_counting(self):
        device = {"ip": "1.2.3.4", "device_id": "d", "local_key": "k",
                  "tuya_paint_dp": 61}
        a = TuyaLightAdapter(device)
        a._dev = FakeTuyaHandle()
        self.assertEqual(a.capabilities.segment_count, 10)
        device["segment_count_override"] = 12
        self.assertEqual(a.capabilities.segment_count, 12)
        device["segment_count_override"] = 99  # capped by the 128-byte payload
        self.assertEqual(a.capabilities.segment_count, paint_zone_capacity())

    def test_query_status_learns_fields_the_device_reports(self):
        device = {"ip": "1.2.3.4", "device_id": "d", "local_key": "k"}
        a = TuyaLightAdapter(device)
        a._dev = FakeTuyaHandle()
        a._dev.echo = {"20": True, "21": "colour", "24": "007803e801f4",
                       "61": "AAEAFAAAAAPoA+g="}
        status = a.query_status()
        self.assertEqual(status["brightness"], 50)
        self.assertEqual(device["dp_schema"], "v2")
        self.assertEqual(device["colour_format"], "hsv")
        self.assertEqual(device["tuya_paint_dp"], 61)
        self.assertEqual(device["tuya_paint_zones"], 20)
        # The zone default follows the device's own answer, not a fixed 10.
        self.assertEqual(a.capabilities.segment_count, 20)

    def test_query_status_keeps_power_and_colour_when_replies_are_change_only(self):
        # The strip pushes paint/switch echoes between DP_QUERY replies; those
        # change-only packets must not blank the last confirmed state.
        a = self._adapter()
        a._dev.echo = {"20": True, "21": "colour", "24": "000003e803e8"}
        first = a.query_status()
        self.assertTrue(first["power_on"])
        self.assertEqual(first["color"], (255, 0, 0))

        a._dev.echo = {"61": "AAEBCgEAYAMdAliDCAkK"}
        second = a.query_status()
        self.assertTrue(second["power_on"])
        self.assertNotIn("color", second)  # a paint echo carries no colour DP
        self.assertEqual(a._last_brightness, 100)

    def test_power_on_restores_a_visible_base_after_paint_left_it_black(self):
        a = self._adapter(tuya_paint_dp=61)
        a._base_black = True
        a._last_colour = (255, 0, 0)  # last colour the user chose
        a.set_power(True)

        self.assertTrue(any(command.get(24) for command in a._dev.multiple))
        self.assertIn((20, True), a._dev.single)
        self.assertFalse(a._base_black)

    def test_power_on_leaves_a_user_chosen_black_base_alone(self):
        a = self._adapter(tuya_paint_dp=61)
        a._base_black = False
        a.set_power(True)
        self.assertEqual(a._dev.single, [(20, True)])
        self.assertFalse(a._dev.multiple)

    def test_brightness_rides_the_remembered_colour_without_a_status_poll(self):
        a = self._adapter(
            tuya_paint_dp=61,
            tuya_functions={
                "switch_led": {"dp_id": 20},
                "work_mode": {"dp_id": 21},
                "colour_data": {"dp_id": 24},
                "paint_colour_data": {"dp_id": 61},
            },
        )
        a._last_colour = (255, 0, 0)
        a._dev.echo = None  # a status poll would fail here
        a.set_brightness(40)
        self.assertEqual(a._dev.multiple, [{21: "colour", 24: "000003e80190"}])

    def test_stream_start_powers_on_and_stop_restores_the_solid_colour(self):
        a = self._adapter(tuya_paint_dp=61)
        a._dev.echo = {"20": False, "21": "colour", "24": "000003e803e8"}
        a.begin_stream()

        self.assertIn((20, True), a._dev.single)
        self.assertIn((24, "000000000000"), a._dev.single)

        a.end_stream()
        clear = encode_paint_clear(list(range(a.capabilities.segment_count)),
                                   zones=a.capabilities.segment_count)
        self.assertIn((61, clear), a._dev.single)
        self.assertEqual(a._dev.single[-1], (24, "000003e803e8"))
        self.assertIsNone(a._last_frame)

    def test_query_status_uses_the_legacy_value_byte_for_brightness(self):
        a = self._adapter(tuya_functions={
            "switch": {"dp_id": 1},
            "work_mode": {"dp_id": 2},
            "colour_data": {"dp_id": 5},
        })
        a._dev.echo = {"1": True, "2": "colour", "5": "ff00000000ff80"}
        status = a.query_status()
        self.assertEqual(status["color"], (255, 0, 0))
        self.assertEqual(status["brightness"], 50)

    def test_query_status_never_overwrites_values_it_already_knows(self):
        device = {"ip": "1.2.3.4", "device_id": "d", "local_key": "k",
                  "colour_format": "legacy"}
        a = TuyaLightAdapter(device)
        a._dev = FakeTuyaHandle()
        a._dev.echo = {"20": True, "21": "colour", "24": "007803e801f4",
                       "61": "AAEAAAAAAABk"}
        a.query_status()
        self.assertEqual(device["colour_format"], "legacy")

    def test_link_health_check_reconnects_after_a_dead_session(self):
        a = self._adapter(tuya_paint_dp=61)
        dead = FakeTuyaHandle()
        dead.echo = None  # status() -> {"Error": ...} -> dead link
        a._dev = dead
        a._last_frame = [(1, 2, 3)] * 10
        events = []

        def fake_close():
            events.append("closed")
            a._dev = None

        def fake_handle():
            events.append("reopened")
            fresh = FakeTuyaHandle()
            fresh.echo = {}
            return fresh

        a._status_dps = lambda: None  # dead link
        a.close = fake_close
        a._handle = fake_handle
        a._ensure_link_alive(dead)
        self.assertEqual(events, ["closed", "reopened"])
        self.assertIsNone(a._last_frame)  # force a full repaint after recovery

    def test_link_health_check_keeps_old_frame_when_alive(self):
        a = self._adapter(tuya_paint_dp=61)
        alive = FakeTuyaHandle()
        alive.echo = {"20": True, "21": "colour"}
        a._dev = alive
        a._last_frame = [(9, 9, 9)] * 10
        a._ensure_link_alive(alive)
        self.assertEqual(a._last_frame, [(9, 9, 9)] * 10)

    def test_begin_stream_survives_a_rejected_command(self):
        class RejectingHandle(FakeTuyaHandle):
            def set_value(self, dp, value, nowait=False):
                super().set_value(dp, value, nowait)
                return {"Error": "Check device key or version"}

        a = self._adapter(tuya_paint_dp=61)
        a._dev = RejectingHandle()
        closed = []
        a.close = lambda: closed.append(True)
        a._handle = lambda: RejectingHandle()
        a.begin_stream()  # must not raise (sync would abort otherwise)
        self.assertTrue(closed)  # one reconnect attempt was made

    def test_segments_stream_one_colour_per_zone_via_paint(self):
        a = self._adapter()
        a._dev = EchoTuyaHandle()  # echoes writes to its paint DP (61)
        colors = [(255, 0, 0)] * 5 + [(0, 0, 255)] * 5  # 10 zones, two runs
        a.set_segments(colors)
        paints = [value for dp, value in a._dev.single if dp == 61]
        # One probe stroke + one stroke per colour run (not per zone).
        self.assertEqual(len(a._dev.single), 3)
        probe, red, blue = (base64.b64decode(v) for v in paints)
        self.assertEqual(probe[4], 1)  # single mode, one zone
        self.assertEqual(probe[11], 0x81)
        self.assertEqual(red, bytes.fromhex("0001010a01" "000003e803e8" "85" "0102030405"))
        self.assertEqual(blue, bytes.fromhex("0001010a01" "00f003e803e8" "85" "060708090a"))
        # No averaging commands on the colour DPs while painting works.
        self.assertEqual(a._dev.multiple, [])

    def test_segments_recognize_the_paint_dp_from_the_cached_state(self):
        a = self._adapter()
        a._dev.echo = {"61": "AAEAFAAAAAPoA+g="}  # Battletron-style state
        a.set_segments([(255, 0, 0)])
        self.assertEqual(a.device["tuya_paint_dp"], 61)
        # No probing writes needed, but the frame itself still streams to DP 61.
        self.assertEqual(len(a._dev.single), 1)
        self.assertEqual(a._dev.single[0][0], 61)
        self.assertEqual(a._dev.multiple, [])

    def test_segments_fall_back_to_averaging_without_a_paint_dp(self):
        a = self._adapter()
        a._dev.status = lambda: {"dps": {"20": True, "21": "colour", "24": "000003e803e8"}}
        a.set_segments([(255, 0, 0), (0, 0, 255)])  # avg -> (127, 0, 127)
        self.assertEqual(len(a._dev.multiple), 1)
        self.assertEqual(a._dev.multiple[0][21], "colour")

    def test_segments_probe_moves_to_another_candidate_dp(self):
        a = self._adapter()
        a._dev = EchoTuyaHandle()
        a._dev.paint_dp = 103  # this device paints on DP 103, not 61
        a.set_segments([(0, 0, 255)])
        paints = [value for dp, value in a._dev.single if dp == 103]
        self.assertTrue(paints)
        self.assertEqual(a.device["tuya_paint_dp"], 103)
        probe = base64.b64decode(paints[0])
        self.assertEqual(probe[:5], bytes.fromhex("0001011401"))  # app-grammar stroke
        self.assertEqual(probe[4], 1)

    def _presence(self, address, age=0.0):
        import time

        registry = tuya_presence.PresenceRegistry()
        registry.note_seen("d", at=time.time() - age, address=address)
        fake = mock.Mock(Device=FakeTinytuyaDevice)
        return (mock.patch.object(tuya_presence, "registry", registry),
                mock.patch("lumisync.drivers.tuya_lan._require_tinytuya", return_value=fake))

    def test_handle_opens_where_a_fresh_presence_beat_announces(self):
        # The router moved the strip to a new DHCP lease; the saved IP is stale.
        a = TuyaLightAdapter({"ip": "192.168.0.44", "device_id": "d", "local_key": "k"})
        beat, tinytuya = self._presence("192.168.0.19")
        with beat, tinytuya:
            handle = a._handle()
        self.assertEqual(handle.address, "192.168.0.19")
        self.assertEqual(a.device["ip"], "192.168.0.19")

    def test_handle_reconnects_when_the_device_moves_mid_session(self):
        a = TuyaLightAdapter({"ip": "192.168.0.44", "device_id": "d", "local_key": "k"})
        old = FakeTinytuyaDevice("d", address="192.168.0.44")
        a._dev = old
        a._last_frame = [(9, 9, 9)] * 10
        beat, tinytuya = self._presence("192.168.0.19")
        with beat, tinytuya:
            handle = a._handle()
            self.assertIs(a._handle(), handle)  # settles on the new session
        self.assertTrue(old.closed)
        self.assertEqual(handle.address, "192.168.0.19")
        self.assertIsNone(a._last_frame)  # repaint every zone at the new address

    def test_handle_keeps_the_saved_ip_without_a_fresh_beat(self):
        a = TuyaLightAdapter({"ip": "192.168.0.44", "device_id": "d", "local_key": "k"})
        current = FakeTinytuyaDevice("d", address="192.168.0.44")
        a._dev = current
        beat, tinytuya = self._presence("192.168.0.19", age=tuya_presence.PRESENCE_FRESH_AFTER + 5)
        with beat, tinytuya:
            self.assertIs(a._handle(), current)
        self.assertFalse(current.closed)
        self.assertEqual(a.device["ip"], "192.168.0.44")

    def test_missing_credentials_raise(self):
        adapter = TuyaLightAdapter({"ip": "1.2.3.4"})  # no id/key
        with self.assertRaises(RuntimeError):
            adapter._handle()

    def test_query_status_decodes_hsv_colour_from_legacy_code_name(self):
        a = self._adapter(tuya_functions={code: {"dp_id": dp} for code, dp in (
            ("switch_led", 20), ("work_mode", 21), ("colour_data", 24))})
        a._dev.echo = {"20": True, "21": "colour", "24": "000003e803e8"}
        status = a.query_status()
        self.assertEqual(status["power_on"], True)
        self.assertEqual(status["color"], (255, 0, 0))
        self.assertEqual(status["brightness"], 100)


if __name__ == "__main__":
    unittest.main()
