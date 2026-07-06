from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL DEFAULT 'url',
  audio_sha256 TEXT NOT NULL DEFAULT '',
  duration_seconds REAL NOT NULL DEFAULT 0,
  segment_length INTEGER NOT NULL DEFAULT 60,
  status TEXT NOT NULL DEFAULT 'queued',
  error TEXT NOT NULL DEFAULT '',
  duplicate_of INTEGER,
  progress_done INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER NOT NULL DEFAULT 0,
  cache_hits INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  set_id INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  offset_seconds INTEGER NOT NULL,
  sha256 TEXT NOT NULL DEFAULT '',
  matched INTEGER NOT NULL DEFAULT 0,
  artist TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  track_key TEXT NOT NULL DEFAULT '',
  genre TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  cover_url TEXT NOT NULL DEFAULT '',
  bpm REAL,
  confidence INTEGER,
  flags TEXT NOT NULL DEFAULT '[]',
  UNIQUE(set_id, idx)
);

CREATE TABLE IF NOT EXISTS shazam_cache (
  sha256 TEXT PRIMARY KEY,
  matched INTEGER NOT NULL,
  artist TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  track_key TEXT NOT NULL DEFAULT '',
  genre TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  cover_url TEXT NOT NULL DEFAULT '',
  bpm REAL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_segments_set ON segments(set_id, idx);
CREATE INDEX IF NOT EXISTS idx_segments_track ON segments(track_key);
CREATE INDEX IF NOT EXISTS idx_sets_sha ON sets(audio_sha256);
"""


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Optional[Path] = None):
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def create_set(conn, title: str, source_url: str, source_kind: str, segment_length: int) -> int:
    cursor = conn.execute(
        "INSERT INTO sets (title, source_url, source_kind, segment_length) VALUES (?, ?, ?, ?)",
        (title, source_url, source_kind, segment_length),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_set(conn, set_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM sets WHERE id = ?", (set_id,)).fetchone()
    return dict(row) if row else None


def update_set(conn, set_id: int, **fields):
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE sets SET {assignments} WHERE id = ?", (*fields.values(), set_id))
    conn.commit()


def list_sets(conn, query: str = "", limit: int = 200) -> list[dict]:
    if query:
        like = f"%{query}%"
        rows = conn.execute(
            """
            SELECT DISTINCT s.* FROM sets s
            LEFT JOIN segments g ON g.set_id = s.id
            WHERE s.title LIKE ? OR s.source_url LIKE ? OR g.artist LIKE ? OR g.title LIKE ?
            ORDER BY s.created_at DESC LIMIT ?
            """,
            (like, like, like, like, limit),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM sets ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


def active_sets(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM sets WHERE status NOT IN ('done', 'failed') ORDER BY created_at ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def find_done_set_by_sha(conn, sha256: str, exclude_id: int = 0) -> Optional[dict]:
    if not sha256:
        return None
    row = conn.execute(
        "SELECT * FROM sets WHERE audio_sha256 = ? AND status = 'done' AND id != ? AND duplicate_of IS NULL "
        "ORDER BY id ASC LIMIT 1",
        (sha256, exclude_id),
    ).fetchone()
    return dict(row) if row else None


def find_done_set_by_url(conn, source_url: str) -> Optional[dict]:
    if not source_url:
        return None
    row = conn.execute(
        "SELECT * FROM sets WHERE source_url = ? AND status = 'done' AND duplicate_of IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (source_url,),
    ).fetchone()
    return dict(row) if row else None


def insert_segment(conn, set_id: int, idx: int, offset_seconds: int, sha256: str, match: Optional[dict]):
    match = match or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO segments
          (set_id, idx, offset_seconds, sha256, matched, artist, title, track_key, genre, album, cover_url, bpm)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            set_id,
            idx,
            offset_seconds,
            sha256,
            1 if match else 0,
            match.get("artist", ""),
            match.get("title", ""),
            match.get("track_key", ""),
            match.get("genre", ""),
            match.get("album", ""),
            match.get("cover_url", ""),
            match.get("bpm"),
        ),
    )
    conn.commit()


def get_segments(conn, set_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM segments WHERE set_id = ? ORDER BY idx ASC", (set_id,)).fetchall()
    segments = []
    for row in rows:
        segment = dict(row)
        try:
            segment["flags"] = json.loads(segment.get("flags") or "[]")
        except json.JSONDecodeError:
            segment["flags"] = []
        segments.append(segment)
    return segments


def update_segment_score(conn, segment_id: int, confidence: int, flags: list[str]):
    conn.execute(
        "UPDATE segments SET confidence = ?, flags = ? WHERE id = ?",
        (confidence, json.dumps(flags), segment_id),
    )


def cache_lookup(conn, sha256: str) -> Optional[dict]:
    if not sha256:
        return None
    row = conn.execute("SELECT * FROM shazam_cache WHERE sha256 = ?", (sha256,)).fetchone()
    return dict(row) if row else None


def cache_store(conn, sha256: str, match: Optional[dict]):
    match = match or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO shazam_cache
          (sha256, matched, artist, title, track_key, genre, album, cover_url, bpm)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sha256,
            1 if match else 0,
            match.get("artist", ""),
            match.get("title", ""),
            match.get("track_key", ""),
            match.get("genre", ""),
            match.get("album", ""),
            match.get("cover_url", ""),
            match.get("bpm"),
        ),
    )
    conn.commit()


def cached_match_to_dict(cached: dict) -> Optional[dict]:
    if not cached or not cached.get("matched"):
        return None
    return {
        "artist": cached.get("artist", ""),
        "title": cached.get("title", ""),
        "track_key": cached.get("track_key", ""),
        "genre": cached.get("genre", ""),
        "album": cached.get("album", ""),
        "cover_url": cached.get("cover_url", ""),
        "bpm": cached.get("bpm"),
    }


def list_tracks(conn, query: str = "", limit: int = 500) -> list[dict]:
    """Aggregate recognized tracks across all sets for the track browser."""
    where = "WHERE matched = 1"
    params: list[Any] = []
    if query:
        where += " AND (artist LIKE ? OR title LIKE ? OR genre LIKE ?)"
        like = f"%{query}%"
        params.extend([like, like, like])
    rows = conn.execute(
        f"""
        SELECT track_key, artist, title, genre,
               MAX(cover_url) AS cover_url,
               COUNT(*) AS segment_hits,
               COUNT(DISTINCT set_id) AS set_count,
               MAX(confidence) AS best_confidence
        FROM segments
        {where}
        GROUP BY track_key, artist, title
        ORDER BY set_count DESC, segment_hits DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def map_data(conn) -> dict:
    """Aggregate the whole library into nodes and links for the map view.

    Artists are the nodes (label size ~ how often we met them); links carry
    co-occurrence weight (how many sets two artists shared). Each artist gets
    its dominant genre so the map can cluster and color by genre.
    """
    artist_rows = conn.execute(
        """
        SELECT artist,
               COUNT(*) AS hits,
               COUNT(DISTINCT set_id) AS set_count,
               COUNT(DISTINCT track_key) AS track_count
        FROM segments
        WHERE matched = 1 AND artist != ''
        GROUP BY artist
        ORDER BY hits DESC
        LIMIT 1500
        """
    ).fetchall()

    genre_rows = conn.execute(
        """
        SELECT artist, genre, COUNT(*) AS weight
        FROM segments
        WHERE matched = 1 AND artist != '' AND genre != ''
        GROUP BY artist, genre
        ORDER BY weight ASC
        """
    ).fetchall()
    dominant_genre: dict[str, str] = {}
    for row in genre_rows:  # ascending weight: the heaviest genre wins last
        dominant_genre[row["artist"]] = row["genre"]

    track_rows = conn.execute(
        """
        SELECT artist, title, COUNT(*) AS hits
        FROM segments
        WHERE matched = 1 AND artist != ''
        GROUP BY artist, title
        ORDER BY hits DESC
        """
    ).fetchall()
    tracks_by_artist: dict[str, list[dict]] = {}
    for row in track_rows:
        tracks_by_artist.setdefault(row["artist"], []).append(
            {"title": row["title"], "hits": row["hits"]}
        )

    pair_rows = conn.execute(
        """
        SELECT a.artist AS artist_a, b.artist AS artist_b, COUNT(DISTINCT a.set_id) AS weight
        FROM (SELECT DISTINCT set_id, artist FROM segments WHERE matched = 1 AND artist != '') a
        JOIN (SELECT DISTINCT set_id, artist FROM segments WHERE matched = 1 AND artist != '') b
          ON a.set_id = b.set_id AND a.artist < b.artist
        GROUP BY a.artist, b.artist
        ORDER BY weight DESC
        LIMIT 6000
        """
    ).fetchall()

    artists = []
    for row in artist_rows:
        name = row["artist"]
        artists.append(
            {
                "name": name,
                "genre": dominant_genre.get(name, ""),
                "hits": row["hits"],
                "sets": row["set_count"],
                "tracks": tracks_by_artist.get(name, [])[:12],
                "track_count": row["track_count"],
            }
        )

    known = {artist["name"] for artist in artists}
    links = [
        [row["artist_a"], row["artist_b"], row["weight"]]
        for row in pair_rows
        if row["artist_a"] in known and row["artist_b"] in known
    ]

    genres: dict[str, int] = {}
    for artist in artists:
        if artist["genre"]:
            genres[artist["genre"]] = genres.get(artist["genre"], 0) + artist["hits"]

    stats_row = conn.execute(
        """
        SELECT COUNT(DISTINCT set_id) AS sets,
               COUNT(DISTINCT track_key) AS tracks,
               COUNT(DISTINCT artist) AS artists
        FROM segments WHERE matched = 1
        """
    ).fetchone()

    return {
        "stats": {
            "sets": stats_row["sets"],
            "tracks": stats_row["tracks"],
            "artists": stats_row["artists"],
            "genres": len(genres),
        },
        "genres": [
            {"name": name, "hits": hits}
            for name, hits in sorted(genres.items(), key=lambda item: item[1], reverse=True)
        ],
        "artists": artists,
        "links": links,
    }


def sets_for_track(conn, track_key: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT s.id, s.title, g.offset_seconds
        FROM segments g JOIN sets s ON s.id = g.set_id
        WHERE g.track_key = ? AND g.matched = 1
        ORDER BY s.id DESC
        """,
        (track_key,),
    ).fetchall()
    return [dict(row) for row in rows]
