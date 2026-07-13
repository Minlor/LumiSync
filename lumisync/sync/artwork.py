"""Non-blocking color palettes from the current Windows media artwork."""

from __future__ import annotations

import asyncio
import colorsys
from io import BytesIO
import sys
import threading
import time
from typing import Callable

from PIL import Image, UnidentifiedImageError

from .audio import RGB


MAX_ARTWORK_BYTES = 8 * 1024 * 1024


def extract_palette(image_bytes: bytes | None, color_count: int = 6) -> list[RGB]:
    """Extract a compact, saturated palette from encoded cover artwork."""
    if not image_bytes or color_count <= 0:
        return []

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
            image.thumbnail((96, 96), Image.Resampling.LANCZOS)
            quantized = image.quantize(
                colors=max(8, min(32, color_count * 3)),
                method=Image.Quantize.MEDIANCUT,
            )
            color_table = quantized.getpalette() or []
            indexed_colors = quantized.getcolors() or []
    except (OSError, UnidentifiedImageError, ValueError):
        return []

    candidates: list[tuple[float, RGB]] = []
    for population, index in indexed_colors:
        offset = int(index) * 3
        if offset + 2 >= len(color_table):
            continue
        red, green, blue = color_table[offset : offset + 3]
        hue, saturation, value = colorsys.rgb_to_hsv(
            red / 255.0,
            green / 255.0,
            blue / 255.0,
        )
        if value < 0.08:
            continue

        # Preserve genuinely neutral artwork, but give existing hues enough
        # saturation and luminance to remain recognizable on an LED strip.
        if saturation >= 0.08:
            saturation = min(1.0, max(0.38, saturation * 1.16))
        value = min(1.0, max(0.34, value * 1.08))
        enriched = colorsys.hsv_to_rgb(hue, saturation, value)
        color = tuple(int(round(channel * 255)) for channel in enriched)
        score = float(population) * (0.35 + saturation) * (0.30 + value)
        candidates.append((score, color))

    palette: list[RGB] = []
    for _score, color in sorted(candidates, reverse=True):
        if all(
            sum((first - second) ** 2 for first, second in zip(color, existing))
            >= 34**2
            for existing in palette
        ):
            palette.append(color)
        if len(palette) >= color_count:
            break
    return palette


async def _current_artwork_bytes() -> bytes | None:
    if sys.platform != "win32":
        return None

    # Import only on Windows so Linux installs can still import music sync.
    from winrt.windows.media.control import (  # type: ignore[import-not-found]
        GlobalSystemMediaTransportControlsSessionManager,
    )
    from winrt.windows.storage.streams import (  # type: ignore[import-not-found]
        DataReader,
    )

    manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
    session = manager.get_current_session() if manager is not None else None
    if session is None:
        return None
    properties = await session.try_get_media_properties_async()
    thumbnail = properties.thumbnail if properties is not None else None
    if thumbnail is None:
        return None

    stream = await thumbnail.open_read_async()
    reader = None
    try:
        size = min(int(stream.size), MAX_ARTWORK_BYTES)
        if size <= 0:
            return None
        reader = DataReader(stream.get_input_stream_at(0))
        loaded = int(await reader.load_async(size))
        if loaded <= 0:
            return None
        payload = bytearray(loaded)
        reader.read_bytes(payload)
        return bytes(payload)
    finally:
        if reader is not None:
            try:
                reader.detach_stream()
            except (OSError, RuntimeError):
                pass
            reader.close()
        stream.close()


def read_current_artwork_bytes() -> bytes | None:
    """Read the current media thumbnail from a worker-thread event loop."""
    try:
        return asyncio.run(_current_artwork_bytes())
    except (ImportError, OSError, RuntimeError):
        return None


class ArtworkPaletteProvider:
    """Poll artwork in the background and expose the most recent palette."""

    def __init__(
        self,
        poll_interval: float = 8.0,
        fetcher: Callable[[], bytes | None] = read_current_artwork_bytes,
    ) -> None:
        self.poll_interval = max(1.0, float(poll_interval))
        self._fetcher = fetcher
        self._colors: tuple[RGB, ...] = ()
        self._next_poll = 0.0
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    def get_colors(self) -> tuple[RGB, ...]:
        """Return immediately, starting a refresh in the background if due."""
        worker_to_start = None
        with self._lock:
            now = time.monotonic()
            if now >= self._next_poll and (
                self._worker is None or not self._worker.is_alive()
            ):
                self._next_poll = now + self.poll_interval
                worker_to_start = threading.Thread(
                    target=self._refresh_worker,
                    name="LumiSyncArtworkPalette",
                    daemon=True,
                )
                self._worker = worker_to_start
            colors = self._colors
        if worker_to_start is not None:
            worker_to_start.start()
        return colors

    def refresh_now(self) -> tuple[RGB, ...]:
        """Refresh synchronously; primarily useful for tests and diagnostics."""
        try:
            colors = tuple(extract_palette(self._fetcher()))
        except Exception:
            # Media providers are external to LumiSync. A player disappearing
            # mid-read should simply activate the palette's automatic fallback.
            colors = ()
        with self._lock:
            self._colors = colors
        return colors

    def _refresh_worker(self) -> None:
        try:
            self.refresh_now()
        finally:
            with self._lock:
                self._worker = None
