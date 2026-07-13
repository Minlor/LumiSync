import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette

from lumisync.gui.utils import window_effects


class WindowsBackdropTests(unittest.TestCase):
    def test_native_titlebar_colors_use_windows_color_attributes(self):
        dwmapi = MagicMock()
        dwmapi.DwmSetWindowAttribute.return_value = 0

        with (
            patch.object(window_effects, "_IS_WINDOWS", True),
            patch.object(window_effects.windll, "dwmapi", dwmapi),
        ):
            self.assertTrue(
                window_effects.set_windows_titlebar_colors(
                    123,
                    caption="#112233",
                    text="#F1F2F3",
                    border="#445566",
                )
            )

        attributes = [
            invocation.args[1].value
            for invocation in dwmapi.DwmSetWindowAttribute.call_args_list
        ]
        self.assertEqual(attributes, [35, 36, 34])
        self.assertEqual(window_effects._colorref("#112233"), 0x332211)

    def test_dark_titlebar_also_applies_integrated_chrome(self):
        dwmapi = MagicMock()
        dwmapi.DwmSetWindowAttribute.return_value = 0

        with (
            patch.object(window_effects, "_IS_WINDOWS", True),
            patch.object(window_effects.windll, "dwmapi", dwmapi),
            patch.object(
                window_effects,
                "set_windows_titlebar_colors",
                return_value=True,
            ) as set_colors,
            patch.object(
                window_effects,
                "enable_windows_rounded_corners",
                return_value=True,
            ) as set_corners,
        ):
            self.assertTrue(window_effects.enable_dark_titlebar(123))

        set_colors.assert_called_once_with(123)
        set_corners.assert_called_once_with(123)

    def test_system_backdrop_requires_windows_11_22621(self):
        with (
            patch.object(window_effects, "_IS_WINDOWS", True),
            patch.object(
                window_effects.sys,
                "getwindowsversion",
                return_value=SimpleNamespace(build=22620),
            ),
        ):
            self.assertFalse(window_effects.windows_system_backdrop_supported())

        with (
            patch.object(window_effects, "_IS_WINDOWS", True),
            patch.object(
                window_effects.sys,
                "getwindowsversion",
                return_value=SimpleNamespace(build=22621),
            ),
        ):
            self.assertTrue(window_effects.windows_system_backdrop_supported())

    def test_apply_acrylic_sets_window_property_after_dwm_accepts(self):
        window = MagicMock()
        window.winId.return_value = 123
        window.findChildren.return_value = []
        window.palette.return_value = QPalette()
        window.autoFillBackground.return_value = False

        with (
            patch.object(
                window_effects,
                "windows_system_backdrop_supported",
                return_value=True,
            ),
            patch.object(
                window_effects,
                "enable_windows_backdrop",
                return_value=True,
            ) as enable_backdrop,
            patch.object(
                window_effects,
                "extend_windows_frame_into_client",
                return_value=True,
            ) as extend_frame,
        ):
            self.assertTrue(window_effects.apply_windows_acrylic(window))

        enable_backdrop.assert_called_once_with(
            123, window_effects.WindowsBackdropType.ACRYLIC
        )
        extend_frame.assert_called_once_with(123)
        window.setProperty.assert_called_once_with("backdrop", "acrylic")
        applied_palette = window.setPalette.call_args.args[0]
        self.assertEqual(
            applied_palette.color(QPalette.ColorRole.Window).alpha(),
            0,
        )
        window.update.assert_called_once_with()

    def test_apply_acrylic_restores_opaque_fallback_when_dwm_rejects(self):
        window = MagicMock()
        window.winId.return_value = 123
        original_palette = QPalette()
        window.palette.return_value = original_palette
        window.autoFillBackground.return_value = False

        with (
            patch.object(
                window_effects,
                "windows_system_backdrop_supported",
                return_value=True,
            ),
            patch.object(
                window_effects,
                "enable_windows_backdrop",
                return_value=False,
            ),
        ):
            self.assertFalse(window_effects.apply_windows_acrylic(window))

        translucent = Qt.WidgetAttribute.WA_TranslucentBackground
        self.assertEqual(
            window.setAttribute.call_args_list,
            [call(translucent, True), call(translucent, False)],
        )
        window.setProperty.assert_called_once_with("backdrop", "")
        self.assertEqual(window.setPalette.call_args_list[-1].args[0], original_palette)

    def test_apply_acrylic_rolls_back_when_client_frame_extension_fails(self):
        window = MagicMock()
        window.winId.return_value = 123
        window.palette.return_value = QPalette()
        window.autoFillBackground.return_value = False

        with (
            patch.object(
                window_effects,
                "windows_system_backdrop_supported",
                return_value=True,
            ),
            patch.object(
                window_effects,
                "enable_windows_backdrop",
                return_value=True,
            ) as enable_backdrop,
            patch.object(
                window_effects,
                "extend_windows_frame_into_client",
                return_value=False,
            ),
        ):
            self.assertFalse(window_effects.apply_windows_acrylic(window))

        self.assertEqual(
            enable_backdrop.call_args_list,
            [
                call(123, window_effects.WindowsBackdropType.ACRYLIC),
                call(123, window_effects.WindowsBackdropType.NONE),
            ],
        )
        window.setProperty.assert_called_once_with("backdrop", "")

    def test_disable_backdrop_restores_solid_theme(self):
        window = MagicMock()
        window.winId.return_value = 123
        window.findChildren.return_value = []
        window.palette.return_value = QPalette()
        window.autoFillBackground.return_value = False

        with (
            patch.object(window_effects, "_IS_WINDOWS", True),
            patch.object(
                window_effects,
                "enable_windows_backdrop",
                return_value=True,
            ) as enable_backdrop,
            patch.object(
                window_effects,
                "reset_windows_frame_client_area",
                return_value=True,
            ) as reset_frame,
        ):
            self.assertTrue(window_effects.disable_windows_backdrop(window))

        enable_backdrop.assert_called_once_with(
            123,
            window_effects.WindowsBackdropType.NONE,
        )
        reset_frame.assert_called_once_with(123)
        window.setProperty.assert_called_once_with("backdrop", "")
        window.setAttribute.assert_called_once_with(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            False,
        )


if __name__ == "__main__":
    unittest.main()
