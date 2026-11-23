import threading
import unittest
from unittest import mock

from lumisync.sync import system_volume


PACTL_INFO = """Server String: unix:/run/user/1000/pulse/native
Server Name: PulseAudio (on PipeWire 1.6.2)
Default Sink: alsa_output.pci-0000_00_1f.3.analog-stereo
Default Source: alsa_input.pci-0000_00_1f.3.analog-stereo
"""


def sink_block(index, name, volume="19661 /  30% / -31.37 dB", mute="no", flags="DECIBEL_VOLUME LATENCY"):
    return f"""Sink #{index}
\tState: RUNNING
\tName: {name}
\tDescription: {name}
\tMute: {mute}
\tVolume: front-left: {volume},   front-right: {volume}
\t        balance 0.00
\tBase Volume: 65536 / 100% / 0.00 dB
\tMonitor Source: {name}.monitor
\tFlags: {flags}
\tProperties:
\t\tdevice.description = "Name: not this one"
"""


DEFAULT = "alsa_output.pci-0000_00_1f.3.analog-stereo"


def known(value):
    if value is None:
        raise AssertionError("expected a gain, got None")
    return value


class ParseTests(unittest.TestCase):
    def test_default_sink_is_read_from_pactl_info(self):
        self.assertEqual(system_volume.parse_default_sink(PACTL_INFO), DEFAULT)
        self.assertIsNone(system_volume.parse_default_sink("Server Name: x\n"))

    def test_software_volume_is_cubic_like_the_monitor_level(self):
        # Measured: a software sink at 30% gives its monitor 0.3**3 = 2.7% of
        # the amplitude, so that is the gain to divide back out.
        listing = sink_block(1, "other", volume="65536 / 100% / 0.00 dB") + sink_block(2, DEFAULT)
        gain = known(system_volume.parse_sink_gain(listing, DEFAULT))
        self.assertAlmostEqual(gain, (19661 / 65536) ** 3, places=6)
        bands = system_volume.compensate((0.027, 0.027, 0.027), gain)
        for value in bands:
            self.assertAlmostEqual(value, 1.0, places=2)

    def test_full_volume_is_unity_and_over_amplification_is_reported(self):
        full = sink_block(0, DEFAULT, volume="65536 / 100% / 0.00 dB")
        self.assertAlmostEqual(known(system_volume.parse_sink_gain(full, DEFAULT)), 1.0)
        loud = sink_block(0, DEFAULT, volume="98304 / 150% / 10.57 dB")
        self.assertAlmostEqual(known(system_volume.parse_sink_gain(loud, DEFAULT)), 1.5 ** 3)

    def test_muted_sink_is_zero_gain(self):
        muted = sink_block(0, DEFAULT, mute="yes")
        self.assertEqual(system_volume.parse_sink_gain(muted, DEFAULT), 0.0)

    def test_hardware_volume_sink_is_not_compensated(self):
        # Hardware volume is applied after the monitor tap: the monitor level
        # does not follow the fader, so dividing it out would double-correct.
        hardware = sink_block(
            0, DEFAULT, flags="HARDWARE HW_MUTE_CTRL HW_VOLUME_CTRL DECIBEL_VOLUME LATENCY"
        )
        self.assertIsNone(system_volume.parse_sink_gain(hardware, DEFAULT))

    def test_missing_or_malformed_sink_gives_none(self):
        self.assertIsNone(system_volume.parse_sink_gain("", DEFAULT))
        self.assertIsNone(system_volume.parse_sink_gain(sink_block(0, "other"), DEFAULT))
        broken = sink_block(0, DEFAULT, volume="garbage")
        self.assertIsNone(system_volume.parse_sink_gain(broken, DEFAULT))


class FakePactl:
    def __init__(self, sinks):
        self.sinks = sinks
        self.calls = []

    def __call__(self, command, timeout):
        self.calls.append((list(command), timeout))
        if command[1:] == ["info"]:
            return PACTL_INFO
        if command[1:] == ["list", "sinks"]:
            return self.sinks
        return None


