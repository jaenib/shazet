import tempfile
import unittest
from pathlib import Path

from shazet import db


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        db.init_db(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_shazam_cache_roundtrip_prevents_reshazaming(self):
        with db.connect(self.db_path) as conn:
            self.assertIsNone(db.cache_lookup(conn, "sha-1"))
            db.cache_store(conn, "sha-1", {"artist": "A", "title": "T", "track_key": "k1", "genre": "House"})
            cached = db.cache_lookup(conn, "sha-1")
            match = db.cached_match_to_dict(cached)

        self.assertEqual(match["artist"], "A")
        self.assertEqual(match["track_key"], "k1")

    def test_cache_stores_no_match_results_too(self):
        with db.connect(self.db_path) as conn:
            db.cache_store(conn, "sha-2", None)
            cached = db.cache_lookup(conn, "sha-2")

        self.assertIsNotNone(cached)
        self.assertIsNone(db.cached_match_to_dict(cached))

    def test_duplicate_audio_detection_by_sha(self):
        with db.connect(self.db_path) as conn:
            first = db.create_set(conn, "Set 1", "http://x/1", "url", 60)
            db.update_set(conn, first, audio_sha256="abc", status="done")
            second = db.create_set(conn, "Set 2", "http://x/2", "url", 60)
            db.update_set(conn, second, audio_sha256="abc")

            found = db.find_done_set_by_sha(conn, "abc", exclude_id=second)

        self.assertIsNotNone(found)
        self.assertEqual(found["id"], first)

    def test_done_url_lookup_returns_latest(self):
        with db.connect(self.db_path) as conn:
            done = db.create_set(conn, "Set", "http://x/set", "url", 60)
            db.update_set(conn, done, status="done")
            found = db.find_done_set_by_url(conn, "http://x/set")

        self.assertEqual(found["id"], done)

    def test_track_aggregation(self):
        with db.connect(self.db_path) as conn:
            set_id = db.create_set(conn, "Set", "http://x/agg", "url", 60)
            db.insert_segment(conn, set_id, 0, 0, "s0", {"artist": "A", "title": "T", "track_key": "k"})
            db.insert_segment(conn, set_id, 1, 60, "s1", {"artist": "A", "title": "T", "track_key": "k"})
            db.insert_segment(conn, set_id, 2, 120, "s2", None)
            tracks = db.list_tracks(conn)

        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["segment_hits"], 2)
        self.assertEqual(tracks[0]["set_count"], 1)


if __name__ == "__main__":
    unittest.main()
