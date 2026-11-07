import unittest
import numpy as np
from shared.pao_alignment import text_anchor_candidates, normalized_audio_match


class PaoAlignmentTests(unittest.TestCase):
    def test_contiguous_tokens_cross_segments_but_not_parts(self):
        phrase = "one two three four five six seven eight nine ten"
        ia = [dict(id="ia", recordingId="r", start=0, end=8, text=phrase)]
        yt = [dict(id="y1", part=1, start=10, end=14, text="one two three four five"), dict(id="y2", part=1, start=14, end=18, text="six seven eight nine ten")]
        result = text_anchor_candidates(ia, yt)
        self.assertEqual(result["anchors"][0]["youtube"]["segmentIds"], ["y1", "y2"])
        yt[1]["part"] = 2
        self.assertEqual(len(text_anchor_candidates(ia, yt)["anchors"]), 0)

    def test_repeated_phrase_never_becomes_unique_anchor(self):
        phrase = "one two three four five six seven eight nine ten"
        ia = [dict(id="ia", recordingId="r", start=0, end=8, text=phrase)]
        yt = [dict(id="y1", part=1, start=10, end=18, text=phrase), dict(id="y2", part=2, start=14, end=22, text=phrase)]
        result = text_anchor_candidates(ia, yt)
        self.assertEqual(result["anchors"], [])
        self.assertEqual(result["ambiguous"][0]["ytOccurrences"], 2)

    def test_acoustic_gain_offset_and_bounds(self):
        rng = np.random.default_rng(9)
        reference = rng.normal(size=8000)
        target = rng.normal(scale=.01, size=20000)
        target[4000:12000] = reference * .2 + .04
        result = normalized_audio_match(reference, target, 4000)
        self.assertEqual(result["verification"], "verified-bounded-acoustic")
        self.assertEqual(result["offsetSeconds"], 1)
        self.assertEqual(result["durationSeconds"], 2)
        filtered = normalized_audio_match(reference, target, 4000, lowpass_hz=450)
        self.assertEqual(filtered["verification"], "verified-bounded-acoustic")
        self.assertEqual(filtered["offsetSeconds"], 1)
        self.assertEqual(normalized_audio_match(np.zeros(8000), target, 4000)["verification"], "unknown")
        target[12000:20000] = reference * .2
        self.assertEqual(normalized_audio_match(reference, target, 4000)["verification"], "unknown")


if __name__ == "__main__":
    unittest.main()
