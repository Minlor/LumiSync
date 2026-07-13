"""Capture the main LumiSync pages for README documentation.

The utility renders the real PySide application and captures its native window,
so documentation stays aligned with the current theme, controls, and app icon.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lumisync.gui.main_window import LumiSyncMainWindow  # noqa: E402
from lumisync.gui.theme import apply_theme  # noqa: E402
from lumisync.gui.utils.window_effects import (  # noqa: E402
    install_dark_titlebar_filter,
)


PAGES = (
    ("devices", "lumisync-devices.png"),
    ("monitor", "lumisync-monitor-sync.png"),
    ("music", "lumisync-music-sync.png"),
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture LumiSync README screenshots from the live GUI."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "docs" / "images",
        help="Destination directory (default: docs/images).",
    )
    parser.add_argument(
        "--material",
        choices=("acrylic", "mica", "solid"),
        default="acrylic",
        help="Window material used for the screenshots.",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LumiSync")
    app.setOrganizationName("Minlor")
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    install_dark_titlebar_filter(app)

    window = LumiSyncMainWindow()
    window.resize(1120, 760)
    window.set_window_material(args.material, notify=False)
    window.show()
    window.raise_()
    window.activateWindow()

    original_cursor_position = QCursor.pos()
    active_screen = window.screen() or app.primaryScreen()
    if active_screen:
        # Keep the pointer out of the documentation images, then restore it.
        QCursor.setPos(active_screen.availableGeometry().bottomRight())

    page_index = 0

    def finish() -> None:
        QCursor.setPos(original_cursor_position)
        if window.tray is not None:
            window.tray.hide()
        window._quitting = True
        window.close()
        app.quit()

    def capture_page() -> None:
        nonlocal page_index
        key, filename = PAGES[page_index]
        app.processEvents()

        screen = window.screen() or app.primaryScreen()
        if screen:
            # Capturing the desktop region defined by frameGeometry includes
            # the native title bar and therefore documents the real app icon.
            frame = window.frameGeometry()
            pixmap = screen.grabWindow(
                0,
                frame.x(),
                frame.y(),
                frame.width(),
                frame.height(),
            )
        else:
            pixmap = window.grab()
        if pixmap.isNull():
            pixmap = window.grab()

        destination = output_dir / filename
        if not pixmap.save(str(destination), "PNG"):
            raise RuntimeError(f"Could not save {destination}")
        print(f"Captured {key}: {destination}")

        page_index += 1
        if page_index >= len(PAGES):
            QTimer.singleShot(100, finish)
            return
        QTimer.singleShot(100, show_page)

    def show_page() -> None:
        key, _filename = PAGES[page_index]
        window.nav_shell.set_current_by_key(key)
        QTimer.singleShot(550, capture_page)

    QTimer.singleShot(900, show_page)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
