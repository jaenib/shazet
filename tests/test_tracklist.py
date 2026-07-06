import unittest

from shazet import tracklist


def seg(idx, track_key, artist="Artist", title="Title", matched=True, confidence=None):
    return {
        "idx": idx,
        "offset_seconds": idx * 60,
        "matched": 1 if matched else 0,
        "track_key": track_key,
        "artist": artist,
        "title": title,
        "genre": "",
        "cover_url": "",
        "confidence": confidence,
        "flags": [],
    }


class TracklistTests(unittest.TestCase):
    def test_adjacent_same_track_segments_merge_into_range(self):
        segments = [
            seg(0, "A", artist="Mementomor", title="Expression", confidence=62),
            seg(1, "A", artist="Mementomor", title="Expression", confidence=70),
            seg(2, "B", artist="Other", title="Song"),
        ]
        entries = tracklist.build_entries(segments, 60)
        self.assertEqual(len(entries), 2)
        self.assertTrue(entries[0].is_range)
        self.assertEqual(entries[0].start_seconds, 0)
        self.assertEqual(entries[0].end_seconds, 120)
        self.assertEqual(entries[0].confidence, 70)

    def test_unmatched_gap_splits_entries(self):
        segments = [seg(0, "A"), seg(1, "", matched=False), seg(2, "A")]
        entries = tracklist.build_entries(segments, 60)
        self.assertEqual(len(entries), 2)

    def test_text_export_is_setseeker_compatible(self):
        segments = [
            seg(0, "A", artist="Mementomor", title="Expression"),
            seg(1, "A", artist="Mementomor", title="Expression"),
            seg(2, "B", artist="Tiscore", title="Red Card"),
        ]
        text = tracklist.entries_to_text(tracklist.build_entries(segments, 60))
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], "Final Tracklist:")
        self.assertEqual(lines[1], "[00:00:00-00:02:00] Mementomor - Expression")
        self.assertEqual(lines[2], "[00:02:00] Tiscore - Red Card")

    def test_cue_export_contains_tracks(self):
        segments = [seg(0, "A", artist="X", title="Y")]
        cue = tracklist.entries_to_cue(tracklist.build_entries(segments, 60), "My Set")
        self.assertIn('TITLE "My Set"', cue)
        self.assertIn("TRACK 01 AUDIO", cue)
        self.assertIn("INDEX 01 00:00:00", cue)


if __name__ == "__main__":
    unittest.main()
