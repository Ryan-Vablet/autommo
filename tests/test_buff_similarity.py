import unittest

import numpy as np

from src.analysis.slot_analyzer import SlotAnalyzer


class BuffSimilarityTests(unittest.TestCase):
    def test_template_similarity_exact_match_is_high(self) -> None:
        arr = np.full((20, 20), 128, dtype=np.uint8)
        score = SlotAnalyzer._template_similarity(arr, arr.copy())
        self.assertGreaterEqual(score, 0.99)

    def test_template_similarity_unrelated_patterns_is_lower(self) -> None:
        roi = np.zeros((20, 20), dtype=np.uint8)
        roi[:, 10:] = 255
        tmpl = np.zeros((20, 20), dtype=np.uint8)
        tmpl[10:, :] = 255
        score = SlotAnalyzer._template_similarity(roi, tmpl)
        self.assertLess(score, 0.6)

    def test_template_similarity_uniform_region_exact_match(self) -> None:
        """Flat-region templates (resource bars) should score >= 0.99 on exact match."""
        arr = np.full((20, 20), 128, dtype=np.uint8)
        score = SlotAnalyzer._template_similarity(arr, arr.copy())
        self.assertGreaterEqual(score, 0.99)

    def test_template_similarity_resource_bar_level_detection(self) -> None:
        """Partially-filled bar at correct level scores high; wrong level scores low."""
        # Template: left half white (filled), right half black (empty)
        tmpl = np.zeros((20, 40), dtype=np.uint8)
        tmpl[:, :20] = 200
        # Matching frame
        roi_match = tmpl.copy()
        score_match = SlotAnalyzer._template_similarity(roi_match, tmpl)
        self.assertGreaterEqual(score_match, 0.95)
        # Non-matching frame: bar at different level
        roi_miss = np.zeros((20, 40), dtype=np.uint8)
        roi_miss[:, :10] = 200  # only 25% filled instead of 50%
        score_miss = SlotAnalyzer._template_similarity(roi_miss, tmpl)
        self.assertLess(score_miss, 0.7)


if __name__ == "__main__":
    unittest.main()
