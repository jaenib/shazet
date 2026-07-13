import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from shazet import config, db, playlists, worker
from shazet.ingest import IngestError


class PlatformDetectionTests(unittest.TestCase):
    def test_playlist_urls_are_detected(self):
        cases = {
            "https://soundcloud.com/someone/sets/festival-warmup": "soundcloud",
            "https://www.soundcloud.com/someone/sets/x?si=abc": "soundcloud",
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=xyz": "spotify",
            "https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M": "spotify",
            "https://tidal.com/browse/playlist/2ec9f8a4-4d6d-4f3a-9c1e-000000000000": "tidal",
            "https://listen.tidal.com/playlist/2ec9f8a4-4d6d-4f3a-9c1e-000000000000": "tidal",
        }
        for url, expected in cases.items():
            self.assertEqual(playlists.platform(url), expected, url)

    def test_non_playlist_urls_are_not_detected(self):
        cases = [
            "https://soundcloud.com/someone/single-track",
            "https://open.spotify.com/track/abc123",
            "https://www.youtube.com/watch?v=abc",
            "https://tidal.com/browse/album/12345",
            "not a url",
        ]
        for url in cases:
            self.assertIsNone(playlists.platform(url), url)


class SplitArtistTitleTests(unittest.TestCase):
    def test_splits_on_common_separators(self):
        self.assertEqual(playlists.split_artist_title("Artist - Title"), ("Artist", "Title"))
        self.assertEqual(playlists.split_artist_title("Artist – Title"), ("Artist", "Title"))
        self.assertEqual(playlists.split_artist_title("A - B - C"), ("A", "B - C"))

    def test_falls_back_to_uploader(self):
        self.assertEqual(playlists.split_artist_title("Just A Title", "Uploader"), ("Uploader", "Just A Title"))


class PastedTracklistTests(unittest.TestCase):
    def test_plain_and_decorated_lines(self):
        text = """
Final Tracklist:
1. Alpha - One
02) Beta - Two
[00:12:34] Gamma - Three
[01:00:00-01:04:00] Delta - Four
12:34 Epsilon - Five
Zeta\tSix
just a title
"""
        tracks = playlists.parse_pasted_tracklist(text)
        self.assertEqual([t["artist"] for t in tracks], ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", ""])
        self.assertEqual(tracks[0]["title"], "One")
        self.assertEqual(tracks[5]["title"], "Six")
        self.assertEqual(tracks[6]["title"], "just a title")

    def test_exportify_csv(self):
        text = (
            '"Track URI","Track Name","Artist Name(s)","Album Name","Genres"\n'
            '"spotify:track:x","One","Alpha, Beta","Album","house, techno"\n'
            '"spotify:track:y","Two","Gamma","Album2",""\n'
        )
        tracks = playlists.parse_pasted_tracklist(text)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0], {"artist": "Alpha, Beta", "title": "One", "genre": "house, techno", "cover_url": ""})

    def test_empty_input_yields_no_tracks(self):
        self.assertEqual(playlists.parse_pasted_tracklist("\n \n"), [])


class SpotifyParsingTests(unittest.TestCase):
    def test_parse_spotify_items(self):
        items = [
            {
                "track": {
                    "name": "One",
                    "artists": [{"name": "Alpha"}, {"name": "Beta"}],
                    "album": {"images": [{"url": "big.jpg"}, {"url": "small.jpg"}]},
                }
            },
            {"track": None},  # removed/local track
            {"track": {"name": "Two", "artists": [{"name": "Gamma"}], "album": {}}},
        ]
        tracks = playlists.parse_spotify_items(items)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0], {"artist": "Alpha, Beta", "title": "One", "cover_url": "small.jpg"})
        self.assertEqual(tracks[1]["artist"], "Gamma")

    def test_find_key_walks_nested_json(self):
        data = {"props": {"pageProps": {"state": {"data": {"entity": {"trackList": [1, 2]}}}}}}
        self.assertEqual(playlists.find_key(data, "trackList"), [1, 2])
        self.assertIsNone(playlists.find_key(data, "missing"))


