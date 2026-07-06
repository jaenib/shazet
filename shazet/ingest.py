"""Fetch source audio for a set: URL download via yt-dlp or an uploaded file."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from . import config


class IngestError(Exception):
    pass


def is_supported_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def sha256_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def probe_duration_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def download_url(source_url: str, set_id: int) -> tuple[Path, str]:
    """Download a URL as mp3 into the audio dir. Returns (path, title)."""
    try:
        import yt_dlp
    except ModuleNotFoundError as exc:  # pragma: no cover - deploy-time dependency
        raise IngestError("yt-dlp is not installed") from exc

    config.ensure_dirs()
    output_template = str(config.AUDIO_DIR / f"{set_id}.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(source_url, download=True)

    title = ""
    if isinstance(info, dict):
        uploader = str(info.get("uploader") or "").strip()
        raw_title = str(info.get("title") or "").strip()
        title = f"{uploader} - {raw_title}".strip(" -") if uploader or raw_title else ""

    audio_path = config.AUDIO_DIR / f"{set_id}.mp3"
    if not audio_path.is_file():
        candidates = sorted(config.AUDIO_DIR.glob(f"{set_id}.*"))
        if not candidates:
            raise IngestError("download finished but no audio file was produced")
        audio_path = candidates[0]
    return audio_path, title


def store_upload(payload: bytes, original_name: str, set_id: int) -> tuple[Path, str]:
    config.ensure_dirs()
    suffix = Path(original_name).suffix.lower() or ".mp3"
    if suffix not in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aif", ".aiff"}:
        raise IngestError(f"unsupported upload format: {suffix}")
    audio_path = config.AUDIO_DIR / f"{set_id}{suffix}"
    audio_path.write_bytes(payload)
    title = Path(original_name).stem
    return audio_path, title


def cleanup_audio(set_id: int):
    for path in config.AUDIO_DIR.glob(f"{set_id}.*"):
        try:
            path.unlink()
        except OSError:
            pass


def find_audio(set_id: int) -> Optional[Path]:
    candidates = sorted(config.AUDIO_DIR.glob(f"{set_id}.*"))
    return candidates[0] if candidates else None
