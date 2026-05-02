import socket
import time
from functools import partial
from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image

from .. import connection, led_mapping, utils
from ..config.options import BRIGHTNESS, GENERAL


MISSING_CV2_MESSAGE = (
    "Monitor sync requires OpenCV's cv2 module for Windows screen capture. "
    "Install opencv-python-headless or use a LumiSync build that bundles it."
)


class ScreenCaptureDependencyError(RuntimeError):
    """Raised when a required screen-capture dependency is unavailable."""


def _create_dxcam_camera(dxcam_module, **kwargs):
    try:
        return dxcam_module.create(processor_backend="numpy", **kwargs)
    except TypeError:
        return dxcam_module.create(**kwargs)


class ScreenGrab:
    """Facilitates taking a screenshot while supporting
    different platforms and compositors (the latter for Unix).
    """

    # Track the current dxcam instance so we can release it when switching displays
    _dxcam_instance = None
    _dxcam_output_idx = None

    def __init__(self, *, display_index: int = 0) -> None:
        self.display_index = int(display_index)
        if GENERAL.platform == "Windows":
            import dxcam

            # dxcam caches camera instances globally. We must delete the old
            # instance before creating one for a different output.
            if (
                ScreenGrab._dxcam_instance is not None
                and ScreenGrab._dxcam_output_idx != self.display_index
            ):
                try:
                    del ScreenGrab._dxcam_instance
                except Exception:
                    pass
                ScreenGrab._dxcam_instance = None
                ScreenGrab._dxcam_output_idx = None

            if ScreenGrab._dxcam_instance is None:
                requested_output = max(0, self.display_index)
                try:
                    self.camera = _create_dxcam_camera(
                        dxcam,
                        output_idx=requested_output,
                    )
                except Exception:
                    try:
                        self.camera = _create_dxcam_camera(
                            dxcam,
                            device_idx=0,
                            output_idx=requested_output,
                        )
                    except Exception:
                        # Fall back to dxcam's default output, usually the primary
                        # monitor. This keeps GUI sync usable when QSettings contains
                        # a stale or dxcam-incompatible display index.
                        self.camera = _create_dxcam_camera(dxcam)
                        self.display_index = 0
                ScreenGrab._dxcam_instance = self.camera
                ScreenGrab._dxcam_output_idx = self.display_index
            else:
                self.camera = ScreenGrab._dxcam_instance

            self.capture_method = self.camera.grab
        else:
            if GENERAL.compositor == "x11":
                import mss

                self.camera = mss.mss()

                # mss.monitors[0] is the virtual bounding box; [1..n] are real monitors
                monitor_idx = self.display_index + 1
                if monitor_idx < 1 or monitor_idx >= len(self.camera.monitors):
                    monitor_idx = 1 if len(self.camera.monitors) > 1 else 0
                self.capture_method = partial(self.camera.grab, self.camera.monitors[monitor_idx])
            else:
                # TODO: Implement Wayland support
                raise NotImplementedError("Wayland support is not yet implemented in ScreenGrab.")

    def capture(self) -> Image.Image | None:
        """Captures a screenshot."""
        try:
            screen = self.capture_method()
        except ModuleNotFoundError as exc:
            if exc.name == "cv2":
                raise ScreenCaptureDependencyError(MISSING_CV2_MESSAGE) from exc
            raise
        if screen is None:
            return screen

        if GENERAL.platform != "Windows" and GENERAL.compositor == "x11":
            screen = np.array(screen)[..., [2, 1, 0]]

        return Image.fromarray(screen)


def start(server: socket.socket, device: Dict[str, Any]) -> None:
    """Starts the monitor-light synchronization."""
    connection.switch_razer(server, device, True)
    try:
        screen_grab = ScreenGrab()
    except ScreenCaptureDependencyError as exc:
        print(f"Monitor sync unavailable: {exc}")
        return

    segment_count = connection.get_segment_count(device, default=10)
    previous_colors = [(0, 0, 0)] * segment_count
    while True:
        colors = []
        try:
            screen = screen_grab.capture()
            if screen is None:
                continue

            width, height = screen.size
        except ScreenCaptureDependencyError as exc:
            print(f"Monitor sync unavailable: {exc}")
            return
        except OSError:
            print("Warning: Screenshot failed, trying again...")
            continue

        mapping = led_mapping.generate_screen_mapping(
            segment_count,
            width / max(1, height),
        )
        for rect in mapping:
            normalized = led_mapping.normalize_rect(rect)
            x1 = int(normalized["x"] * width)
            y1 = int(normalized["y"] * height)
            x2 = int((normalized["x"] + normalized["w"]) * width)
            y2 = int((normalized["y"] + normalized["h"]) * height)
            colors.append(
                screen.getpixel(
                    (
                        max(0, min(width - 1, x1 + max(1, x2 - x1) // 2)),
                        max(0, min(height - 1, y1 + max(1, y2 - y1) // 2)),
                    )
                )
            )

        # Apply brightness setting to colors
        colors = apply_brightness(colors, BRIGHTNESS.monitor)
        colors = utils.resample_colors_to_count(colors, segment_count)
        previous_colors = utils.fit_colors_to_count(previous_colors, segment_count)

        smooth_transition(server, device, previous_colors, colors)
        previous_colors = colors


def apply_brightness(
    colors: List[Tuple[int, int, int]], brightness_factor: float
) -> List[Tuple[int, int, int]]:
    """Apply brightness factor to a list of colors.

    Args:
        colors: List of RGB color tuples
        brightness_factor: Brightness factor (0.0 to 1.0)

    Returns:
        List of adjusted RGB color tuples
    """
    return [
        (
            int(r * brightness_factor),
            int(g * brightness_factor),
            int(b * brightness_factor),
        )
        for r, g, b in colors
    ]


def smooth_transition(
    server: socket.socket,
    device: Dict[str, Any],
    previous_colors: List[Tuple[float, float, float]],
    colors: List[Tuple[float, float, float]],
    steps: int = 10,
    delay: float = 0.01,
) -> None:
    """Computes a smooth transition of the colors and sends it to a device."""
    previous_colors = utils.fit_colors_to_count(previous_colors, len(colors))
    for step in range(1, steps + 1):
        t = step / steps
        interpolated_colors = [
            (
                int(prev[0] + (cur[0] - prev[0]) * t),
                int(prev[1] + (cur[1] - prev[1]) * t),
                int(prev[2] + (cur[2] - prev[2]) * t),
            )
            for prev, cur in zip(previous_colors, colors)
        ]

        connection.send_razer_data(
            server, device, utils.convert_colors(interpolated_colors)
        )
        time.sleep(delay)
