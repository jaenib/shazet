"""Background job worker: download -> segment -> recognize (history-first) -> score."""

from __future__ import annotations

import asyncio
import queue
import shutil
import threading
import time
import traceback

from . import config, db, enrich, ingest, playlists, recognizer, scoring, segmenter

_jobs: "queue.Queue[int]" = queue.Queue()
_worker_started = threading.Lock()
_worker_thread = None


def enqueue(set_id: int):
    _jobs.put(set_id)


def start_worker():
    global _worker_thread
    with _worker_started:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker_loop, name="shazet-worker", daemon=True)
            _worker_thread.start()


def requeue_unfinished():
    """Re-enqueue jobs that were interrupted by a restart."""
    with db.connect() as conn:
        for row in db.active_sets(conn):
            enqueue(row["id"])


def _worker_loop():
    while True:
        set_id = _jobs.get()
        try:
            process_set(set_id)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
            try:
                with db.connect() as conn:
                    db.update_set(conn, set_id, status="failed", error=detail)
            except Exception:
                pass
        finally:
            # Audio and segments are working files only: never keep them,
            # whether the run succeeded, deduplicated, or failed.
            try:
                _cleanup(set_id)
            except Exception:
                pass
            _jobs.task_done()


def process_set(set_id: int):
    with db.connect() as conn:
        record = db.get_set(conn, set_id)
    if record is None or record["status"] in {"done", "failed"}:
        return

    if record["source_kind"] == "playlist":
        _process_playlist(set_id, record)
        return

    audio_path = _fetch_audio(set_id, record)

    sha256 = ingest.sha256_of_file(audio_path)
    duration = ingest.probe_duration_seconds(audio_path)
    with db.connect() as conn:
        db.update_set(conn, set_id, audio_sha256=sha256, duration_seconds=duration)
        # History first: identical audio was already analyzed -> reuse, no Shazam.
        existing = db.find_done_set_by_sha(conn, sha256, exclude_id=set_id)
        if existing is not None:
            db.update_set(
                conn,
                set_id,
                status="done",
                duplicate_of=existing["id"],
                completed_at=_now(conn),
            )
            return

    with db.connect() as conn:
        db.update_set(conn, set_id, status="segmenting")
    segment_files = segmenter.split_audio(audio_path, set_id, int(record["segment_length"]))

    with db.connect() as conn:
        db.update_set(conn, set_id, status="recognizing", progress_total=len(segment_files), progress_done=0)

    cache_hits = asyncio.run(_recognize_all(set_id, segment_files, int(record["segment_length"])))

    with db.connect() as conn:
        db.update_set(conn, set_id, status="scoring", cache_hits=cache_hits)
        segments = db.get_segments(conn, set_id)
        for segment_id, (confidence, flags) in scoring.score_segments(segments).items():
            db.update_segment_score(conn, segment_id, confidence, flags)
        conn.commit()
        db.update_set(conn, set_id, status="done", completed_at=_now(conn))


def _process_playlist(set_id: int, record: dict):
    """Playlists carry their own metadata: store the tracks directly, no audio."""
    with db.connect() as conn:
        db.update_set(conn, set_id, status="fetching")

    title, tracks = playlists.fetch_playlist(record["source_url"])
    if not tracks:
        raise ingest.IngestError("the playlist has no readable tracks")

    with db.connect() as conn:
        if title and not record["title"]:
            db.update_set(conn, set_id, title=title)
        db.update_set(conn, set_id, status="enriching", progress_total=len(tracks))

    enrich_tracks(set_id, tracks)

    with db.connect() as conn:
        store_playlist_tracks(conn, set_id, tracks)
        db.update_set(
            conn, set_id, progress_done=len(tracks), status="done", completed_at=_now(conn)
        )


def enrich_tracks(set_id: int, tracks):
    """Fill in missing genres (Tidal/Spotify ship none) via keyless lookups.

    Cached by track_key so each track is only ever asked about once.
    """
    for index, track in enumerate(tracks):
        if not track.get("genre"):
            artist = str(track.get("artist") or "")
            title = str(track.get("title") or "")
            key = f"{artist.lower()}|{title.lower()}"
            with db.connect() as conn:
                cached = db.genre_cache_lookup(conn, key)
            if cached is not None:
                track["genre"] = cached
            else:
                try:
                    track["genre"] = enrich.lookup_genre(artist, title)
                except Exception:
                    track["genre"] = ""
                with db.connect() as conn:
                    db.genre_cache_store(conn, key, track["genre"])
                time.sleep(enrich.LOOKUP_SPACING)
        with db.connect() as conn:
            db.update_set(conn, set_id, progress_done=index + 1)


