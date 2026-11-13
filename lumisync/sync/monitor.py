import importlib.util
import socket
import sys
import threading
import time
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from ..config.options import GENERAL
from ..config.sync_settings import SyncSettings
from . import processing


MISSING_CAPTURE_BACKEND_MESSAGE = (
    "Monitor sync could not load dxcam's screen-capture backend. Reinstall "
    "LumiSync (or the dxcam wheel for this Python version) and try again."
)

MISSING_X11_CAPTURE_MESSAGE = (
    "Monitor sync could not load the mss screen-capture module. Reinstall "
    "LumiSync and try again."
)

WAYLAND_MESSAGE = (
    "Monitor (screen) sync isn't available on Wayland yet — Wayland has no "
    "direct screen grab. Music sync and manual control still work. For screen "
    "sync, log out and choose an X11/Xorg session (running under XWayland does "
    "not help). Wayland capture via the desktop portal is planned."
)

# dxcam retries a lost output forever (a detached monitor, a display that went
# to sleep over DisplayPort, a GPU driver reset). Give up after this long so the
# sync loop can release the camera, back off and stay responsive to Stop.
CAPTURE_RECOVERY_TIMEOUT = 3.0


class ScreenCaptureDependencyError(RuntimeError):
    """Raised when a required screen-capture dependency is unavailable."""


class WaylandUnsupportedError(RuntimeError):
    """Raised when monitor sync is attempted on a Wayland session."""


class CaptureRecoveryTimeout(RuntimeError):
    """The capture backend could not get the display back in time."""


def capture_supported() -> Tuple[bool, str]:
    """Return whether monitor sync can capture this session, and why not."""
    if GENERAL.platform == "Windows":
        if importlib.util.find_spec("dxcam") is None:
            return False, MISSING_CAPTURE_BACKEND_MESSAGE
        return True, ""
    if GENERAL.compositor == "wayland":
        return False, WAYLAND_MESSAGE
    if importlib.util.find_spec("mss") is None:
        return False, MISSING_X11_CAPTURE_MESSAGE
    return True, ""


