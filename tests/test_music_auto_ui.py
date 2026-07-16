import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from lumisync.gui.views.modes_view import ModesView


class FakeSyncController(QObject):
    sync_started = Signal(str)
    sync_stopped = Signal()
    brightness_changed = Signal(str, float)
    music_auto_state_changed = Signal(str, object)
    status_updated = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.reaction = "auto"
        self.palette = "auto"
        self.running = False
        self.mode = None
        self.selected_devices = []
        self.auto_gain = True

    def get_music_auto_gain(self) -> bool:
        return self.auto_gain

    def set_music_auto_gain(self, enabled: bool) -> None:
        self.auto_gain = bool(enabled)

    def get_music_reaction(self) -> str:
        return self.reaction

    def set_music_reaction(self, reaction: str) -> None:
        self.reaction = reaction

    def get_music_palette(self) -> str:
        return self.palette

    def set_music_palette(self, palette: str) -> None:
        self.palette = palette

    def get_music_brightness(self) -> float:
        return 0.8

    def set_music_brightness(self, _brightness: float) -> None:
        pass

    def is_syncing(self) -> bool:
        return self.running

    def get_current_sync_mode(self):
        return self.mode

    def start_music_sync(self, _devices=None) -> None:
        self.running = True
        self.mode = "music"
        self.sync_started.emit("music")

    def stop_sync(self, announce: bool = True) -> None:
        del announce
        self.running = False
        self.mode = None
        self.sync_stopped.emit()


class MusicAutoDirectorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.controller = FakeSyncController()
        self.view = ModesView(self.controller, mode="music")

    def tearDown(self) -> None:
        self.view.close()

    def test_auto_director_status_shows_live_reaction_and_color(self):
        self.assertFalse(self.view.music_auto_status.isHidden())

        self.controller.music_auto_state_changed.emit(
            "pulse",
            (42, 164, 255),
        )
        self.app.processEvents()

        self.assertEqual(
            self.view.music_auto_status_title.text(),
            "Now directing · Beat Pulse",
        )
        self.assertIn("Auto Mix", self.view.music_auto_status_detail.text())
        self.assertIn(
            "#2AA4FF",
            self.view.music_auto_color_dot.styleSheet(),
        )
        self.assertIn("#2AA4FF", self.view.music_auto_color_dot.toolTip())
        self.assertEqual(self.view.music_auto_color_dot.width(), 28)
        self.assertFalse(hasattr(self.view, "music_auto_color_value"))
        self.assertTrue(
            self.view.music_reaction_buttons["pulse"].property("autoActive")
        )
        self.assertTrue(self.view.music_reaction_buttons["auto"].isChecked())

    def test_switching_auto_reactions_moves_the_secondary_highlight(self):
        self.controller.music_auto_state_changed.emit("pulse", (255, 80, 40))
        self.controller.music_auto_state_changed.emit("wave", (40, 120, 255))
        self.app.processEvents()

        self.assertFalse(
            self.view.music_reaction_buttons["pulse"].property("autoActive")
        )
        self.assertTrue(
            self.view.music_reaction_buttons["wave"].property("autoActive")
        )

    def test_manual_reaction_hides_status_and_clears_auto_highlight(self):
        self.controller.music_auto_state_changed.emit("pulse", (255, 80, 40))

        self.view._on_music_reaction_changed("wave")

        self.assertTrue(self.view.music_auto_status.isHidden())
        self.assertFalse(
            self.view.music_reaction_buttons["pulse"].property("autoActive")
        )

    def test_sync_start_shows_a_listening_state_until_audio_arrives(self):
        self.controller.start_music_sync()
        self.app.processEvents()

        self.assertEqual(
            self.view.music_auto_status_title.text(),
            "Auto Director is listening",
        )
        self.assertEqual(
            self.view.music_auto_color_dot.toolTip(),
            "Waiting for live output color",
        )


if __name__ == "__main__":
    unittest.main()
