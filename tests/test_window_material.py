import unittest
from unittest.mock import MagicMock, patch

from lumisync.gui.main_window import LumiSyncMainWindow


class WindowMaterialPreferenceTests(unittest.TestCase):
    def test_mica_is_applied_persisted_and_reflected_in_settings(self):
        window = MagicMock()
        window.settings_page = MagicMock()

        with (
            patch(
                "lumisync.gui.utils.window_effects.apply_windows_mica",
                return_value=True,
            ) as apply_mica,
            patch(
                "lumisync.gui.utils.window_effects.disable_windows_backdrop",
                return_value=True,
            ) as disable_backdrop,
        ):
            active = LumiSyncMainWindow.set_window_material(window, "mica")

        self.assertEqual(active, "mica")
        apply_mica.assert_called_once_with(window)
        disable_backdrop.assert_not_called()
        window.settings.setValue.assert_called_once_with(
            "ui/window_material",
            "mica",
        )
        window.settings_page.set_window_material_value.assert_called_once_with(
            "mica"
        )
        window.show_status.assert_called_once_with(
            "Window material changed to Mica."
        )

    def test_unavailable_acrylic_falls_back_to_solid_dark(self):
        window = MagicMock()
        window.settings_page = MagicMock()

        with (
            patch(
                "lumisync.gui.utils.window_effects.apply_windows_acrylic",
                return_value=False,
            ),
            patch(
                "lumisync.gui.utils.window_effects.disable_windows_backdrop",
                return_value=True,
            ) as disable_backdrop,
        ):
            active = LumiSyncMainWindow.set_window_material(window, "acrylic")

        self.assertEqual(active, "solid")
        disable_backdrop.assert_called_once_with(window)
        window.settings.setValue.assert_called_once_with(
            "ui/window_material",
            "solid",
        )
        window.settings_page.set_window_material_value.assert_called_once_with(
            "solid"
        )
        window.show_error.assert_called_once_with(
            "Acrylic is unavailable; using Solid Dark."
        )


if __name__ == "__main__":
    unittest.main()
