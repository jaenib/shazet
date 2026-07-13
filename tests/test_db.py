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

    def test_map_data_aggregates_artists_links_and_genres(self):
        with db.connect(self.db_path) as conn:
            set_a = db.create_set(conn, "Set A", "http://x/a", "url", 60)
            set_b = db.create_set(conn, "Set B", "http://x/b", "url", 60)
            # Alpha appears in both sets; Beta only in set A alongside Alpha.
            db.insert_segment(conn, set_a, 0, 0, "a0", {"artist": "Alpha", "title": "One", "track_key": "k1", "genre": "House"})
            db.insert_segment(conn, set_a, 1, 60, "a1", {"artist": "Alpha", "title": "One", "track_key": "k1", "genre": "House"})
            db.insert_segment(conn, set_a, 2, 120, "a2", {"artist": "Beta", "title": "Two", "track_key": "k2", "genre": "Techno"})
            db.insert_segment(conn, set_b, 0, 0, "b0", {"artist": "Alpha", "title": "Three", "track_key": "k3", "genre": "Trance"})
            db.insert_segment(conn, set_b, 1, 60, "b1", {"artist": "Alpha", "title": "Three", "track_key": "k3", "genre": "House"})

            data = db.map_data(conn)

        self.assertEqual(data["stats"]["sets"], 2)
        self.assertEqual(data["stats"]["artists"], 2)

        by_name = {artist["name"]: artist for artist in data["artists"]}
        self.assertEqual(by_name["Alpha"]["sets"], 2)
        self.assertEqual(by_name["Alpha"]["genre"], "House")  # dominant across sets
        self.assertEqual(by_name["Alpha"]["track_count"], 2)
        self.assertEqual(by_name["Beta"]["sets"], 1)

        self.assertEqual(len(data["links"]), 1)
        artist_a, artist_b, weight = data["links"][0]
        self.assertEqual({artist_a, artist_b}, {"Alpha", "Beta"})
        self.assertEqual(weight, 1)

        genre_names = [genre["name"] for genre in data["genres"]]
        self.assertIn("House", genre_names)

    def test_added_by_is_stored_and_searchable(self):
        with db.connect(self.db_path) as conn:
            set_id = db.create_set(conn, "Set", "http://x/tag", "url", 60, added_by="miko")

            record = db.get_set(conn, set_id)
            by_tag = db.list_sets(conn, query="miko")
            no_match = db.list_sets(conn, query="nobody")

        self.assertEqual(record["added_by"], "miko")
        self.assertEqual([s["id"] for s in by_tag], [set_id])
        self.assertEqual(no_match, [])

    def test_init_db_migrates_sets_table_without_added_by(self):
        legacy_path = Path(self.tmpdir.name) / "legacy.db"
        with db.connect(legacy_path) as conn:
            # The sets table as it existed before the added_by column.
            conn.execute(
                "CREATE TABLE sets (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "title TEXT NOT NULL DEFAULT '', audio_sha256 TEXT NOT NULL DEFAULT '')"
            )
            conn.execute("INSERT INTO sets (title) VALUES ('old set')")
            conn.commit()

        db.init_db(legacy_path)

        with db.connect(legacy_path) as conn:
            row = conn.execute("SELECT added_by FROM sets").fetchone()
        self.assertEqual(row["added_by"], "")

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
