"""Background job worker: download -> segment -> recognize (history-first) -> score."""

from __future__ import annotations

import asyncio
import queue
import threading
import traceback

from . import config, db, ingest, recognizer, scoring, segmenter

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
            _jobs.task_done()


def process_set(set_id: int):
    with db.connect() as conn:
        record = db.get_set(conn, set_id)
    if record is None or record["status"] in {"done", "failed"}:
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
            _cleanup(set_id)
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

    _cleanup(set_id)


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
    if not config.keep_audio():
        ingest.cleanup_audio(set_id)


def _now(conn) -> str:
    return conn.execute("SELECT datetime('now')").fetchone()[0]