class BoundedRecoveryWait:
    """Stand-in for dxcam's ``ProgressiveWait`` that gives up.

    dxcam's display recovery calls ``next_delay_seconds()`` after each failed
    attempt and sleeps that long, forever. Raising here ends the recovery with
    an error the sync loop already handles, once ``timeout`` seconds have
    passed or the session is stopping.
    """

    def __init__(
        self,
        stop_event: Any = None,
        *,
        timeout: float = CAPTURE_RECOVERY_TIMEOUT,
        max_delay: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stop_event = stop_event
        self._timeout = float(timeout)
        self._max_delay = float(max_delay)
        self._clock = clock
        self._started_at: Optional[float] = None

    def reset(self) -> None:
        self._started_at = None

    def next_delay_seconds(self) -> float:
        now = self._clock()
        if self._started_at is None:
            self._started_at = now
        if self._stop_event is not None and self._stop_event.is_set():
            raise CaptureRecoveryTimeout("Screen capture recovery was cancelled.")
        elapsed = now - self._started_at
        if elapsed >= self._timeout:
            raise CaptureRecoveryTimeout(
                "The display did not come back within "
                f"{self._timeout:.0f} seconds (monitor unplugged or asleep, or "
                "the graphics driver restarted)."
            )
        return max(0.01, min(self._max_delay, self._timeout - elapsed))


def install_bounded_recovery(camera: Any, stop_event: Any = None) -> bool:
    """Bound a dxcam camera's display-recovery loop; False if not possible.

    This reaches into dxcam's private ``_display_recovery._wait``. Any other
    dxcam layout leaves the camera untouched (the old unbounded behaviour)
    instead of failing.
    """
    try:
        recovery = getattr(camera, "_display_recovery", None)
        if recovery is None:
            return False
        current = getattr(recovery, "_wait", None)
        if not callable(getattr(current, "next_delay_seconds", None)):
            return False
        setattr(recovery, "_wait", BoundedRecoveryWait(stop_event))
        return True
    except Exception:
        return False


def _dxcam_factory(dxcam_module: Any) -> Any:
    try:
        return vars(dxcam_module).get("__factory")
    except TypeError:
        return None


def find_dxcam_output(dxcam_module: Any, display_name: Optional[str]) -> Optional[Tuple[int, int]]:
    """Return ``(device_idx, output_idx)`` of the dxcam output with this name.

    Qt lists screens primary-first while dxcam numbers outputs per graphics
    adapter, so the Settings index is not a dxcam index. The GUI resolves the
    selected screen to its GDI device name (for example ``\\\\.\\DISPLAY2``),
    which dxcam reports for every output; this matches it across adapters.
    """
    wanted = str(display_name or "").strip().upper()
    if not wanted:
        return None
    outputs = getattr(_dxcam_factory(dxcam_module), "outputs", None)
    if not outputs:
        return None
    try:
        for device_idx, device_outputs in enumerate(outputs):
            for output_idx, output in enumerate(device_outputs):
                try:
                    name = str(output.devicename or "").strip().upper()
                except Exception:
                    continue
                if name == wanted:
                    return device_idx, output_idx
    except TypeError:
        return None
    return None


def rebuild_capture_backend() -> bool:
    """Re-enumerate graphics adapters and displays for the next capture.

    dxcam enumerates adapters and outputs once, at import, in a singleton
    factory. A monitor plugged in later, or a graphics device removed by a
    driver reset, stays invisible (or broken) until that factory is rebuilt.
    Only a dxcam already imported by monitor sync is touched, and any other
    dxcam layout leaves the factory as it is.
    """
    if GENERAL.platform != "Windows":
        return False
    dxcam_module = sys.modules.get("dxcam")
    if dxcam_module is None:
        return False
    ScreenGrab.reset_cache()
    try:
        factory_cls = getattr(dxcam_module, "DXFactory", None)
        registry = getattr(getattr(dxcam_module, "Singleton", None), "_instances", None)
        if factory_cls is None or not isinstance(registry, dict):
            return False
        if "__factory" not in vars(dxcam_module):
            return False
    except Exception:
        return False
    previous = registry.pop(factory_cls, None)
    try:
        factory = factory_cls()
    except Exception:
        if previous is not None:
            registry[factory_cls] = previous
        return False
    setattr(dxcam_module, "__factory", factory)
    return True


def _create_dxcam_camera(dxcam_module, **kwargs):
    # BGRA is DXCam's native capture format and does not require OpenCV color
    # conversion. LumiSync converts it to RGB with NumPy in capture_array().
    kwargs = {"output_color": "BGRA", **kwargs}
    try:
        return dxcam_module.create(processor_backend="numpy", **kwargs)
    except TypeError:
        return dxcam_module.create(**kwargs)


def _display_label(display_index: int, display_name: Optional[str]) -> str:
    label = f"Display {int(display_index) + 1}"
    return f"{label} ({display_name})" if display_name else label


class ScreenGrab:
    """Facilitates taking a screenshot while supporting
    different platforms and compositors (the latter for Unix).

    ``display_index`` is the Settings index (Qt's screen order). On Windows
    ``display_name`` (the GDI device name the GUI resolved for that screen)
    picks the matching dxcam output on any graphics adapter, and the index is
    only the fallback. When the requested display cannot be captured,
    ``fallback_note`` says which one is.
    """

    # Track the current dxcam instance so we can release it when switching displays
    _dxcam_instance = None
    _dxcam_output_key: Optional[Tuple[int, int]] = None

    def __init__(
        self,
        *,
        display_index: int = 0,
        display_name: Optional[str] = None,
        stop_event: Any = None,
    ) -> None:
        self.display_index = int(display_index)
        self.display_name = display_name or None
        self.fallback_note: Optional[str] = None
        self.camera = None
        if GENERAL.platform == "Windows":
            import dxcam

            requested_output = max(0, self.display_index)
            target = find_dxcam_output(dxcam, self.display_name)
            output_key = target or (0, requested_output)

            # dxcam caches camera instances globally. Release the old instance
            # before creating one for a different output; a released camera can
            # be replaced, while a cached broken one cannot.
            if (
                ScreenGrab._dxcam_instance is not None
                and ScreenGrab._dxcam_output_key != output_key
            ):
                self._release_cached_dxcam()

            if ScreenGrab._dxcam_instance is None:
                self.camera, output_key = self._create_camera(dxcam, target, requested_output)
                ScreenGrab._dxcam_instance = self.camera
                ScreenGrab._dxcam_output_key = output_key
            else:
                self.camera = ScreenGrab._dxcam_instance

            # Bound it on every use: a cached camera may come from an earlier
            # session with a different stop event.
            install_bounded_recovery(self.camera, stop_event)
            self.capture_method = self.camera.grab
        else:
            if GENERAL.compositor == "x11":
                import mss

                self.camera = mss.mss()

                # mss.monitors[0] is the virtual bounding box; [1..n] are real monitors
                monitor_idx = self.display_index + 1
                if monitor_idx < 1 or monitor_idx >= len(self.camera.monitors):
                    monitor_idx = 1 if len(self.camera.monitors) > 1 else 0
                    self.fallback_note = (
                        f"{_display_label(self.display_index, self.display_name)} "
                        "is not available for screen capture; capturing the "
                        "first display instead."
                    )
                self.capture_method = partial(self.camera.grab, self.camera.monitors[monitor_idx])
            else:
                # TODO: Wayland capture via xdg-desktop-portal ScreenCast +
                # PipeWire. Until then, fail with an actionable message rather
                # than a raw NotImplementedError.
                raise WaylandUnsupportedError(WAYLAND_MESSAGE)

    def _create_camera(
        self,
        dxcam_module: Any,
        target: Optional[Tuple[int, int]],
        requested_output: int,
    ) -> Tuple[Any, Tuple[int, int]]:
        label = _display_label(self.display_index, self.display_name)
        if target is not None:
            try:
                camera = _create_dxcam_camera(
                    dxcam_module, device_idx=target[0], output_idx=target[1]
                )
                return camera, target
            except Exception:
                pass
        if self.display_name:
            problem = "was not found by" if target is None else "could not be opened for"
            self.fallback_note = (
                f"{label} {problem} the screen capture; capturing output "
                f"{requested_output + 1} of the first graphics adapter instead."
            )
        try:
            camera = _create_dxcam_camera(
                dxcam_module, device_idx=0, output_idx=requested_output
            )
            return camera, (0, requested_output)
        except Exception:
            pass
        # Fall back to dxcam's default output, usually the primary monitor.
        # This keeps sync usable when the saved display index is stale.
        camera = _create_dxcam_camera(dxcam_module)
        self.display_index = 0
        self.fallback_note = (
            f"{label} is not available for screen capture; capturing the "
            "primary display instead."
        )
        return camera, (0, -1)

    @classmethod
    def _release_cached_dxcam(cls) -> None:
        camera = cls._dxcam_instance
        cls._dxcam_instance = None
        cls._dxcam_output_key = None
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
        on_status=print,
        on_error=print,
    )
    session.run()
