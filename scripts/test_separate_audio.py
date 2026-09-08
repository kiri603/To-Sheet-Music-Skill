import unittest
from urllib.error import URLError
import numpy as np

from separate_audio import prepare_mix, diagnose_stems, select_roles, load_model_with_retry


class SeparationTests(unittest.TestCase):
    def test_transient_download_error_retries_then_succeeds(self):
        failures = iter([URLError("temporary EOF"), URLError("timeout"), "model"])
        def loader(name):
            result = next(failures)
            if isinstance(result, Exception):
                raise result
            return result
        self.assertEqual(load_model_with_retry(loader, "test", pause=0), "model")

    def test_download_retries_are_bounded(self):
        count = []
        def loader(name):
            count.append(name)
            raise URLError("offline")
        with self.assertRaises(URLError):
            load_model_with_retry(loader, "test", pause=0)
        self.assertEqual(len(count), 3)

    def test_antiphase_stereo_is_not_treated_as_silence(self):
        wave = np.sin(np.arange(4000) / 10).astype(np.float32)
        mix, mean, scale = prepare_mix(np.stack([wave, -wave], axis=1))
        self.assertTrue(np.isfinite(mix).all())
        self.assertGreater(scale, 0.1)
        np.testing.assert_allclose(mix.T * scale + mean, np.stack([wave, -wave], axis=1), atol=1e-6)

    def test_silent_input_fails_instead_of_dividing_by_zero(self):
        with self.assertRaisesRegex(ValueError, "silent"):
            prepare_mix(np.zeros((100, 2), dtype=np.float32))

    def test_timeline_mismatch_fails(self):
        with self.assertRaisesRegex(ValueError, "shape"):
            diagnose_stems(np.ones((100, 2)), {"bass": np.ones((99, 2))})

    def test_exact_sum_is_reported_without_quality_claim(self):
        source = np.ones((100, 2), dtype=np.float32)
        result = diagnose_stems(source, {"a": source * 0.3, "b": source * 0.7})
        self.assertLess(result["reconstruction_error_db"], -100)
        self.assertFalse(result["separation_quality_verified"])

    def test_six_source_roles_remain_candidates(self):
        roles = select_roles({"htdemucs_ft": {"stems": {"bass": "four/bass.wav", "drums": "four/drums.wav", "vocals": "four/vocals.wav", "other": "four/other.wav"}},
                              "htdemucs_6s": {"stems": {"guitar": "six/guitar.wav", "piano": "six/piano.wav", "other": "six/other.wav"}}})
        self.assertEqual(roles["bass"], "four/bass.wav")
        self.assertIn("guitar_candidate", roles)
        self.assertNotIn("lead_guitar", roles)
        self.assertNotIn("rhythm_guitar", roles)


if __name__ == "__main__":
    unittest.main()
