import os
import platform
from types import SimpleNamespace


def _detect_display_server() -> str:
    """Detects the display server for a Unix platform."""
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    xdg_session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()

    if (
        wayland_display
        or xdg_session_type == "wayland"
        or "wayland" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    ):
        return "wayland"

    return "x11"


# TODO: Should the led option be moved under a different global?
GENERAL = SimpleNamespace(nled=20, platform=platform.system(), display_server=None)
if GENERAL.platform != "Windows":
    GENERAL.display_server = _detect_display_server()

# TODO: Replace the settings.json with this during runtime
# and only use the settings.json on restart?
CONNECTION = SimpleNamespace(
    default=SimpleNamespace(
        multicast="255.255.255.255",
        port=4001,
        listen_port=4002,
        timeout=1,
    ),
    devices=[],
)
# NOTE: Duration (seconds)
AUDIO = SimpleNamespace(sample_rate=48000, duration=0.01)

# TODO: This needs to change as soon as support for multiple devices
# is being implemented -> Similar with next as for the devices query?
COLORS = SimpleNamespace(previous=[], current=[])

# NOTE: Brightness settings for different sync modes (percent)
BRIGHTNESS = SimpleNamespace(monitor=0.75, music=0.85)
