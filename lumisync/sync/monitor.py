import socket
import time
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .. import led_mapping, utils
from ..config.options import BRIGHTNESS, GENERAL, SYNC
from ..drivers.registry import create_adapter
from . import processing


MISSING_CV2_MESSAGE = (
    "Monitor sync requires OpenCV's cv2 module for Windows screen capture. "
    "Install opencv-python-headless or use a LumiSync build that bundles it."
)

WAYLAND_MESSAGE = (
    "Monitor (screen) sync isn't available on Wayland yet — Wayland has no "
    "direct screen grab. Music sync and manual control still work. For screen "
    "sync, log out and choose an X11/Xorg session (running under XWayland does "
    "not help). Wayland capture via the desktop portal is planned."
)


class ScreenCaptureDependencyError(RuntimeError):
    """Raised when a required screen-capture dependency is unavailable."""


class WaylandUnsupportedError(RuntimeError):
    """Raised when monitor sync is attempted on a Wayland session."""


def _create_dxcam_camera(dxcam_module, **kwargs):
    # BGRA is DXCam's native capture format and does not require OpenCV color
    # conversion. LumiSync converts it to RGB with NumPy in capture_array().
    kwargs = {"output_color": "BGRA", **kwargs}
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
                # TODO: Wayland capture via xdg-desktop-portal ScreenCast +
                # PipeWire. Until then, fail with an actionable message rather
                # than a raw NotImplementedError.
                raise WaylandUnsupportedError(WAYLAND_MESSAGE)

    def capture_array(self) -> Optional[np.ndarray]:
        """Capture the screen as a contiguous ``(H, W, 3)`` uint8 RGB array.

        Returning NumPy directly lets the sync pipeline vectorize zone sampling
        instead of paying for a PIL round-trip and per-pixel Python access.
        """
        try:
            screen = self.capture_method()
        except ModuleNotFoundError as exc:
            if exc.name == "cv2":
                raise ScreenCaptureDependencyError(MISSING_CV2_MESSAGE) from exc
            raise
        if screen is None:
            return None

        array = np.asarray(screen)
        if array.ndim == 3 and array.shape[2] == 4 and (
            GENERAL.platform == "Windows" or GENERAL.compositor == "x11"
        ):
            # DXCam and mss both return native BGRA; reorder to RGB and drop alpha.
            array = array[..., [2, 1, 0]]
        elif array.ndim == 3 and array.shape[2] == 4:
            array = array[..., :3]
        return np.ascontiguousarray(array)

    def capture(self) -> Image.Image | None:
        """Captures a screenshot as a PIL image (compatibility helper)."""
        array = self.capture_array()
        if array is None:
            return None
        return Image.fromarray(array)


def apply_brightness(
    colors: List[Tuple[int, int, int]], brightness_factor: float
) -> List[Tuple[int, int, int]]:
    """Apply a 0..1 brightness factor to a list of RGB colors."""
    return processing.apply_brightness(colors, brightness_factor)


def start(server: socket.socket, device: Dict[str, Any]) -> None:
    """Run the CLI monitor-sync loop for a single device.

    Each iteration captures one frame, averages the mapped edge zones, applies
    saturation/brightness, smooths toward the new colors with a temporal EMA,
    and sends exactly one packet. Frame pacing keeps CPU and UDP traffic in
    check while staying far more responsive than the old ten-packets-per-frame
    approach.
    """
    adapter = create_adapter(device, server)
    adapter.begin_stream()
    try:
        screen_grab = ScreenGrab()
    except ScreenCaptureDependencyError as exc:
        print(f"Monitor sync unavailable: {exc}")
        return

    segment_count = adapter.capabilities.segment_count
    smoother = processing.ColorSmoother(SYNC.smoothing, segment_count)
    frame_interval = 1.0 / max(1, SYNC.monitor_fps)
    mapping: Optional[List[led_mapping.NormalizedRect]] = None
    mapping_aspect: Optional[float] = None
    last_sent: Optional[List[Tuple[int, int, int]]] = None
    last_sent_at: Optional[float] = None

    while True:
        frame_start = time.monotonic()
        try:
            frame = screen_grab.capture_array()
        except ScreenCaptureDependencyError as exc:
            print(f"Monitor sync unavailable: {exc}")
            return
        except OSError:
            print("Warning: Screenshot failed, trying again...")
            continue

        if frame is None:
            time.sleep(0.01)
            continue

        height, width = frame.shape[:2]
        aspect_ratio = width / max(1, height)
        if mapping is None or aspect_ratio != mapping_aspect:
            mapping = led_mapping.generate_screen_mapping(segment_count, aspect_ratio)
            mapping_aspect = aspect_ratio

        colors = processing.average_zone_colors(
            frame, mapping, gamma_correct=SYNC.gamma_correct
        )
        colors = processing.apply_saturation(colors, SYNC.saturation)
        colors = utils.resample_colors_to_count(colors, segment_count)
        colors = processing.apply_brightness(colors, BRIGHTNESS.monitor)
        smoothed = smoother.update(colors)

        now = time.monotonic()
        if processing.frame_needs_send(
            last_sent,
            smoothed,
            SYNC.delta_threshold,
            last_sent_at=last_sent_at,
            now=now,
        ):
            adapter.set_segments(smoothed)
            last_sent = smoothed
            last_sent_at = now

        elapsed = time.monotonic() - frame_start
        if elapsed < frame_interval:
            time.sleep(frame_interval - elapsed)
