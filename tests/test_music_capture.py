"""Music sync capture: source selection, the recording thread and the loop."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lumisync.config.sync_settings import SyncSettings
from lumisync.sync import music
from lumisync.sync import session as sync_session


class FakeDevice:
    def __init__(self, id, name, loopback=False):
        self.id = id
        self.name = name
        self.isloopback = loopback


class FakeSoundcard:
    """Mimics soundcard's lookup: exact id, then name substring."""

    def __init__(self, speaker, microphones):
        self.speaker = speaker
        self.microphones = list(microphones)

    def default_speaker(self):
        return self.speaker

    def get_microphone(self, id, include_loopback=False):
        candidates = [m for m in self.microphones if include_loopback or not m.isloopback]
        for microphone in candidates:
            if microphone.id == id:
                return microphone
        for microphone in candidates:
            if id in microphone.name:
                return microphone
        raise IndexError(f"no soundcard with id {id}")

    def all_microphones(self, include_loopback=False):
        return [m for m in self.microphones if include_loopback or not m.isloopback]


class LoopbackSelectionTests(unittest.TestCase):
    def pick(self, system, soundcard):
        with (
            patch.object(music, "_soundcard_backend", return_value=soundcard),
            patch.object(music, "_platform_system", return_value=system),
        ):
            return music.default_loopback_microphone()

    def test_linux_uses_the_default_sinks_monitor_source(self):
        speaker = FakeDevice("alsa_output.pci.analog-stereo", "Built-in Audio Analog Stereo")
        soundcard = FakeSoundcard(
            speaker,
            [
                # PipeWire: the input source shares the sink's description.
                FakeDevice("alsa_input.pci.analog-stereo", "Built-in Audio Analog Stereo"),
                FakeDevice(
                    "alsa_output.pci.analog-stereo.monitor",
                    "Monitor of Built-in Audio Analog Stereo",
                    loopback=True,
                ),
            ],
        )

        self.assertEqual(self.pick("Linux", soundcard).id, "alsa_output.pci.analog-stereo.monitor")

    def test_windows_uses_the_speakers_own_loopback(self):
        speaker = FakeDevice("{0.0.0.00000000}.{speaker}", "Speakers (Realtek)")
        soundcard = FakeSoundcard(
            speaker,
            [
                FakeDevice("{0.0.1.00000000}.{mic}", "Microphone (Speakers Realtek)"),
                FakeDevice("{0.0.0.00000000}.{headset}", "Headset", loopback=True),
                FakeDevice("{0.0.0.00000000}.{speaker}", "Speakers (Realtek)", loopback=True),
            ],
        )

        microphone = self.pick("Windows", soundcard)
        self.assertEqual(microphone.id, speaker.id)
        self.assertTrue(microphone.isloopback)

    def test_name_fallback_only_considers_loopback_sources(self):
        speaker = FakeDevice("bluez_output.headphones", "WH-1000XM4")
        soundcard = FakeSoundcard(
            speaker,
            [
                FakeDevice("bluez_input.headset", "WH-1000XM4"),
                FakeDevice("renamed.monitor", "Monitor of WH-1000XM4", loopback=True),
            ],
        )

        self.assertEqual(self.pick("Linux", soundcard).id, "renamed.monitor")

    def test_a_real_microphone_is_never_used(self):
        speaker = FakeDevice("alsa_output.usb", "USB Speakers")
        soundcard = FakeSoundcard(speaker, [FakeDevice("alsa_input.usb", "USB Speakers Mic")])

        with self.assertRaises(RuntimeError) as raised:
            self.pick("Linux", soundcard)
        self.assertIn("monitor", str(raised.exception).lower())

    def test_error_lists_the_available_loopback_sources(self):
        speaker = FakeDevice("alsa_output.hdmi", "HDMI Output")
        soundcard = FakeSoundcard(
            speaker,
            [
                FakeDevice("alsa_input.usb", "USB Mic"),
                FakeDevice("alsa_output.usb.monitor", "Monitor of USB Speakers", loopback=True),
            ],
        )

        with self.assertRaises(RuntimeError) as raised:
            self.pick("Linux", soundcard)
        self.assertIn("Monitor of USB Speakers", str(raised.exception))
        self.assertNotIn("USB Mic", str(raised.exception))


class FakeRecorder:
    def __init__(self, chunks, release):
        self.chunks = list(chunks)
        self.release = release
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_exc):
        self.exited = True

    def record(self, numframes=None):
        if self.chunks:
            return self.chunks.pop(0)
        self.release.wait(0.05)
        return np.zeros((0, 2), dtype=np.float32)


class FakeMicrophone:
    name = "Monitor of Speakers"

    def __init__(self, chunks):
        self.release = threading.Event()
        self.opened = []
        self.recorder_obj = FakeRecorder(chunks, self.release)

    def recorder(self, samplerate, channels=None, blocksize=None):
        self.opened.append({"samplerate": samplerate, "blocksize": blocksize})
        return self.recorder_obj