_backfill_lock = threading.Lock()


def backfill_genres():
    """Fill missing genres on already-stored tracks (cache-first, idempotent).

    Covers pasted tracklists, playlists ingested before enrichment existed,
    and Shazam matches that came back genre-less. Safe to run repeatedly:
    every miss is cached, so settled tracks cost nothing.
    """
    if not _backfill_lock.acquire(blocking=False):
        return  # a sweep is already running
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT track_key, artist, title FROM segments "
                "WHERE matched = 1 AND genre = '' AND track_key != ''"
            ).fetchall()
        for row in rows:
            key = row["track_key"]
            with db.connect() as conn:
                genre = db.genre_cache_lookup(conn, key)
            if genre is None:
                try:
                    genre = enrich.lookup_genre(row["artist"], row["title"])
                except Exception:
                    genre = ""
                with db.connect() as conn:
                    db.genre_cache_store(conn, key, genre)
                time.sleep(enrich.LOOKUP_SPACING)
            if genre:
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE segments SET genre = ? WHERE track_key = ? AND genre = ''",
                        (genre, key),
                    )
                    conn.commit()
    finally:
        _backfill_lock.release()


def start_genre_backfill():
    threading.Thread(target=backfill_genres, name="shazet-genre-backfill", daemon=True).start()


def store_playlist_tracks(conn, set_id: int, tracks):
    """Insert playlist tracks as matched segments (shared with pasted tracklists)."""
    for index, track in enumerate(tracks):
        artist = str(track.get("artist") or "")
        track_title = str(track.get("title") or "")
        match = {
            "artist": artist,
            "title": track_title,
            "track_key": f"{artist.lower()}|{track_title.lower()}",
            "genre": str(track.get("genre") or ""),
            "album": "",
            "cover_url": str(track.get("cover_url") or ""),
            "bpm": None,
        }
        db.insert_segment(conn, set_id, index, 0, "", match)


def _fetch_audio(set_id: int, record: dict):
    if record["source_kind"] == "upload":
        audio_path = ingest.find_audio(set_id)
        if audio_path is None:
            raise ingest.IngestError("uploaded audio file is missing")
        return audio_path

    with db.connect() as conn:
        db.update_set(conn, set_id, status="downloading")
    audio_path, title = ingest.download_url(record["source_url"], set_id)
    if title and not record["title"]:
        with db.connect() as conn:
            db.update_set(conn, set_id, title=title)
    return audio_path


async def _recognize_all(set_id: int, segment_files, segment_length: int) -> int:
    cache_hits = 0
    for index, segment_file in enumerate(segment_files):
        sha256 = ingest.sha256_of_file(segment_file)

        with db.connect() as conn:
            cached = db.cache_lookup(conn, sha256)

        if cached is not None:
            match = db.cached_match_to_dict(cached)
            cache_hits += 1
        else:
            try:
                match = await recognizer.recognize_file(str(segment_file))
            except Exception:
                match = None  # persistent API failure for this segment: record as unmatched
            with db.connect() as conn:
                db.cache_store(conn, sha256, match)
            await asyncio.sleep(config.RECOGNITION_REQUEST_SPACING)

        with db.connect() as conn:
            db.insert_segment(conn, set_id, index, index * segment_length, sha256, match)
            db.update_set(conn, set_id, progress_done=index + 1)
    return cache_hits


def _cleanup(set_id: int):
    segmenter.cleanup_segments(set_id)
    ingest.cleanup_audio(set_id)


def cleanup_orphans():
    """Delete working files that no in-flight set owns.

    Catches everything the per-job cleanup can miss: files from runs
    interrupted by a crash or restart, sets deleted from the DB, and
    yt-dlp .part remnants. Runs at startup before jobs are requeued.
    """
    config.ensure_dirs()
    with db.connect() as conn:
        active = {row["id"] for row in db.active_sets(conn)}

    for path in config.AUDIO_DIR.iterdir():
        if _owner_set_id(path.name) in active:
            continue
        try:
            path.unlink()
        except OSError:
            pass

    for path in config.SEGMENT_DIR.iterdir():
        if _owner_set_id(path.name) in active:
            continue
        shutil.rmtree(path, ignore_errors=True)


def _owner_set_id(name: str):
    try:
        return int(name.split(".")[0])
    except ValueError:
        return None


def _now(conn) -> str:
    return conn.execute("SELECT datetime('now')").fetchone()[0]
