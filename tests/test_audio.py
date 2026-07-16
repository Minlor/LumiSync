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

    def test_fft_level_is_normalized_and_tracks_source_loudness(self):
        quiet = sum(audio.spectral_bands(_tone(100) * 0.05, SAMPLE_RATE))
        loud = sum(audio.spectral_bands(_tone(100) * 0.50, SAMPLE_RATE))

        self.assertGreater(loud, quiet * 8.0)
        self.assertLess(loud, 1.0)


class AutoGainTests(unittest.TestCase):
    @staticmethod
    def _settle(gain, bands, frames=600):
        out = (0.0, 0.0, 0.0)
        for _ in range(frames):
            out = gain.process(bands)
        return out

    def test_master_volume_no_longer_sets_brightness(self):
        # The same track balance at very different capture amplitudes (i.e.
        # different master-volume settings) should normalize to the same level.
        loud = self._settle(audio.AutoGain(), (0.30, 0.15, 0.05))
        quiet = self._settle(audio.AutoGain(), (0.030, 0.015, 0.005))

        self.assertAlmostEqual(sum(loud), sum(quiet), delta=0.02)
        # And both land near the normalizer's target, not near the raw input.
        self.assertAlmostEqual(sum(loud), audio.AutoGain.TARGET, delta=0.03)

    def test_band_balance_is_preserved(self):
        gain = audio.AutoGain()
        bass, mid, treble = self._settle(gain, (0.30, 0.15, 0.05))
        total = bass + mid + treble
        self.assertAlmostEqual(bass / total, 0.6, delta=0.02)
        self.assertAlmostEqual(treble / total, 0.1, delta=0.02)

    def test_beats_still_rise_above_a_steady_bed(self):
        # A quiet bed with periodic loud beats: the beat frame must stay
        # meaningfully louder than the bed even after normalization, so the
        # slow envelope doesn't flatten musical dynamics.
        gain = audio.AutoGain()
        bed = (0.04, 0.02, 0.01)
        beat = (0.30, 0.15, 0.05)
        for i in range(600):
            gain.process(beat if i % 30 == 0 else bed)
        bed_level = sum(gain.process(bed))
        beat_level = sum(gain.process(beat))
        self.assertGreater(beat_level, bed_level * 2.0)

    def test_silence_passes_through_dark(self):
        gain = audio.AutoGain()
        self._settle(gain, (0.30, 0.15, 0.05))
        self.assertEqual(gain.process((0.0, 0.0, 0.0)), (0.0, 0.0, 0.0))

    def test_noise_floor_is_not_amplified_to_full(self):
        # A near-silent stream must not be boosted into a bright glow.
        gain = audio.AutoGain()
        out = self._settle(gain, (6e-5, 3e-5, 1e-5))
        self.assertLessEqual(sum(out), audio.AutoGain.TARGET)
        level = min(1.0, np.sqrt(sum(out)) * 1.7)
        self.assertLess(level, 0.25)


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

    def test_every_palette_explains_its_behavior(self):
        for palette in audio.PALETTES:
            self.assertIn(palette, audio.PALETTE_DESCRIPTIONS)

    def test_rich_palettes_produce_distinct_color_families(self):
        colors = {
            audio.bands_to_color(2.0, 3.0, 4.0, palette=palette)
            for palette in ("aurora", "sunset", "ocean", "neon", "jewel", "fire")
        }
        self.assertGreaterEqual(len(colors), 5)

    def test_album_art_palette_uses_supplied_colors(self):
        color = audio.bands_to_color(
            1.0,
            1.0,
            1.0,
            palette=audio.PALETTE_ALBUM_ART,
            palette_colors=[(255, 0, 0)],
        )
        self.assertGreater(color[0], 0)
        self.assertEqual(color[1:], (0, 0))

    def test_album_art_palette_falls_back_when_artwork_is_unavailable(self):
        color = audio.bands_to_color(
            1.0,
            1.0,
            1.0,
            palette=audio.PALETTE_ALBUM_ART,
        )
        self.assertNotEqual(color, (0, 0, 0))

    def test_rotating_palette_moves_only_while_audio_is_active(self):
        renderer = audio.MusicPatternRenderer(4)
        first = renderer.render(
            (0.08, 0.04, 0.02), reaction="pulse", palette="rainbow"
        )
        for _ in range(24):
            moved = renderer.render(
                (0.08, 0.04, 0.02), reaction="pulse", palette="rainbow"
            )
        self.assertNotEqual(first, moved)

        phase = renderer._palette_phase
        renderer.render((0.0, 0.0, 0.0), reaction="pulse", palette="rainbow")
        self.assertEqual(renderer._palette_phase, phase)


