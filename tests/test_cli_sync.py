"""Headless ``--monitor`` / ``--music`` sync follows the GUI and exits cleanly."""

import io
import threading
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from lumisync import lumisync as cli
from lumisync.config.sync_settings import SyncSettings
from lumisync.sync import session as sync_session


class FakeSettings:
    def __init__(self, values):
        self.values = values

    def value(self, key, default=None):
        return self.values.get(key, default)


class TargetSelectionTests(unittest.TestCase):
    devices = [
        {"mac": "AA:01", "model": "H6199", "transport": "lan"},
        {"mac": "AA:02", "model": "H6199", "transport": "lan"},
        {"device_id": "acct-1", "transport": "govee_account"},
    ]

    def select(self, mode, saved):
        def capabilities(device):
            return SimpleNamespace(supports_streaming=device.get("transport") == "lan")

        with (
            patch.object(cli, "_gui_settings", return_value=FakeSettings(saved)),
            patch("lumisync.drivers.registry.capabilities_for_device", side_effect=capabilities),
            redirect_stdout(io.StringIO()),
        ):
            return cli._select_cli_targets(mode, self.devices)

    def test_only_devices_that_can_stream_are_synced(self):
        selected = self.select("monitor", {})
        self.assertEqual([device.get("mac") for device in selected], ["AA:01", "AA:02"])

    def test_the_guis_saved_targets_are_honoured(self):
        selected = self.select("music", {"sync/music/devices": "AA:02"})
        self.assertEqual([device.get("mac") for device in selected], ["AA:02"])

    def test_missing_saved_targets_fall_back_to_every_local_device(self):
        selected = self.select("monitor", {"sync/monitor/devices": "gone"})
        self.assertEqual(len(selected), 2)

    def test_saved_mapping_is_used_for_monitor_sync(self):
        saved = {"sync/led_mapping_edge_v1": '[{"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}]'}
        with patch.object(cli, "_gui_settings", return_value=FakeSettings(saved)):
            provider = cli._cli_mapping_provider()
            self.assertIsNotNone(provider)
            mapping = provider(3, 16 / 9)
        self.assertEqual(len(mapping), 3)
        self.assertTrue(all(rect["w"] == 0.5 for rect in mapping))


class FakeSession:
    behaviour = "run_until_stopped"
    instances = []

    def __init__(self, mode, devices, settings, **kwargs):
        self.kwargs = kwargs
        FakeSession.instances.append(self)

    def run(self):
        on_error = self.kwargs["on_error"]
        on_started = self.kwargs["on_started"]
        if FakeSession.behaviour == "dies":
            on_error("Could not start 1 device(s): Strip: unreachable")
            return
        on_error("Could not start 1 device(s): Lamp: unreachable")
        on_started(1)
        self.kwargs["stop_event"].wait(5)


class RunCliSyncTests(unittest.TestCase):
    def run_cli(self, behaviour, *, press_enter):
        FakeSession.behaviour = behaviour
        FakeSession.instances = []

        def watch(_prompt, pressed):
            if press_enter:
                threading.Timer(0.1, pressed.set).start()

        output = io.StringIO()
        with (
            patch.object(sync_session, "SyncSession", FakeSession),
            patch.object(cli, "_watch_for_enter", side_effect=watch),
            redirect_stdout(output),
        ):
            code = cli._run_cli_sync(object(), [{"mac": "AA"}], "monitor", SyncSettings())
        return code, output.getvalue()

    def test_exits_non_zero_when_the_session_dies(self):
        code, output = self.run_cli("dies", press_enter=False)

        self.assertEqual(code, 1)
        self.assertIn("No device could start monitor sync", output)

    def test_one_failed_device_does_not_fail_a_running_sync(self):
        code, output = self.run_cli("run_until_stopped", press_enter=True)

        self.assertEqual(code, 0)
        self.assertIn("Lamp: unreachable", output)
        self.assertIs(FakeSession.instances[0].kwargs["on_status"], print)


if __name__ == "__main__":
    unittest.main()