class AudioCaptureTests(unittest.TestCase):
    def test_recorder_uses_a_small_blocksize_and_keeps_the_newest_audio(self):
        first = np.full((300, 2), 0.1, dtype=np.float32)
        second = np.full((300, 2), 0.5, dtype=np.float32)
        microphone = FakeMicrophone([first, second])
        capture = music.AudioCapture(
            samplerate=48000, buffer_frames=1000, microphone_factory=lambda: microphone
        )
        capture.start()
        try:
            self.assertTrue(capture.wait_until_ready(2.0))
            window = capture.read(400, timeout=2.0)
        finally:
            capture.close()

        self.assertEqual(microphone.opened, [{"samplerate": 48000, "blocksize": music.RECORD_BLOCKSIZE}])
        self.assertEqual(window.shape, (400, 2))
        self.assertTrue(np.all(window[-300:] == 0.5))
        self.assertTrue(np.all(window[:100] == 0.1))
        self.assertEqual(capture.device_name, "Monitor of Speakers")
        self.assertTrue(microphone.recorder_obj.exited)

    def test_a_huge_backlog_only_keeps_the_newest_window(self):
        backlog = np.zeros((200_000, 2), dtype=np.float32)
        backlog[-10:] = 0.9
        microphone = FakeMicrophone([backlog])
        capture = music.AudioCapture(
            samplerate=48000, buffer_frames=64, microphone_factory=lambda: microphone
        )
        capture.start()
        try:
            window = capture.read(64, timeout=2.0)
        finally:
            capture.close()

        self.assertEqual(window.shape, (64, 2))
        self.assertTrue(np.all(window[-10:] == 0.9))

    def test_read_times_out_and_reports_how_long_audio_has_been_missing(self):
        microphone = FakeMicrophone([])
        now = [100.0]
        capture = music.AudioCapture(
            samplerate=48000,
            buffer_frames=64,
            microphone_factory=lambda: microphone,
            clock=lambda: now[0],
        )
        capture.start()
        try:
            self.assertTrue(capture.wait_until_ready(2.0))
            started = time.monotonic()
            self.assertIsNone(capture.read(32, timeout=0.05))
            self.assertLess(time.monotonic() - started, 1.0)
            now[0] = 104.0
            self.assertGreaterEqual(capture.seconds_since_data(), 4.0)
        finally:
            started = time.monotonic()
            capture.close()
        self.assertLess(time.monotonic() - started, 1.0)

    def test_an_unavailable_device_raises_from_the_caller(self):
        def missing():
            raise RuntimeError("No monitor/loopback source found for HDMI")

        capture = music.AudioCapture(samplerate=48000, buffer_frames=64, microphone_factory=missing)
        capture.start()
        try:
            with self.assertRaises(RuntimeError) as raised:
                for _ in range(40):
                    if capture.wait_until_ready(0.05):
                        break
            self.assertIn("HDMI", str(raised.exception))
        finally:
            capture.close()


class FakeCapture:
    def __init__(self, reads, *, name="Speakers", since_data=0.0, ready_error=None,
                 clock=None, on_empty=None):
        self.reads = list(reads)
        self._name = name
        self.since_data = since_data
        self.ready_error = ready_error
        self.clock = clock
        self.on_empty = on_empty
        self.closed = False

    @property
    def device_name(self):
        return self._name

    def start(self):
        pass

    def wait_until_ready(self, timeout):
        if self.ready_error is not None:
            raise self.ready_error
        return True

    def read(self, numframes, timeout):
        if not self.reads:
            if self.on_empty is not None:
                self.on_empty()
            return None
        item = self.reads.pop(0)
        if isinstance(item, tuple):  # (advance_seconds, data)
            advance, item = item
            if self.clock is not None:
                self.clock["now"] += advance
        elif self.clock is not None:
            self.clock["now"] += 0.02
        return item

    def seconds_since_data(self):
        return self.since_data

    def close(self):
        self.closed = True


class InlineSender:
    def __init__(self, send, max_hz=40.0, **_kwargs):
        self._send = send

    def submit(self, frame):
        self._send(frame)

    def last_error(self):
        return None

    def close(self):
        pass


class Adapter:
    def __init__(self, fail_frames=0):
        self.capabilities = SimpleNamespace(segment_count=3, max_update_hz=40.0)
        self.device = {}
        self.frames = []
        self.fail_frames = fail_frames

    def begin_stream(self):
        pass

    def set_segments(self, colors):
        if self.fail_frames:
            self.fail_frames -= 1
            raise OSError("boom")
        self.frames.append(list(colors))

    def end_stream(self):
        pass

    def close(self):
        pass


class VolumeProbe:
    created = 0

    def __init__(self):
        VolumeProbe.created += 1

    def linear_gain(self):
        return None


def loud(frames=1104):
    t = np.arange(frames) / 48000.0
    tone = (0.5 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)
    return np.stack([tone, tone], axis=1)


def silent(frames=1104):
    return np.zeros((frames, 2), dtype=np.float32)