class TidalParsingTests(unittest.TestCase):
    def test_parse_tidal_playlist_document(self):
        page = {
            "data": {
                "attributes": {"name": "My List"},
                "relationships": {
                    "items": {
                        "data": [
                            {"id": "t2", "type": "tracks"},
                            {"id": "t1", "type": "tracks"},
                        ]
                    }
                },
            },
            "included": [
                {
                    "type": "tracks",
                    "id": "t1",
                    "attributes": {"title": "One"},
                    "relationships": {"artists": {"data": [{"id": "a1", "type": "artists"}]}},
                },
                {
                    "type": "tracks",
                    "id": "t2",
                    "attributes": {"title": "Two"},
                    "relationships": {"artists": {"data": [{"id": "a2", "type": "artists"}]}},
                },
                {"type": "artists", "id": "a1", "attributes": {"name": "Alpha"}},
                {"type": "artists", "id": "a2", "attributes": {"name": "Beta"}},
            ],
        }
        tracks = playlists.parse_tidal_page(page)
        # order follows the relationship list, not the included array
        self.assertEqual(tracks, [
            {"artist": "Beta", "title": "Two", "cover_url": ""},
            {"artist": "Alpha", "title": "One", "cover_url": ""},
        ])

    def test_parse_tidal_relationship_page(self):
        page = {
            "data": [{"id": "t1", "type": "tracks"}],
            "included": [
                {"type": "tracks", "id": "t1", "attributes": {"title": "Solo"}, "relationships": {}},
            ],
            "links": {"next": "/playlists/x/relationships/items?page[cursor]=abc"},
        }
        tracks = playlists.parse_tidal_page(page)
        self.assertEqual(tracks, [{"artist": "", "title": "Solo", "cover_url": ""}])
        next_link = playlists._tidal_next_link(page)
        self.assertTrue(next_link.startswith(playlists.TIDAL_API))
        self.assertIn("include=", next_link)


class TidalErrorTests(unittest.TestCase):
    def test_private_playlist_404_becomes_check_public_hint(self):
        responses = [
            {"access_token": "t"},  # token request succeeds
            urllib.error.HTTPError("url", 404, "Not Found", None, None),  # playlist is private
        ]

        def fake_http_json(request):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(playlists.config, "tidal_credentials", return_value=("id", "secret")):
            with mock.patch.object(playlists, "_http_json", side_effect=fake_http_json):
                with self.assertRaises(IngestError) as ctx:
                    playlists.fetch_playlist("https://tidal.com/playlist/d4cbfc4f-0000-0000-0000-000000000000")

        self.assertIn("public", str(ctx.exception))


class PlaylistWorkerTests(unittest.TestCase):
    """The playlist pipeline stores tracks straight to the DB — no audio, no Shazam."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        db.init_db(self.db_path)
        self.db_patch = mock.patch.object(config, "DB_PATH", self.db_path)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.tmpdir.cleanup()

    def test_process_playlist_stores_tracks_without_audio(self):
        with db.connect(self.db_path) as conn:
            set_id = db.create_set(
                conn, "", "https://open.spotify.com/playlist/abc", "playlist", 60, added_by="miko"
            )

        fake = ("Warmup Mix", [
            {"artist": "Alpha", "title": "One", "genre": "Trance", "cover_url": "c1.jpg"},
            {"artist": "Beta", "title": "Two", "cover_url": ""},
        ])
        with mock.patch.object(playlists, "fetch_playlist", return_value=fake):
            worker.process_set(set_id)

        with db.connect(self.db_path) as conn:
            record = db.get_set(conn, set_id)
            segments = db.get_segments(conn, set_id)

        self.assertEqual(record["status"], "done")
        self.assertEqual(record["title"], "Warmup Mix")
        self.assertEqual(record["progress_done"], 2)
        self.assertEqual(len(segments), 2)
        self.assertTrue(all(segment["matched"] for segment in segments))
        self.assertEqual(segments[0]["artist"], "Alpha")
        self.assertEqual(segments[0]["track_key"], "alpha|one")
        self.assertEqual(segments[0]["genre"], "Trance")
        self.assertEqual(segments[1]["cover_url"], "")

    def test_empty_playlist_fails_the_set(self):
        with db.connect(self.db_path) as conn:
            set_id = db.create_set(
                conn, "", "https://open.spotify.com/playlist/empty", "playlist", 60, added_by="miko"
            )

        with mock.patch.object(playlists, "fetch_playlist", return_value=("Empty", [])):
            with self.assertRaises(Exception):
                worker.process_set(set_id)


if __name__ == "__main__":
    unittest.main()
