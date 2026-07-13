"""Shazam recognition with retries plus field extraction from raw results."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from . import config


async def _recognize(shazam, file_path: str):
    # Support both recent and older shazamio APIs.
    if hasattr(shazam, "recognize"):
        return await shazam.recognize(file_path)
    if hasattr(shazam, "recognize_song"):
        return await shazam.recognize_song(file_path)
    raise AttributeError("Unsupported shazamio version: missing recognize methods")


def _is_retryable(error: Exception) -> bool:
    message = str(error)
    retryable = ("URL is invalid", "Cannot connect to host", "Server disconnected", "Timeout", "429")
    return any(snippet in message for snippet in retryable)


async def recognize_file(file_path: str) -> Optional[dict]:
    """Recognize one segment file. Returns extracted match dict or None for no match.

    Raises the last error when Shazam stays unreachable after retries.
    """
    try:  # deploy-time dependency; tests run without it
        from shazamio import Shazam
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("shazamio is not installed") from exc

    last_error: Optional[Exception] = None
    endpoint_countries = ("US", "GB")
    for attempt in range(1, config.RECOGNITION_RETRIES + 1):
        endpoint_country = endpoint_countries[(attempt - 1) % len(endpoint_countries)]
        shazam = Shazam(language="en-US", endpoint_country=endpoint_country)
        try:
            result = await _recognize(shazam, file_path)
            return extract_match(result)
        except Exception as exc:  # network/API errors
            last_error = exc
            if attempt >= config.RECOGNITION_RETRIES or not _is_retryable(exc):
                raise
            await asyncio.sleep(config.RECOGNITION_RETRY_DELAY * attempt)
    raise last_error  # pragma: no cover - loop always returns or raises


def extract_match(result: Any) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    track = result.get("track")
    if not isinstance(track, dict):
        return None

    title = str(track.get("title") or "").strip()
    artist = str(track.get("subtitle") or "").strip()
    if not title and not artist:
        return None

    genres = track.get("genres")
    genre = str(genres.get("primary") or "").strip() if isinstance(genres, dict) else ""

    images = track.get("images")
    cover_url = str(images.get("coverart") or "").strip() if isinstance(images, dict) else ""

    album = ""
    bpm = None
    for section in track.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for meta in section.get("metadata") or []:
            if not isinstance(meta, dict):
                continue
            meta_title = str(meta.get("title") or "").lower()
            if meta_title == "album":
                album = str(meta.get("text") or "").strip()
            elif meta_title in {"bpm", "tempo"}:
                try:
                    bpm = float(str(meta.get("text") or "").strip())
                except ValueError:
                    bpm = None

    key = str(track.get("key") or "").strip()
    if not key:
        key = f"{artist.lower()}|{title.lower()}"

    return {
        "artist": artist,
        "title": title,
        "track_key": key,
        "genre": genre,
        "album": album,
        "cover_url": cover_url,
        "bpm": bpm,
    }
