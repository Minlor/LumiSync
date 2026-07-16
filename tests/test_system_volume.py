import unittest

from lumisync.sync import system_volume


class CompensateTests(unittest.TestCase):
    def test_unknown_gain_is_passthrough(self):
        bands = (0.02, 0.01, 0.005)
        self.assertEqual(system_volume.compensate(bands, None), bands)

    def test_muted_or_zero_gain_is_passthrough(self):
        # A muted fader reports gain 0; the captured signal is already silence,
        # so dividing it out must not manufacture light from nothing.
        bands = (0.0, 0.0, 0.0)
        self.assertEqual(system_volume.compensate(bands, 0.0), bands)

    def test_known_gain_is_divided_out(self):
        # A fader at ~0.0955 linear (about -20 dB) should be undone by ~10.47x,
        # recovering the pre-fader level.
        out = system_volume.compensate((0.02, 0.01, 0.005), 0.0955)
        for value, expected in zip(out, (0.2094, 0.1047, 0.0524)):
            self.assertAlmostEqual(value, expected, places=3)

    def test_full_volume_is_a_near_noop(self):
        bands = (0.3, 0.15, 0.05)
        out = system_volume.compensate(bands, 1.0)
        for value, original in zip(out, bands):
            self.assertAlmostEqual(value, original, places=6)

    def test_very_low_gain_boost_is_capped(self):
        out = system_volume.compensate((0.001, 0.001, 0.001), 0.0001, max_boost=50.0)
        for value in out:
            self.assertAlmostEqual(value, 0.05, places=6)


class MasterVolumeProbeTests(unittest.TestCase):
    def test_probe_constructs_and_reports_safely(self):
        # Must never raise on construction regardless of platform/pycaw, and
        # linear_gain returns either None (unreadable) or a 0..1 float.
        probe = system_volume.MasterVolumeProbe()
        gain = probe.linear_gain()
        self.assertTrue(gain is None or (0.0 <= gain <= 1.0))
        if probe.available:
            self.assertIsNotNone(gain)


if __name__ == "__main__":
    unittest.main()
