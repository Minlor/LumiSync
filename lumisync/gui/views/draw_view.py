"""Draw view — paint a pixel image / animation and send it to a matrix device."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..widgets.pixel_canvas import PixelCanvas
from ..widgets.product_controls import ProductComboBox
from ...drivers import pool
from ...drivers.idotmatrix_ble import KNOWN_SIZES

RGB = tuple


class _DrawWorker(QObject):
    """Sends a drawing or animation to a device off the UI thread.

    The animation loop lives here (not in the adapter) so it can be stopped
    cleanly when the user navigates away or quits.
    """

    finished = Signal()
    error = Signal(str)

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
        self._current_size = "32x32"
        self._thread: Optional[QThread] = None
        self._worker: Optional[_DrawWorker] = None
        self._send_failed = False
        self._stop_requested = False
        self._suppress_stop_status = False
        self._sending_animation = False
        self._send_device: Optional[Dict[str, Any]] = None
        # Custom-painted canvas conflicts with the nav fade's opacity effect.
        self.setProperty("noFadeEffect", True)
        self._build()
        self._connect_device_signals()
        self._refresh_devices()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(18)

        page_header = QVBoxLayout()
        page_header.setSpacing(3)
        header = QLabel("Draw")
        header.setProperty("role", "title")
        page_header.addWidget(header)

        intro = QLabel(
            "Paint a matrix panel directly, or assemble saved frames into an animation."
        )
        intro.setProperty("role", "pageDescription")
        intro.setWordWrap(True)
        page_header.addWidget(intro)
        root.addLayout(page_header)

        workspace = QHBoxLayout()
        workspace.setSpacing(16)

        tools_panel = QFrame()
        tools_panel.setObjectName("DrawToolsPanel")
        tools_panel.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        tools_panel.setFixedWidth(236)
        tools = QVBoxLayout(tools_panel)
        tools.setContentsMargins(16, 16, 16, 16)
        tools.setSpacing(10)

        target_label = QLabel("TARGET PANEL")
        target_label.setProperty("role", "eyebrow")
        tools.addWidget(target_label)
        self.device_combo = ProductComboBox()
        self.device_combo.setAccessibleName("Target Bluetooth panel")
        self.device_combo.currentIndexChanged.connect(self._update_action_states)
        tools.addWidget(self.device_combo)

        size_label = QLabel("CANVAS SIZE")
        size_label.setProperty("role", "eyebrow")
        tools.addSpacing(6)
        tools.addWidget(size_label)

        self.size_combo = ProductComboBox()
        for size in KNOWN_SIZES:
            self.size_combo.addItem(size, size)
        self.size_combo.setCurrentText("32x32")
        self.size_combo.currentIndexChanged.connect(self._on_size_changed)
        tools.addWidget(self.size_combo)

        brush_label = QLabel("BRUSH")
        brush_label.setProperty("role", "eyebrow")
        tools.addSpacing(6)
        tools.addWidget(brush_label)

        color_row = QHBoxLayout()
        color_row.setSpacing(10)
        color_row.addWidget(QLabel("Color"), 1)
        self.color_swatch = QLabel()
        self.color_swatch.setObjectName("DrawColorSwatch")
        self.color_swatch.setFixedSize(32, 32)
        self._refresh_swatch()
        color_row.addWidget(self.color_swatch)
        tools.addLayout(color_row)

        self.color_button = QPushButton("Choose Color")
        self.color_button.clicked.connect(self._pick_color)
        tools.addWidget(self.color_button)

        self.clear_button = QPushButton("Clear Canvas")
        self.clear_button.clicked.connect(self._clear_canvas)
        tools.addWidget(self.clear_button)

        self.fill_button = QPushButton("Fill Canvas")
        self.fill_button.clicked.connect(lambda: self.canvas.fill(self._color))
        tools.addWidget(self.fill_button)
        tools.addStretch(1)
        workspace.addWidget(tools_panel)

        canvas_panel = QFrame()
        canvas_panel.setObjectName("DrawCanvasPanel")
        canvas_layout = QVBoxLayout(canvas_panel)
        canvas_layout.setContentsMargins(16, 14, 16, 16)
        canvas_layout.setSpacing(10)

        canvas_header = QHBoxLayout()
        canvas_title = QLabel("Canvas")
        canvas_title.setProperty("role", "strong")
        canvas_header.addWidget(canvas_title)
        canvas_header.addStretch(1)
        self.canvas_meta_label = QLabel("32 × 32")
        self.canvas_meta_label.setProperty("role", "subtle")
        canvas_header.addWidget(self.canvas_meta_label)
        canvas_layout.addLayout(canvas_header)

        self.canvas = PixelCanvas(32, 32)
        self.canvas.setAccessibleName("Pixel canvas")
        self.canvas.setAccessibleDescription(
            "Paint a pixel image for the selected Bluetooth matrix panel"
        )
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.canvas.changed.connect(self._update_action_states)
        canvas_layout.addWidget(self.canvas, 1)
        workspace.addWidget(canvas_panel, 1)
        root.addLayout(workspace, 1)

        animation_panel = QFrame()
        animation_panel.setObjectName("DrawTimelinePanel")
        animation_layout = QVBoxLayout(animation_panel)
        animation_layout.setContentsMargins(16, 12, 16, 12)
        animation_layout.setSpacing(10)

        animation_header = QHBoxLayout()
        animation_title = QLabel("Animation")
        animation_title.setProperty("role", "strong")
        animation_header.addWidget(animation_title)
        animation_header.addStretch(1)
        self.frames_label = QLabel("No saved frames")
        self.frames_label.setProperty("role", "subtle")
        animation_header.addWidget(self.frames_label)
        animation_layout.addLayout(animation_header)

        send_row = QHBoxLayout()
        send_row.setSpacing(10)

        self.add_frame_button = QPushButton("Add Frame")
        self.add_frame_button.clicked.connect(self._add_frame)
        send_row.addWidget(self.add_frame_button)

        self.clear_frames_button = QPushButton("Clear Frames")
        self.clear_frames_button.clicked.connect(lambda: self._clear_frames())
        self.clear_frames_button.setEnabled(False)
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
        self.stop_button.clicked.connect(lambda: self._stop_send())
        self.stop_button.setEnabled(False)
        send_row.addWidget(self.stop_button)

        animation_layout.addLayout(send_row)
        root.addWidget(animation_panel)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        root.addWidget(self.status)

    # --- helpers ---
    def _connect_device_signals(self) -> None:
        for signal_name in (
            "devices_discovered",
            "device_added",
            "device_removed",
            "device_updated",
        ):
            signal = getattr(self.controller, signal_name, None)
            if signal is not None:
                signal.connect(lambda *_: self._refresh_devices())

    def _refresh_devices(self) -> None:
        previous = self.device_combo.currentData()
        previous_id = self._device_id(previous)

        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        selected_index = -1
        for device in self.controller.devices:
            if str(device.get("transport", "")).lower() != "ble":
                continue
            device_copy = dict(device)
            model = device_copy.get("model", "Bluetooth panel")
            size = device_copy.get("matrix_size")
            label = f"{model} · {size}" if size else str(model)
            self.device_combo.addItem(label, device_copy)
            if self._device_id(device_copy) == previous_id:
                selected_index = self.device_combo.count() - 1

        if self.device_combo.count() == 0:
            self.device_combo.addItem("No Bluetooth panels found", None)
            self.device_combo.setEnabled(False)
        else:
            self.device_combo.setEnabled(True)
            self.device_combo.setCurrentIndex(max(0, selected_index))
        self.device_combo.blockSignals(False)
        self._update_action_states()

    @staticmethod
    def _device_id(device: Optional[Dict[str, Any]]) -> str:
        if not device:
            return ""
        return str(device.get("ble_address") or device.get("mac") or device.get("id") or "")

    def _update_action_states(self) -> None:
        if not hasattr(self, "send_button"):
            return
        ready = self._target_device() is not None
        running = self._thread is not None
        has_frames = bool(self._frames)
        self.send_button.setEnabled(ready and not running)
        self.play_button.setEnabled(ready and len(self._frames) >= 2 and not running)
        self.stop_button.setEnabled(running)
        self.device_combo.setEnabled(ready and not running)
        self.size_combo.setEnabled(not running)
        self.color_button.setEnabled(not running)
        self.clear_button.setEnabled(not self.canvas.is_empty() and not running)
        self.fill_button.setEnabled(not running)
        self.canvas.setEnabled(not running)
        self.add_frame_button.setEnabled(not running)
        self.clear_frames_button.setEnabled(has_frames and not running)

        target_hint = "" if ready else "Connect or add a Bluetooth matrix panel first"
        self.send_button.setToolTip(target_hint)
        self.play_button.setToolTip(
            target_hint
            if not ready
            else "Add at least two saved frames to play an animation"
            if len(self._frames) < 2
            else "Loop the saved frames on the selected panel"
        )

    def _set_status(self, text: str, state: str = "") -> None:
        self.status.setText(text)
        self.status.setProperty("state", state)
        self.status.setVisible(bool(text))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _refresh_swatch(self) -> None:
        r, g, b = self._color
        self.color_swatch.setStyleSheet(
            f"background: rgb({r},{g},{b}); border: 2px solid #727272; "
            "border-radius: 10px;"
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
        if size == self._current_size:
            return
        if not self.canvas.is_empty() or self._frames:
            frame_count = len(self._frames)
            details = "the current drawing"
            if frame_count:
                details += f" and {frame_count} saved frame{'s' if frame_count != 1 else ''}"
            reply = QMessageBox.question(
                self,
                "Change Canvas Size",
                f"Changing the canvas size clears {details}. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                blocked = self.size_combo.blockSignals(True)
                self.size_combo.setCurrentIndex(
                    max(0, self.size_combo.findData(self._current_size))
                )
                self.size_combo.blockSignals(blocked)
                return

        cols, rows = KNOWN_SIZES.get(size, (32, 32))
        self._current_size = str(size)
        self.canvas.set_matrix_size(cols, rows)
        self.canvas_meta_label.setText(f"{cols} × {rows}")
        self._clear_frames(confirm=False)

    def _clear_canvas(self) -> None:
        if self.canvas.is_empty():
            return
        reply = QMessageBox.question(
            self,
            "Clear Canvas",
            "Clear every pixel from the current canvas?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.canvas.clear()

    def _add_frame(self) -> None:
        self._frames.append(self.canvas.get_grid())
        count = len(self._frames)
        self.frames_label.setText(f"{count} saved frame{'s' if count != 1 else ''}")
        self._update_action_states()

    def _clear_frames(self, confirm: bool = True) -> None:
        if not self._frames:
            return
        if confirm:
            count = len(self._frames)
            reply = QMessageBox.question(
                self,
                "Clear Saved Frames",
                f"Remove all {count} saved frame{'s' if count != 1 else ''}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._frames = []
        self.frames_label.setText("No saved frames")
        self._update_action_states()

    def _target_device(self) -> Optional[Dict[str, Any]]:
        device = self.device_combo.currentData() if hasattr(self, "device_combo") else None
        if device and str(device.get("transport", "")).lower() == "ble":
            return dict(device)
        return None

    def _send(self, frames) -> None:
        device = self._target_device()
        if not device:
            self._set_status("Connect or add a Bluetooth matrix panel first.", "error")
            return
        if self._thread is not None:
            self._set_status("A send is already in progress.")
            return

        self._send_failed = False
        self._stop_requested = False
        self._suppress_stop_status = False
        self._sending_animation = len(frames) > 1
        self._send_device = dict(device)
        self._set_status(
            f"Playing {len(frames)}-frame animation..."
            if self._sending_animation
            else "Sending to panel..."
        )
        self._thread = QThread()
        self._worker = _DrawWorker(device, frames, 0.2)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.error.connect(self._on_send_error)
        self._worker.finished.connect(self._on_send_finished)
        self._worker.finished.connect(self._thread.quit)
        # Clear references only once the thread has actually stopped.
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_thread_refs)
        self._thread.start()
        if self._sending_animation:
            self.controller.mark_device_output(
                self._send_device, "Pixel animation", active=True
            )
        self._update_action_states()

    def _on_send_error(self, message: str) -> None:
        self._send_failed = True
        if self._send_device:
            self.controller.clear_device_activity(self._send_device)
        self._set_status(f"Send failed: {message}", "error")

    def _on_send_finished(self) -> None:
        if self._stop_requested:
            if self._send_device and not self._send_failed:
                self.controller.mark_device_output(
                    self._send_device, "Pixel art", active=False
                )
            if not self._suppress_stop_status:
                self._set_status(
                    "Animation stopped." if self._sending_animation else "Send stopped."
                )
        elif not self._send_failed:
            if self._send_device:
                self.controller.mark_device_output(
                    self._send_device, "Pixel art", active=False
                )
            self._set_status("Sent to panel.", "active")

    def _clear_thread_refs(self) -> None:
        self._thread = None
        self._worker = None
        self._send_device = None
        self._update_action_states()

    def _stop_send(self, show_status: bool = True) -> None:
        self._stop_requested = self._thread is not None
        self._suppress_stop_status = not show_status
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)

    def _send_current(self) -> None:
        self._send([self.canvas.get_grid()])

    def _play_animation(self) -> None:
        if len(self._frames) >= 2:
            self._send(self._frames)

    def hideEvent(self, event) -> None:
        # Stop any running animation when navigating away.
        self._stop_send(show_status=False)
        super().hideEvent(event)
