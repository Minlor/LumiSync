"""Draw view — paint a pixel image / animation and send it to a matrix device."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..widgets.pixel_canvas import PixelCanvas
from ...drivers import pool
from ...drivers.idotmatrix_ble import KNOWN_SIZES

RGB = tuple


class _DrawWorker(QObject):
    """Sends a drawing or animation to a device off the UI thread.

    The animation loop lives here (not in the adapter) so it can be stopped
    cleanly when the user navigates away or quits.
    """

    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, device, frames, frame_delay):
        super().__init__()
        self.device = device
        self.frames = frames
        self.frame_delay = frame_delay
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            adapter = pool.acquire(self.device)
            if len(self.frames) == 1:
                adapter.draw_grid(self.frames[0])
            else:
                while not self._stop.is_set():
                    for grid in self.frames:
                        if self._stop.is_set():
                            break
                        adapter.draw_grid(grid)
                        self._stop.wait(max(0.0, self.frame_delay))
                    if len(self.frames) <= 1:
                        break
        except Exception as exc:  # surface BLE errors to the user
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


class DrawView(QWidget):
    def __init__(self, device_controller):
        super().__init__()
        self.controller = device_controller
        self._frames: List[List[List[tuple]]] = []
        self._color = (255, 0, 0)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_DrawWorker] = None
        # Custom-painted canvas conflicts with the nav fade's opacity effect.
        self.setProperty("noFadeEffect", True)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        header = QLabel("Draw")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        header.setFont(f)
        root.addWidget(header)

        # Toolbar
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.size_combo = QComboBox()
        for size in KNOWN_SIZES:
            self.size_combo.addItem(size, size)
        self.size_combo.setCurrentText("32x32")
        self.size_combo.currentIndexChanged.connect(self._on_size_changed)
        bar.addWidget(QLabel("Size:"))
        bar.addWidget(self.size_combo)

        self.color_button = QPushButton("Color")
        self.color_button.clicked.connect(self._pick_color)
        bar.addWidget(self.color_button)
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(22, 22)
        self._refresh_swatch()
        bar.addWidget(self.color_swatch)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(lambda: self.canvas.clear())
        bar.addWidget(self.clear_button)

        self.fill_button = QPushButton("Fill")
        self.fill_button.clicked.connect(lambda: self.canvas.fill(self._color))
        bar.addWidget(self.fill_button)

        bar.addStretch(1)
        root.addLayout(bar)

        # Canvas
        self.canvas = PixelCanvas(32, 32)
        root.addWidget(self.canvas, 1)

        # Frames / send row
        send_row = QHBoxLayout()
        send_row.setSpacing(8)

        self.add_frame_button = QPushButton("Add Frame")
        self.add_frame_button.clicked.connect(self._add_frame)
        send_row.addWidget(self.add_frame_button)

        self.frames_label = QLabel("0 frames")
        send_row.addWidget(self.frames_label)

        self.clear_frames_button = QPushButton("Clear Frames")
        self.clear_frames_button.clicked.connect(self._clear_frames)
        send_row.addWidget(self.clear_frames_button)

        send_row.addStretch(1)

        self.send_button = QPushButton("Send to Panel")
        self.send_button.setProperty("role", "primary")
        self.send_button.clicked.connect(self._send_current)
        send_row.addWidget(self.send_button)

        self.play_button = QPushButton("Play Animation")
        self.play_button.clicked.connect(self._play_animation)
        send_row.addWidget(self.play_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self._stop_send)
        send_row.addWidget(self.stop_button)

        root.addLayout(send_row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

    # --- helpers ---
    def _refresh_swatch(self) -> None:
        r, g, b = self._color
        self.color_swatch.setStyleSheet(
            f"background: rgb({r},{g},{b}); border: 1px solid #555; border-radius: 3px;"
        )

    def _pick_color(self) -> None:
        r, g, b = self._color
        chosen = QColorDialog.getColor(QColor(r, g, b), self, "Pick color")
        if chosen.isValid():
            self._color = (chosen.red(), chosen.green(), chosen.blue())
            self.canvas.set_color(self._color)
            self._refresh_swatch()

    def _on_size_changed(self) -> None:
        size = self.size_combo.currentData() or "32x32"
        cols, rows = KNOWN_SIZES.get(size, (32, 32))
        self.canvas.set_matrix_size(cols, rows)
        self._clear_frames()

    def _add_frame(self) -> None:
        self._frames.append(self.canvas.get_grid())
        self.frames_label.setText(f"{len(self._frames)} frames")

    def _clear_frames(self) -> None:
        self._frames = []
        self.frames_label.setText("0 frames")

    def _target_device(self) -> Optional[Dict[str, Any]]:
        device = self.controller.get_selected_device()
        if device and str(device.get("transport", "")).lower() == "ble":
            return device
        return None

    def _send(self, frames) -> None:
        device = self._target_device()
        if not device:
            self.status.setText(
                "Select a Bluetooth matrix device (e.g. iDotMatrix) in the Devices tab first."
            )
            return
        if self._thread is not None:
            self.status.setText("A send is already in progress...")
            return

        self.status.setText("Sending to panel...")
        self._thread = QThread()
        self._worker = _DrawWorker(device, frames, 0.2)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.error.connect(lambda msg: self.status.setText(f"Send failed: {msg}"))
        self._worker.finished.connect(lambda: self.status.setText(
            "Done." if not self.status.text().startswith("Send failed") else self.status.text()
        ))
        self._worker.finished.connect(self._thread.quit)
        # Clear references only once the thread has actually stopped.
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_thread_refs)
        self._thread.start()

    def _clear_thread_refs(self) -> None:
        self._thread = None
        self._worker = None

    def _stop_send(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        self.status.setText("Stopped.")

    def _send_current(self) -> None:
        self._send([self.canvas.get_grid()])

    def _play_animation(self) -> None:
        frames = self._frames or [self.canvas.get_grid()]
        self._send(frames)

    def hideEvent(self, event) -> None:
        # Stop any running animation when navigating away.
        self._stop_send()
        super().hideEvent(event)
