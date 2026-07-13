from io import BytesIO
import threading
import unittest

from PIL import Image, ImageDraw

from lumisync.sync import artwork


def _sample_artwork() -> bytes:
    width = 90
    image = Image.new("RGB", (width, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width // 3 - 1, 29), fill=(230, 30, 45))
    draw.rectangle(
        (width // 3, 0, 2 * width // 3 - 1, 29), fill=(25, 200, 90)
    )
    draw.rectangle(
        (2 * width // 3, 0, width - 1, 29), fill=(35, 80, 230)
    )
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ArtworkPaletteTests(unittest.TestCase):
    def test_extract_palette_finds_rich_dominant_colors(self):
        palette = artwork.extract_palette(_sample_artwork())

        self.assertGreaterEqual(len(palette), 3)
        self.assertTrue(any(r > g and r > b for r, g, b in palette))
        self.assertTrue(any(g > r and g > b for r, g, b in palette))
        self.assertTrue(any(b > r and b > g for r, g, b in palette))

    def test_extract_palette_handles_invalid_or_missing_artwork(self):
        self.assertEqual(artwork.extract_palette(None), [])
        self.assertEqual(artwork.extract_palette(b"not an image"), [])

    def test_provider_updates_from_a_media_thumbnail(self):
        provider = artwork.ArtworkPaletteProvider(fetcher=_sample_artwork)

        colors = provider.refresh_now()

        self.assertGreaterEqual(len(colors), 3)

    def test_provider_clears_to_fallback_when_media_lookup_fails(self):
        def fail() -> bytes:
            raise RuntimeError("player disappeared")

        provider = artwork.ArtworkPaletteProvider(fetcher=fail)
        self.assertEqual(provider.refresh_now(), ())

    def test_get_colors_never_waits_for_the_media_provider(self):
        release = threading.Event()

        def wait_for_release() -> bytes:
            release.wait(timeout=1.0)
            return _sample_artwork()

        provider = artwork.ArtworkPaletteProvider(fetcher=wait_for_release)
        self.assertEqual(provider.get_colors(), ())
        release.set()
        worker = provider._worker
        if worker is not None:
            worker.join(timeout=1.0)
        self.assertGreaterEqual(len(provider.get_colors()), 3)


if __name__ == "__main__":
    unittest.main()
