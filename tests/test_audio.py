import unittest

import numpy as np

from lumisync.sync import audio


SAMPLE_RATE = 48000
WINDOW = 1104  # ~23 ms, matches the music capture window


def _tone(freq: float) -> np.ndarray:
    t = np.arange(WINDOW) / SAMPLE_RATE
    return np.sin(2 * np.pi * freq * t).astype(np.float32)


class SpectralBandTests(unittest.TestCase):
    def test_bass_tone_dominates_bass_band(self):
        bands = audio.spectral_bands(_tone(100), SAMPLE_RATE)
        self.assertEqual(int(np.argmax(bands)), 0)

    def test_mid_tone_dominates_mid_band(self):
        bands = audio.spectral_bands(_tone(1000), SAMPLE_RATE)
        self.assertEqual(int(np.argmax(bands)), 1)

    def test_treble_tone_dominates_treble_band(self):
        bands = audio.spectral_bands(_tone(8000), SAMPLE_RATE)
        self.assertEqual(int(np.argmax(bands)), 2)

    def test_silence_returns_zero_bands(self):
        self.assertEqual(
            audio.spectral_bands(np.zeros(WINDOW, dtype=np.float32), SAMPLE_RATE),
            (0.0, 0.0, 0.0),
        )

    def test_none_and_tiny_input_are_safe(self):
        self.assertEqual(audio.spectral_bands(None, SAMPLE_RATE), (0.0, 0.0, 0.0))
        self.assertEqual(audio.spectral_bands([0.0], SAMPLE_RATE), (0.0, 0.0, 0.0))

    def test_stereo_input_is_downmixed(self):
        stereo = np.stack([_tone(100), _tone(100)], axis=1)
        bands = audio.spectral_bands(stereo, SAMPLE_RATE)
        self.assertEqual(int(np.argmax(bands)), 0)


class BandColorTests(unittest.TestCase):
    def test_silence_is_black(self):
        self.assertEqual(audio.bands_to_color(0.0, 0.0, 0.0), (0, 0, 0))

    def test_bass_only_is_red(self):
        r, g, b = audio.bands_to_color(5.0, 0.0, 0.0, gain=1.7)
        self.assertGreater(r, g)
        self.assertGreater(r, b)
        self.assertEqual((g, b), (0, 0))

    def test_treble_only_is_blue(self):
        r, g, b = audio.bands_to_color(0.0, 0.0, 5.0, gain=1.7)
        self.assertGreater(b, r)
        self.assertGreater(b, g)

    def test_balanced_spectrum_reads_neutral(self):
        r, g, b = audio.bands_to_color(3.0, 3.0, 3.0, gain=1.7)
        self.assertEqual(r, g)
        self.assertEqual(g, b)
        self.assertGreater(r, 0)

    def test_amplitude_color_wrapper_matches_pipeline(self):
        tone = _tone(100)
        expected = audio.bands_to_color(*audio.spectral_bands(tone, SAMPLE_RATE), gain=1.7)
        self.assertEqual(audio.amplitude_color(tone, SAMPLE_RATE, gain=1.7), expected)


class PaletteTests(unittest.TestCase):
    def test_all_palettes_render_silence_black(self):
        for palette in audio.PALETTES:
            with self.subTest(palette=palette):
                self.assertEqual(
                    audio.bands_to_color(0.0, 0.0, 0.0, palette=palette), (0, 0, 0)
                )

    def test_mono_palette_is_greyscale(self):
        r, g, b = audio.bands_to_color(2.0, 1.0, 0.5, palette="mono")
        self.assertEqual(r, g)
        self.assertEqual(g, b)
        self.assertGreater(r, 0)

    def test_spectrum_palette_bass_is_reddish_treble_is_bluish(self):
        bass_r, bass_g, bass_b = audio.bands_to_color(5.0, 0.0, 0.0, palette="spectrum")
        treble_r, treble_g, treble_b = audio.bands_to_color(0.0, 0.0, 5.0, palette="spectrum")
        self.assertGreater(bass_r, bass_b)
        self.assertGreater(treble_b, treble_r)

    def test_cool_palette_stays_in_blue_family(self):
        r, g, b = audio.bands_to_color(3.0, 2.0, 4.0, palette="cool")
        self.assertGreater(b, r)

    def test_every_palette_has_a_label(self):
        for palette in audio.PALETTES:
            self.assertIn(palette, audio.PALETTE_LABELS)


if __name__ == "__main__":
    unittest.main()
