"""Fetch playlist metadata from SoundCloud, Spotify, and Tidal.

Playlists enter the library as track lists with known metadata: no audio is
downloaded, segmented, or shazammed — nothing heavy ever touches the disk.
SoundCloud rides on yt-dlp; Spotify uses the Web API when credentials are
configured (keyless embed-page fallback otherwise); Tidal needs credentials.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from . import config
from .ingest import IngestError

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API = "https://api.spotify.com/v1"
TIDAL_TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
TIDAL_API = "https://openapi.tidal.com/v2"


def platform(url: str) -> Optional[str]:
    """Return 'soundcloud' | 'spotify' | 'tidal' when the URL is a playlist, else None."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or ""
    if host.endswith("soundcloud.com") and "/sets/" in path:
        return "soundcloud"
    if host == "open.spotify.com" and "/playlist/" in path:
        return "spotify"
    if host.endswith("tidal.com") and "/playlist/" in path:
        return "tidal"
    return None


def fetch_playlist(url: str) -> "tuple[str, list[dict]]":
    """Resolve a playlist URL to (title, tracks); tracks are {artist, title, cover_url}."""
    kind = platform(url)
    if kind == "soundcloud":
        return _fetch_soundcloud(url)
    if kind == "spotify":
        return _fetch_spotify(url)
    if kind == "tidal":
        return _fetch_tidal(url)
    raise IngestError("not a supported playlist URL")


def split_artist_title(raw: str, fallback_artist: str = "") -> "tuple[str, str]":
    """SoundCloud titles are usually 'Artist - Title'; fall back to the uploader."""
    for separator in (" - ", " – ", " — "):
        if separator in raw:
            artist, title = raw.split(separator, 1)
            return artist.strip(), title.strip()
    return fallback_artist.strip(), raw.strip()


# Leading noise on pasted tracklist lines: numbering ("07.", "3)") and/or
# timestamps ("[01:23:45]", "12:34", "[00:01:00-00:04:00]").
_LINE_NOISE = re.compile(
    r"^\s*(?:\d+\s*[.)]\s*)?(?:\[?\d{1,2}:\d{2}(?::\d{2})?(?:\s*-\s*\d{1,2}:\d{2}(?::\d{2})?)?\]?\s+)?"
)
_HEADER_LINES = {"final tracklist", "tracklist"}


def parse_pasted_tracklist(text: str) -> "list[dict]":
    """Parse a pasted tracklist: 'Artist - Title' lines or an Exportify CSV.

    Tolerates numbering, timestamps, and tab separation; platform-proof
    fallback for playlists the streaming APIs won't hand over.
    """
    lines = text.splitlines()
    first = next((line for line in lines if line.strip()), "").lower()
    if "," in first and "track name" in first and "artist name" in first:
        return _parse_exportify_csv(text)

    tracks = []
    for line in lines:
        line = line.strip()
        if not line or line.lower().rstrip(":") in _HEADER_LINES:
            continue
        line = _LINE_NOISE.sub("", line).strip()
        if not line:
            continue
        if "\t" in line and " - " not in line:
            artist, title = line.split("\t", 1)
            artist, title = artist.strip(), title.strip()
        else:
            artist, title = split_artist_title(line)
        if artist or title:
            tracks.append({"artist": artist, "title": title, "genre": "", "cover_url": ""})
    return tracks


def _parse_exportify_csv(text: str) -> "list[dict]":
    import csv
    import io

    tracks = []
    for row in csv.DictReader(io.StringIO(text)):
        title = str(row.get("Track Name") or "").strip()
        artist = str(row.get("Artist Name(s)") or row.get("Artist Name") or "").strip()
        if title or artist:
            tracks.append(
                {
                    "artist": artist,
                    "title": title,
                    "genre": str(row.get("Genres") or "").strip(),
                    "cover_url": "",
                }
            )
    return tracks


def _http_json(request: urllib.request.Request) -> dict:
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


# --- SoundCloud (yt-dlp, no credentials) -----------------------------------