class MusicPatternTests(unittest.TestCase):
    BANDS = (5.0, 3.0, 1.0)

    def test_every_reaction_has_product_copy(self):
        for reaction in audio.REACTIONS:
            self.assertIn(reaction, audio.REACTION_LABELS)
            self.assertIn(reaction, audio.REACTION_DESCRIPTIONS)

    def test_auto_director_holds_a_reaction_for_a_musical_phrase(self):
        renderer = audio.MusicPatternRenderer(8)
        renderer.render((0.20, 0.02, 0.01), reaction="auto")
        selected = renderer._auto_reaction

        renderer.render((0.01, 0.01, 0.20), reaction="auto")

        self.assertIn(selected, ("pulse", "center_burst"))
        self.assertEqual(renderer._auto_reaction, selected)
        self.assertGreater(renderer._auto_hold_seconds, 0.0)

    def test_auto_director_reselects_from_current_spectrum(self):
        renderer = audio.MusicPatternRenderer(8)
        renderer.render((0.20, 0.02, 0.01), reaction="auto")

        # Feed a steady treble passage so the smoothed decision features track
        # it and the phantom transient from the first frame decays away, then
        # let the phrase expire before the next decision.
        for _ in range(40):
            renderer.render((0.01, 0.01, 0.20), reaction="auto")
        renderer._auto_hold_seconds = 0.0
        renderer.render((0.01, 0.01, 0.20), reaction="auto")

        self.assertIn(renderer._auto_reaction, ("twinkle", "wave", "chase"))

    def test_auto_director_crossfades_out_of_the_previous_frame(self):
        renderer = audio.MusicPatternRenderer(8)
        renderer.render((0.20, 0.02, 0.01), reaction="auto")
        # Force an immediate reselection into a different category.
        renderer._auto_hold_seconds = 0.0
        renderer._auto_treble_share = 0.9
        renderer._auto_bass_share = 0.0
        renderer._auto_transient_peak = 0.0
        renderer.render((0.01, 0.01, 0.20), reaction="auto")

        # A crossfade is now in progress, blending out of the prior frame.
        self.assertGreater(renderer._auto_fade, 0.0)
        self.assertLessEqual(renderer._auto_fade, 1.0)

    def test_auto_director_exposes_the_concrete_active_reaction(self):
        renderer = audio.MusicPatternRenderer(8)
        renderer.render((0.20, 0.02, 0.01), reaction="auto")

        self.assertEqual(renderer.active_reaction, renderer._auto_reaction)
        self.assertNotEqual(renderer.active_reaction, audio.REACTION_AUTO)

    def test_every_reaction_is_dark_without_an_audio_signal(self):
        for reaction in audio.REACTIONS:
            with self.subTest(reaction=reaction):
                frame = audio.MusicPatternRenderer(8).render(
                    (0.0, 0.0, 0.0), reaction=reaction
                )
                self.assertEqual(frame, [(0, 0, 0)] * 8)

    def test_every_reaction_changes_with_audio_loudness(self):
        quiet = (0.0004, 0.0002, 0.0001)
        loud = (0.16, 0.08, 0.04)

        for reaction in audio.REACTIONS:
            with self.subTest(reaction=reaction):
                quiet_frame = audio.MusicPatternRenderer(8).render(
                    quiet, reaction=reaction, gain=1.0
                )
                loud_frame = audio.MusicPatternRenderer(8).render(
                    loud, reaction=reaction, gain=1.0
                )
                self.assertGreater(
                    sum(sum(color) for color in loud_frame),
                    sum(sum(color) for color in quiet_frame),
                )

    def test_every_reaction_changes_with_frequency_balance(self):
        bass = (0.12, 0.0, 0.0)
        treble = (0.0, 0.0, 0.12)

        for reaction in audio.REACTIONS:
            with self.subTest(reaction=reaction):
                bass_frame = audio.MusicPatternRenderer(8).render(
                    bass, reaction=reaction, palette="rgb"
                )
                treble_frame = audio.MusicPatternRenderer(8).render(
                    treble, reaction=reaction, palette="rgb"
                )
                self.assertNotEqual(bass_frame, treble_frame)

    def test_every_reaction_uses_fft_analysis_from_real_audio_samples(self):
        bass_audio = audio.spectral_bands(_tone(100) * 0.4, SAMPLE_RATE)
        treble_audio = audio.spectral_bands(_tone(8000) * 0.4, SAMPLE_RATE)

        for reaction in audio.REACTIONS:
            with self.subTest(reaction=reaction):
                bass_frame = audio.MusicPatternRenderer(8).render(
                    bass_audio, reaction=reaction, palette="rgb"
                )
                treble_frame = audio.MusicPatternRenderer(8).render(
                    treble_audio, reaction=reaction, palette="rgb"
                )
                self.assertNotEqual(bass_frame, treble_frame)
                self.assertTrue(
                    any(color != (0, 0, 0) for color in bass_frame)
                    or any(color != (0, 0, 0) for color in treble_frame)
                )

    def test_flow_moves_new_color_across_the_strip(self):
        renderer = audio.MusicPatternRenderer(4)

        first = renderer.render(self.BANDS, reaction="flow")
        second = renderer.render(self.BANDS, reaction="flow")

        self.assertEqual(first[:3], [(0, 0, 0)] * 3)
        self.assertNotEqual(first[3], (0, 0, 0))
        self.assertEqual(second[:2], [(0, 0, 0)] * 2)
        self.assertNotEqual(second[2], (0, 0, 0))

    def test_pulse_lights_every_zone_together(self):
        frame = audio.MusicPatternRenderer(6).render(
            self.BANDS, reaction="pulse"
        )

        self.assertNotEqual(frame[0], (0, 0, 0))
        self.assertEqual(frame, [frame[0]] * 6)

    def test_energy_fill_grows_with_louder_audio(self):
        quiet = audio.MusicPatternRenderer(12).render(
            (0.002, 0.0007, 0.0003),
            reaction="energy_fill",
            gain=1.0,
        )
        loud = audio.MusicPatternRenderer(12).render(
            (0.20, 0.07, 0.03),
            reaction="energy_fill",
            gain=1.0,
        )

        quiet_lit = sum(color != (0, 0, 0) for color in quiet)
        loud_lit = sum(color != (0, 0, 0) for color in loud)
        self.assertGreater(loud_lit, quiet_lit)

    def test_energy_fill_rewards_activity_across_more_frequency_bands(self):
        single_band = audio.MusicPatternRenderer(12).render(
            (0.18, 0.0, 0.0),
            reaction="energy_fill",
            gain=1.0,
            palette="mono",
        )
        rich_spectrum = audio.MusicPatternRenderer(12).render(
            (0.06, 0.06, 0.06),
            reaction="energy_fill",
            gain=1.0,
            palette="mono",
        )

        single_lit = sum(color != (0, 0, 0) for color in single_band)
        rich_lit = sum(color != (0, 0, 0) for color in rich_spectrum)
        self.assertGreater(rich_lit, single_lit)

    def test_energy_fill_recedes_and_fades_after_audio_stops(self):
        renderer = audio.MusicPatternRenderer(12)
        peak = renderer.render((0.25, 0.12, 0.06), reaction="energy_fill")

        faded = peak
        for _ in range(4):
            faded = renderer.render((0.0, 0.0, 0.0), reaction="energy_fill")

        self.assertLess(
            sum(sum(color) for color in faded),
            sum(sum(color) for color in peak),
        )

    def test_band_split_assigns_bass_mid_and_treble_regions(self):
        frame = audio.MusicPatternRenderer(6).render(
            self.BANDS, reaction="band_split", palette="rgb"
        )

        self.assertEqual(frame[0], frame[1])
        self.assertEqual(frame[2], frame[3])
        self.assertEqual(frame[4], frame[5])
        self.assertGreater(frame[0][0], frame[0][1])
        self.assertGreater(frame[2][1], frame[2][0])
        self.assertGreater(frame[4][2], frame[4][0])

    def test_center_burst_expands_outward(self):
        renderer = audio.MusicPatternRenderer(5)

        first = renderer.render(self.BANDS, reaction="center_burst")
        second = renderer.render(self.BANDS, reaction="center_burst")

        self.assertEqual(
            [index for index, color in enumerate(first) if color != (0, 0, 0)],
            [2],
        )
        self.assertEqual(
            [index for index, color in enumerate(second) if color != (0, 0, 0)],
            [1, 2, 3],
        )

    def test_center_burst_emphasizes_a_sudden_audio_transient(self):
        renderer = audio.MusicPatternRenderer(5)
        renderer.render((0.002, 0.001, 0.001), reaction="center_burst")

        transient = renderer.render(
            (0.20, 0.02, 0.01), reaction="center_burst"
        )
        center = transient[2]
        edge_of_previous_ripple = transient[1]

        self.assertGreater(sum(center), sum(edge_of_previous_ripple))

    def test_wave_rolls_different_brightnesses_across_the_strip(self):
        renderer = audio.MusicPatternRenderer(8)

        first = renderer.render(self.BANDS, reaction="wave")
        second = renderer.render(self.BANDS, reaction="wave")

        self.assertGreater(len(set(first)), 2)
        self.assertNotEqual(first, second)

    def test_bass_bounce_fills_symmetrically_from_the_ends(self):
        frame = audio.MusicPatternRenderer(6).render(
            (1.0, 0.0, 0.0), reaction="bass_bounce", gain=0.3
        )

        lit = [index for index, color in enumerate(frame) if color != (0, 0, 0)]
        self.assertEqual(lit, [0, 5])

    def test_chase_moves_a_bright_head_and_leaves_a_trail(self):
        renderer = audio.MusicPatternRenderer(6)

        first = renderer.render(self.BANDS, reaction="chase")
        second = renderer.render(self.BANDS, reaction="chase")

        self.assertNotEqual(first[0], (0, 0, 0))
        self.assertNotEqual(second[1], (0, 0, 0))
        self.assertNotEqual(second[0], (0, 0, 0))
        self.assertNotEqual(first, second)

    def test_chase_speed_increases_with_audio_level(self):
        quiet = audio.MusicPatternRenderer(8)
        loud = audio.MusicPatternRenderer(8)

        quiet.render((0.001, 0.0005, 0.0002), reaction="chase", gain=1.0)
        loud.render((0.3, 0.15, 0.06), reaction="chase", gain=1.0)

        self.assertGreater(loud._chase_position, quiet._chase_position)

    def test_twinkle_scatter_changes_while_old_points_fade(self):
        renderer = audio.MusicPatternRenderer(8)

        first = renderer.render((1.0, 2.0, 6.0), reaction="twinkle")
        second = renderer.render((1.0, 2.0, 6.0), reaction="twinkle")

        self.assertGreater(
            sum(color != (0, 0, 0) for color in first),
            1,
        )
        self.assertNotEqual(first, second)

    def test_every_reaction_returns_one_valid_color_per_segment(self):
        for reaction in audio.REACTIONS:
            with self.subTest(reaction=reaction):
                frame = audio.MusicPatternRenderer(7).render(
                    self.BANDS, reaction=reaction
                )
                self.assertEqual(len(frame), 7)
                self.assertTrue(
                    all(
                        len(color) == 3
                        and all(0 <= channel <= 255 for channel in color)
                        for color in frame
                    )
                )

    def test_switching_reactions_clears_previous_motion(self):
        renderer = audio.MusicPatternRenderer(4)
        renderer.render(self.BANDS, reaction="pulse")

        flow = renderer.render(self.BANDS, reaction="flow")

        self.assertEqual(flow[:3], [(0, 0, 0)] * 3)
        self.assertNotEqual(flow[3], (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
