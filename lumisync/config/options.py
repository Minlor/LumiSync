import os
import platform
from types import SimpleNamespace


def _detect_compositor() -> str:
    """Detects the display server for a Unix platform."""
    if (
        os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or "wayland" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    ):
        return "wayland"

    return "x11"


# TODO: Should the led option be moved under a different global?
GENERAL = SimpleNamespace(nled=20, platform=platform.system(), compositor=None)
if GENERAL.platform != "Windows":
    GENERAL.compositor = _detect_compositor()

# TODO: Replace the settings.json with this during runtime
# and only use the settings.json on restart?
CONNECTION = SimpleNamespace(
    default=SimpleNamespace(
        multicast="239.255.255.250",
        port=4001,
        control_port=4003,
        listen_port=4002,
        timeout=1,
    ),
    devices=[],
)
# NOTE: Duration (seconds). The music window is a little longer so the FFT has
# enough samples (~1100 at 48 kHz) to separate bass from treble cleanly.
AUDIO = SimpleNamespace(sample_rate=48000, duration=0.01, music_window=0.023)

# TODO: This needs to change as soon as support for multiple devices
# is being implemented -> Similar with next as for the devices query?
COLORS = SimpleNamespace(previous=[], current=[])

# NOTE: Brightness settings for different sync modes (percent)
BRIGHTNESS = SimpleNamespace(monitor=0.75, music=0.85)

# Tunables for the real-time sync pipeline. These replace the old "blast ten
# interpolated packets per frame" behaviour with per-frame temporal smoothing,
# which is both lower latency and far gentler on the strip's packet queue.
SYNC = SimpleNamespace(
    # Monitor sync
    monitor_fps=45,          # frame pacing cap; capture stays responsive, CPU stays sane
    smoothing=0.55,          # EMA factor 0..1 (higher = snappier, lower = smoother)
    gamma_correct=True,      # average zone pixels in linear light for accurate color
    saturation=1.15,         # >1 makes ambient color pop like DreamView; 1.0 disables
    delta_threshold=3,       # skip a UDP send when no channel moved more than this
    # Music sync
    music_fps=60,            # cap on how often music frames are emitted
    music_gain=1.7,          # scales band energy into the 0..255 color range
    music_auto_gain=True,    # normalize loudness so master volume doesn't set brightness
    music_smoothing=0.6,     # EMA factor for the scrolling music colors
    music_palette="rgb",     # selected audio-reactive color palette
    music_reaction="flow",   # selected audio-driven spatial reaction
)
