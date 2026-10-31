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
        self.camera = None
        if GENERAL.platform == "Windows":
            import dxcam

            # dxcam caches camera instances globally. Release the old instance
            # before creating one for a different output; a released camera can
            # be replaced, while a cached broken one cannot.
            if (
                ScreenGrab._dxcam_instance is not None
                and ScreenGrab._dxcam_output_idx != self.display_index
            ):
                self._release_cached_dxcam()

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

    @classmethod
    def _release_cached_dxcam(cls) -> None:
        camera = cls._dxcam_instance
        cls._dxcam_instance = None
        cls._dxcam_output_idx = None
        if camera is not None:
            try:
                release = getattr(camera, "release", None)
                if callable(release):
                    release()
            except Exception:
                pass
            try:
                del camera
            except Exception:
                pass

    @classmethod
    def reset_cache(cls) -> None:
        """Drop the cached camera so the next ScreenGrab starts clean."""
        cls._release_cached_dxcam()

    def close(self) -> None:
        """Release the capture backend so a broken camera can be recreated."""
        camera = self.camera
        self.camera = None
        if camera is None:
            return
        if GENERAL.platform == "Windows":
            if ScreenGrab._dxcam_instance is camera:
                self._release_cached_dxcam()
            else:
                try:
                    release = getattr(camera, "release", None)
                    if callable(release):
                        release()
                except Exception:
                    pass
        else:
            try:
                close = getattr(camera, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass

    def _capture_screen(self):
        try:
            screen = self.capture_method()
        except ModuleNotFoundError as exc:
            if exc.name == "cv2":
                raise ScreenCaptureDependencyError(MISSING_CAPTURE_BACKEND_MESSAGE) from exc
            raise
        return screen

    def capture_frame(self) -> Tuple[Optional[np.ndarray], str]:
        """Return the native capture array and its channel order.

        Keeping the native BGRA buffer avoids copying a full 4K frame into a
        new RGB array on every tick. ``average_zone_colors`` only materializes
        the small sampled sub-rectangles, so the channel swap happens there.
        """
        screen = self._capture_screen()
        if screen is None:
            return None, "RGB"

        array = np.asarray(screen)
        if array.ndim == 3 and array.shape[2] == 4 and (
            GENERAL.platform == "Windows" or GENERAL.compositor == "x11"
        ):
            return array, "BGRA"
        if array.ndim == 3 and array.shape[2] == 4:
            return array[..., :3], "RGB"
        return array, "RGB"

    def capture_array(self) -> Optional[np.ndarray]:
        """Capture the screen as a contiguous ``(H, W, 3)`` uint8 RGB array.

        Returning NumPy directly lets the sync pipeline vectorize zone sampling
        instead of paying for a PIL round-trip and per-pixel Python access.
        """
        frame, channel_order = self.capture_frame()
        if frame is None:
            return None
        if channel_order == "BGRA":
            # Compatibility path for callers that want a ready-to-use RGB frame.
            frame = frame[..., [2, 1, 0]]
        return np.ascontiguousarray(frame)

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
