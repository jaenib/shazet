import unittest

from shazet import scoring


def seg(idx, track_key, matched=True, genre="", bpm=None, segment_id=None):
    return {
        "id": segment_id if segment_id is not None else idx + 1000,
        "idx": idx,
        "matched": 1 if matched else 0,
        "track_key": track_key,
        "genre": genre,
        "bpm": bpm,
    }


def score_for(segments, idx):
    scores = scoring.score_segments(segments)
    return scores[idx + 1000]


class ScoringTests(unittest.TestCase):
    def test_long_run_scores_higher_than_single_hit(self):
        segments = [seg(0, "A"), seg(1, "A"), seg(2, "A"), seg(3, "B"), seg(4, "C"), seg(5, "C"), seg(6, "C")]
        run_score, _ = score_for(segments, 0)
        single_score, single_flags = score_for(segments, 3)
        self.assertGreater(run_score, single_score)
        self.assertIn("single-segment hit", single_flags)

    def test_sandwiched_single_hit_is_penalized_hard(self):
        segments = [seg(0, "A"), seg(1, "A"), seg(2, "X"), seg(3, "A"), seg(4, "A")]
        sandwich_score, sandwich_flags = score_for(segments, 2)

        lonely = [seg(0, "A"), seg(1, "A"), seg(2, "X"), seg(3, "B"), seg(4, "B")]
        lonely_score, _ = score_for(lonely, 2)

        self.assertLess(sandwich_score, lonely_score)
        self.assertTrue(any("interrupts a continuous run" in flag for flag in sandwich_flags))

    def test_scattered_single_repeats_are_penalized(self):
        segments = [seg(0, "A"), seg(1, "B"), seg(2, "B"), seg(3, "C"), seg(4, "A")]
        _, flags = score_for(segments, 4)
        self.assertTrue(any("scattered" in flag for flag in flags))

    def test_genre_off_profile_is_penalized(self):
        segments = [
            seg(0, "A", genre="Electronic"),
            seg(1, "A", genre="Electronic"),
            seg(2, "B", genre="Electronic"),
            seg(3, "B", genre="Electronic"),
            seg(4, "K", genre="Country"),
            seg(5, "C", genre="Electronic"),
            seg(6, "C", genre="Electronic"),
        ]
        off_score, off_flags = score_for(segments, 4)
        on_score, on_flags = score_for(segments, 0)
        self.assertLess(off_score, on_score)
        self.assertTrue(any("genre off-profile" in flag for flag in off_flags))
        self.assertTrue(any("genre matches" in flag for flag in on_flags))

    def test_bpm_outlier_is_penalized_and_half_time_is_not(self):
        segments = [
            seg(0, "A", bpm=140),
            seg(1, "A", bpm=140),
            seg(2, "B", bpm=141),
            seg(3, "B", bpm=141),
            seg(4, "H", bpm=70),
            seg(5, "K", bpm=95),
            seg(6, "C", bpm=139),
            seg(7, "C", bpm=139),
        ]
        _, half_time_flags = score_for(segments, 4)
        outlier_score, outlier_flags = score_for(segments, 5)

        self.assertFalse(any("far from set median" in flag for flag in half_time_flags))
        self.assertTrue(any("far from set median" in flag for flag in outlier_flags))

    def test_unmatched_segments_break_runs(self):
        segments = [seg(0, "A"), seg(1, "", matched=False), seg(2, "A")]
        scores = scoring.score_segments(segments)
        self.assertEqual(len(scores), 2)
        _, flags = scores[1000]
        self.assertIn("single-segment hit", flags)


if __name__ == "__main__":
    unittest.main()