def _fetch_soundcloud(url: str) -> "tuple[str, list[dict]]":
    try:
        import yt_dlp
    except ModuleNotFoundError as exc:  # pragma: no cover - deploy-time dependency
        raise IngestError("yt-dlp is not installed") from exc

    # Full (non-flat) extraction: SoundCloud's set API returns id-only stubs
    # in flat mode, so titles/genres only exist after per-track resolution
    # (~1s per track, fine for a background job). Still metadata only.
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if "404" in str(exc):
            raise IngestError(
                "SoundCloud can't find this playlist — check that it exists and is public, then try again"
            ) from exc
        raise
    if not isinstance(info, dict):
        raise IngestError("could not read the playlist")

    title = str(info.get("title") or "").strip()
    uploader = str(info.get("uploader") or "").strip()
    if uploader and title and not title.lower().startswith(uploader.lower()):
        title = f"{uploader} - {title}"

    tracks = []
    for entry in info.get("entries") or []:
        if not isinstance(entry, dict):  # unavailable/private tracks come back as None
            continue
        artist, track_title = split_artist_title(
            str(entry.get("title") or ""), str(entry.get("uploader") or "")
        )
        if not track_title:
            continue
        tracks.append(
            {
                "artist": artist,
                "title": track_title,
                "genre": str(entry.get("genre") or "").strip(),
                "cover_url": str(entry.get("thumbnail") or ""),
            }
        )
    return title, tracks


# --- Spotify -----------------------------------------------------------------


def _spotify_playlist_id(url: str) -> str:
    match = re.search(r"/playlist/([A-Za-z0-9]+)", url)
    if not match:
        raise IngestError("could not find a playlist id in the Spotify URL")
    return match.group(1)


def _fetch_spotify(url: str) -> "tuple[str, list[dict]]":
    playlist_id = _spotify_playlist_id(url)
    client_id, client_secret = config.spotify_credentials()
    if client_id and client_secret:
        try:
            return _fetch_spotify_api(playlist_id, client_id, client_secret)
        except Exception:
            # Dev Mode apps are gated hard since Feb 2026 (Premium-owner
            # requirement, owned-playlists-only items); the keyless embed
            # still answers, so never let API access kill the job.
            pass
    return _fetch_spotify_embed(playlist_id)


def _fetch_spotify_api(playlist_id: str, client_id: str, client_secret: str) -> "tuple[str, list[dict]]":
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    token = _http_json(
        urllib.request.Request(
            SPOTIFY_TOKEN_URL,
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {auth}"},
        )
    ).get("access_token")
    if not token:
        raise IngestError("Spotify rejected the API credentials")
    headers = {"Authorization": f"Bearer {token}"}

    title = str(
        _http_json(
            urllib.request.Request(f"{SPOTIFY_API}/playlists/{playlist_id}?fields=name", headers=headers)
        ).get("name")
        or ""
    ).strip()

    tracks: list[dict] = []
    page_url: Optional[str] = (
        f"{SPOTIFY_API}/playlists/{playlist_id}/tracks"
        "?limit=100&fields=next,items(track(name,artists(name),album(images)))"
    )
    while page_url:
        page = _http_json(urllib.request.Request(page_url, headers=headers))
        tracks.extend(parse_spotify_items(page.get("items") or []))
        page_url = page.get("next")
    return title, tracks


def parse_spotify_items(items: Any) -> "list[dict]":
    tracks = []
    for item in items or []:
        track = item.get("track") if isinstance(item, dict) else None
        if not isinstance(track, dict):
            continue
        name = str(track.get("name") or "").strip()
        artist_names = [
            str(artist.get("name") or "").strip()
            for artist in track.get("artists") or []
            if isinstance(artist, dict)
        ]
        artists = ", ".join(n for n in artist_names if n)
        images = (track.get("album") or {}).get("images") or []
        cover = str(images[-1].get("url") or "") if images and isinstance(images[-1], dict) else ""
        if name or artists:
            tracks.append({"artist": artists, "title": name, "cover_url": cover})
    return tracks


def _fetch_spotify_embed(playlist_id: str) -> "tuple[str, list[dict]]":
    """Keyless fallback: the public embed page ships the tracklist as JSON."""
    request = urllib.request.Request(
        f"https://open.spotify.com/embed/playlist/{playlist_id}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise IngestError(
                "Spotify can't find this playlist — check that it exists and is public, then try again"
            ) from exc
        raise
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    if not match:
        raise IngestError(
            "could not read the Spotify embed page — set SPOTIFY_CLIENT_ID/SECRET to use the API"
        )
    data = json.loads(match.group(1))

    entity = find_key(data, "entity")
    title = ""
    if isinstance(entity, dict):
        title = str(entity.get("name") or entity.get("title") or "").strip()

    tracks = []
    for item in find_key(data, "trackList") or []:
        if not isinstance(item, dict):
            continue
        track_title = str(item.get("title") or "").strip()
        artist = str(item.get("subtitle") or "").strip()
        if track_title or artist:
            tracks.append({"artist": artist, "title": track_title, "cover_url": ""})
    if not tracks:
        raise IngestError(
            "the Spotify embed page had no tracks — is the playlist public? "
            "Private playlists can't be read; make it public and try again"
        )
    return title, tracks