class PulseSinkVolumeTests(unittest.TestCase):
    def test_read_now_queries_default_sink_with_short_timeouts(self):
        runner = FakePactl(sink_block(3, DEFAULT))
        probe = system_volume.PulseSinkVolume(executable="pactl", runner=runner)
        self.assertAlmostEqual(known(probe.read_now()), (19661 / 65536) ** 3, places=6)
        self.assertEqual([call[0] for call in runner.calls], [["pactl", "info"], ["pactl", "list", "sinks"]])
        for _command, timeout in runner.calls:
            self.assertLessEqual(timeout, 1.0)

    def test_pactl_failure_or_timeout_gives_none(self):
        probe = system_volume.PulseSinkVolume(runner=lambda command, timeout: None)
        self.assertIsNone(probe.read_now())

        def explode(command, timeout):
            raise RuntimeError("pactl crashed")

        self.assertIsNone(system_volume.PulseSinkVolume(runner=explode).read_now())

    def test_linear_gain_never_waits_and_polls_at_most_once_a_second(self):
        release = threading.Event()
        calls = []

        def slow_runner(command, timeout):
            calls.append(command)
            release.wait(timeout=2.0)
            return PACTL_INFO if command[1:] == ["info"] else sink_block(0, DEFAULT)

        now = [100.0]
        probe = system_volume.PulseSinkVolume(
            runner=slow_runner, clock=lambda: now[0], refresh_seconds=1.0
        )
        # First call returns at once with "unknown" while pactl runs elsewhere.
        self.assertIsNone(probe.linear_gain())
        now[0] += 0.5
        self.assertIsNone(probe.linear_gain())
        release.set()
        worker = probe._worker
        if worker is not None:
            worker.join(timeout=2.0)
        self.assertEqual(len(calls), 2)  # one info + one list, not two refreshes
        self.assertAlmostEqual(probe.linear_gain() or 0.0, (19661 / 65536) ** 3, places=6)
        now[0] += 1.0
        probe.linear_gain()
        worker = probe._worker
        if worker is not None:
            worker.join(timeout=2.0)
        self.assertEqual(len(calls), 4)


class PlatformTests(unittest.TestCase):
    def linux_probe(self, sinks):
        runner = FakePactl(sinks)
        with mock.patch.object(system_volume.sys, "platform", "linux"), mock.patch.object(
            system_volume.shutil, "which", return_value="/usr/bin/pactl"
        ), mock.patch.object(system_volume, "_run_pactl", runner):
            probe = system_volume.MasterVolumeProbe()
        return probe, runner

    def test_linux_probe_uses_pactl_and_reads_once_up_front(self):
        probe, runner = self.linux_probe(sink_block(0, DEFAULT))
        self.assertTrue(probe.available)
        self.assertIsInstance(probe._sink, system_volume.PulseSinkVolume)
        self.assertEqual(len(runner.calls), 2)
        # The first frame already has a reading and does not poll again.
        self.assertAlmostEqual(known(probe.linear_gain()), (19661 / 65536) ** 3, places=6)
        self.assertEqual(len(runner.calls), 2)

    def test_linux_hardware_volume_sink_is_unavailable(self):
        probe, _runner = self.linux_probe(
            sink_block(0, DEFAULT, flags="HARDWARE HW_VOLUME_CTRL DECIBEL_VOLUME")
        )
        self.assertFalse(probe.available)
        self.assertIsNone(probe.linear_gain())

    def test_linux_without_pactl_is_unknown(self):
        with mock.patch.object(system_volume.sys, "platform", "linux"), mock.patch.object(
            system_volume.shutil, "which", return_value=None
        ):
            probe = system_volume.MasterVolumeProbe()
        self.assertFalse(probe.available)
        self.assertIsNone(probe.linear_gain())

    def test_linux_probe_returns_the_sink_reading(self):
        probe = system_volume.MasterVolumeProbe.__new__(system_volume.MasterVolumeProbe)
        probe._endpoint = None
        probe._available = True
        probe._last_gain = 1.0
        probe._sink = mock.Mock(linear_gain=mock.Mock(return_value=0.027))
        self.assertEqual(probe.linear_gain(), 0.027)

    def test_other_platforms_do_not_touch_pactl(self):
        with mock.patch.object(system_volume.sys, "platform", "darwin"), mock.patch.object(
            system_volume, "PulseSinkVolume"
        ) as pulse:
            probe = system_volume.MasterVolumeProbe()
        pulse.assert_not_called()
        self.assertIsNone(probe._sink)


if __name__ == "__main__":
    unittest.main()
