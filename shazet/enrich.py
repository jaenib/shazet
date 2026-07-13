"""Genre enrichment for playlist tracks that arrive without one.

Tidal and Spotify hand over no genre metadata, which leaves their artists
stranded in the map's "unknown" region. Deezer and iTunes both answer
keyless lookups: Deezer first (its album genres are pleasantly specific),
iTunes as the coarse fallback. Lookups (including misses) are cached by
track_key so a track is only ever asked about once.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

# Deezer allows 50 requests per 5 seconds; stay far below it.
LOOKUP_SPACING = 0.4

_GENERIC_GENRES = {"", "music", "all"}


def lookup_genre(artist: str, title: str) -> str:
    """Best-effort genre for a track; returns '' when nobody knows it."""
    if not artist and not title:
        return ""
    genre = _deezer_genre(artist, title)
    if genre:
        return genre
    return _itunes_genre(artist, title)


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "shazet/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _deezer_genre(artist: str, title: str) -> str:
    query = urllib.parse.quote(
        f'artist:"{artist}" track:"{title}"'.replace('""', '"')
    )
    try:
        found = _get_json(f"https://api.deezer.com/search?q={query}&limit=1")
        hits = found.get("data") or []
        album_id = ((hits[0].get("album") or {}).get("id")) if hits else None
        if not album_id:
            return ""
        album = _get_json(f"https://api.deezer.com/album/{album_id}")
        for genre in (album.get("genres") or {}).get("data") or []:
            name = str(genre.get("name") or "").strip()
            if name.lower() not in _GENERIC_GENRES:
                return name
    except Exception:
        pass
    return ""


def _itunes_genre(artist: str, title: str) -> str:
    term = urllib.parse.quote(f"{artist} {title}".strip())
    try:
        found = _get_json(f"https://itunes.apple.com/search?term={term}&entity=song&limit=1")
        results = found.get("results") or []
        if results:
            name = str(results[0].get("primaryGenreName") or "").strip()
            if name.lower() not in _GENERIC_GENRES:
                return name
    except Exception:
        pass
    return ""