def find_key(value: Any, key: str) -> Any:
    """Depth-first search for the first occurrence of key in nested JSON."""
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = find_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_key(child, key)
            if found is not None:
                return found
    return None


# --- Tidal -------------------------------------------------------------------


def _fetch_tidal(url: str) -> "tuple[str, list[dict]]":
    client_id, client_secret = config.tidal_credentials()
    if not (client_id and client_secret):
        raise IngestError("Tidal playlists need TIDAL_CLIENT_ID / TIDAL_CLIENT_SECRET in the environment")
    match = re.search(r"/playlist/([0-9a-fA-F-]+)", url)
    if not match:
        raise IngestError("could not find a playlist id in the Tidal URL")
    playlist_id = match.group(1)

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    token = _http_json(
        urllib.request.Request(
            TIDAL_TOKEN_URL,
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {auth}"},
        )
    ).get("access_token")
    if not token:
        raise IngestError("Tidal rejected the API credentials")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.api+json"}

    title = ""
    tracks: list[dict] = []
    page_url: Optional[str] = (
        f"{TIDAL_API}/playlists/{playlist_id}"
        f"?countryCode={config.tidal_country()}&include=items,items.artists"
    )
    while page_url:
        try:
            page = _http_json(urllib.request.Request(page_url, headers=headers))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                raise IngestError(
                    "Tidal can't see this playlist — private playlists are invisible to the API. "
                    "Check that it's set to public, then try again"
                ) from exc
            raise
        data = page.get("data")
        if isinstance(data, dict) and not title:
            title = str(((data.get("attributes") or {}).get("name")) or "").strip()
        tracks.extend(parse_tidal_page(page))
        page_url = _tidal_next_link(page)
    if not tracks:
        raise IngestError("the Tidal playlist has no readable tracks")
    return title, tracks


def parse_tidal_page(page: dict) -> "list[dict]":
    """Resolve one JSON:API page (playlist document or relationship page) to tracks."""
    tracks_by_id: dict = {}
    artists_by_id: dict = {}
    for resource in page.get("included") or []:
        if not isinstance(resource, dict):
            continue
        if resource.get("type") == "tracks":
            tracks_by_id[str(resource.get("id"))] = resource
        elif resource.get("type") == "artists":
            artists_by_id[str(resource.get("id"))] = resource

    data = page.get("data")
    if isinstance(data, dict):  # full playlist document: order lives in the relationship
        refs = ((data.get("relationships") or {}).get("items") or {}).get("data") or []
    else:  # relationship page: data is the ordered ref list itself
        refs = data or []

    tracks = []
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("type") != "tracks":
            continue
        resource = tracks_by_id.get(str(ref.get("id")))
        if not isinstance(resource, dict):
            continue
        attributes = resource.get("attributes") or {}
        title = str(attributes.get("title") or "").strip()
        names = []
        for artist_ref in ((resource.get("relationships") or {}).get("artists") or {}).get("data") or []:
            artist = artists_by_id.get(str(artist_ref.get("id"))) if isinstance(artist_ref, dict) else None
            if isinstance(artist, dict):
                name = str((artist.get("attributes") or {}).get("name") or "").strip()
                if name:
                    names.append(name)
        if title or names:
            tracks.append({"artist": ", ".join(names), "title": title, "cover_url": ""})
    return tracks


def _tidal_next_link(page: dict) -> Optional[str]:
    data = page.get("data")
    if isinstance(data, dict):
        links = ((data.get("relationships") or {}).get("items") or {}).get("links") or {}
    else:
        links = page.get("links") or {}
    next_path = links.get("next")
    if not next_path:
        return None
    if "include=" not in next_path:
        next_path += ("&" if "?" in next_path else "?") + "include=items,items.artists"
    return TIDAL_API + next_path if next_path.startswith("/") else next_path