class MusicLoopTests(unittest.TestCase):
    def run_music(self, captures, *, adapter=None, clock=None):
        stop = threading.Event()
        statuses, errors = [], []
        queue = list(captures)
        opened = []
        VolumeProbe.created = 0

        def next_capture(_numframes):
            capture = queue.pop(0) if queue else FakeCapture([])
            if capture.on_empty is None:
                capture.on_empty = stop.set
            opened.append(capture)
            return capture

        patches = [
            patch.object(sync_session.pool, "acquire", return_value=adapter or Adapter()),
            patch.object(sync_session, "_audio_capture", side_effect=next_capture),
            patch.object(sync_session, "_audio_output_id", return_value=None),
            patch.object(sync_session.system_volume, "MasterVolumeProbe", VolumeProbe),
            patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
        ]
        if clock is not None:
            patches.append(patch.object(sync_session.time, "monotonic", side_effect=lambda: clock["now"]))
        for item in patches:
            item.start()
        try:
            sync_session.SyncSession(
                "music",
                [{"mac": "AA", "model": "H6672"}],
                SyncSettings(),
                stop_event=stop,
                on_status=statuses.append,
                on_error=errors.append,
                sender_factory=InlineSender,
            ).run()
        finally:
            for item in reversed(patches):
                item.stop()
        return statuses, errors, opened

    def test_reports_which_output_it_listens_to(self):
        adapter = Adapter()
        statuses, errors, opened = self.run_music([FakeCapture([loud(), loud()], name="Speakers (Realtek)")], adapter=adapter)

        self.assertIn("Listening to Speakers (Realtek)", statuses)
        self.assertEqual(errors, [])
        self.assertEqual(len(adapter.frames), 2)
        self.assertTrue(opened[0].closed)

    def test_silence_hint_is_shown_once_per_silent_episode(self):
        clock = {"now": 0.0}
        reads = [(1.0, silent()) for _ in range(20)] + [(1.0, loud())] + [(1.0, silent()) for _ in range(12)]
        statuses, _errors, _opened = self.run_music([FakeCapture(reads, clock=clock)], clock=clock)

        hints = [status for status in statuses if "No sound is reaching LumiSync from Speakers" in status]
        self.assertEqual(len(hints), 2)

    def test_a_stalled_device_is_reported_and_reopened(self):
        stalled = FakeCapture([None], since_data=5.0, on_empty=lambda: None)
        stalled.reads = []
        healthy = FakeCapture([loud()])
        _statuses, errors, opened = self.run_music([stalled, healthy])

        self.assertTrue(any("No audio data from Speakers for 3 seconds" in error for error in errors))
        self.assertGreaterEqual(len(opened), 2)
        self.assertTrue(stalled.closed)

    def test_a_long_gap_reopens_the_recorder_and_the_volume_probe(self):
        clock = {"now": 0.0}
        first = FakeCapture([(0.02, loud()), (30.0, loud()), (0.02, loud())], clock=clock, on_empty=lambda: None)
        second = FakeCapture([loud()], clock=clock)
        _statuses, errors, opened = self.run_music([first, second], clock=clock)

        self.assertEqual(errors, [])
        self.assertIs(opened[1], second)
        self.assertTrue(first.closed)
        self.assertEqual(len(first.reads), 1)  # left before reading on after the gap
        self.assertEqual(VolumeProbe.created, 2)

    def test_recorders_stuck_in_the_backend_do_not_pile_up(self):
        stop = threading.Event()
        errors = []
        made = []

        def on_error(message):
            errors.append(message)
            if "not responding" in message:
                stop.set()

        def next_capture(_numframes):
            capture = FakeCapture([], since_data=5.0, on_empty=lambda: None)
            capture.alive = True  # its thread never comes back from the backend
            made.append(capture)
            return capture

        with (
            patch.object(sync_session.pool, "acquire", return_value=Adapter()),
            patch.object(sync_session, "_audio_capture", side_effect=next_capture),
            patch.object(sync_session, "_audio_output_id", return_value=None),
            patch.object(sync_session.system_volume, "MasterVolumeProbe", VolumeProbe),
            patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
        ):
            sync_session.SyncSession(
                "music",
                [{"mac": "AA", "model": "H6672"}],
                SyncSettings(),
                stop_event=stop,
                on_error=on_error,
                sender_factory=InlineSender,
            ).run()

        self.assertEqual(len(made), sync_session.MAX_STUCK_AUDIO_CAPTURES)
        self.assertIn("No audio data from Speakers", errors[0])
        self.assertIn("not responding", errors[-1])

    def test_recovered_is_only_reported_after_a_whole_frame_succeeds(self):
        adapter = Adapter(fail_frames=1)
        failing_open = FakeCapture([], ready_error=RuntimeError("device busy"))
        frame_fails = FakeCapture([loud()], on_empty=lambda: None)
        healthy = FakeCapture([loud(), loud()])
        statuses, errors, _opened = self.run_music([failing_open, frame_fails, healthy], adapter=adapter)

        self.assertEqual(errors, ["Error in music sync: device busy", "Error in music sync: boom"])
        self.assertEqual(statuses.count("Music sync audio input recovered"), 1)
        self.assertEqual(len(adapter.frames), 2)


if __name__ == "__main__":
    unittest.main()
