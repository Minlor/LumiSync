import socket
import threading
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..config.options import GENERAL
from ..config.sync_settings import SyncSettings
from . import processing


MISSING_CAPTURE_BACKEND_MESSAGE = (
    "Monitor sync could not load dxcam's screen-capture backend. Reinstall "
    "LumiSync (or the dxcam wheel for this Python version) and try again."
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
                raise ScreenCaptureDependencyError(MISSING_CAPTURE_BACKEND_MESSAGE) from exc
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

def apply_brightness(
    colors: List[Tuple[int, int, int]], brightness_factor: float
) -> List[Tuple[int, int, int]]:
    """Apply a 0..1 brightness factor to a list of RGB colors."""
    return processing.apply_brightness(colors, brightness_factor)


def start(
    server: socket.socket,
    device: Dict[str, Any],
    stop_event=None,
    settings: Optional[SyncSettings] = None,
) -> None:
    """Run monitor sync through the shared :class:`SyncSession` engine."""
    from .session import SyncSession

    session = SyncSession(
        "monitor",
        [device],
        settings or SyncSettings.from_options(),
        server=server,
        stop_event=stop_event or threading.Event(),
        on_error=print,
    )
    session.run()
