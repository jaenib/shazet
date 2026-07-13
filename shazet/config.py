from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHAZET_DATA_DIR", str(REPO_ROOT / "data")))
AUDIO_DIR = DATA_DIR / "audio"
SEGMENT_DIR = DATA_DIR / "segments"
DB_PATH = DATA_DIR / "shazet.db"

# The app is mounted behind nginx at this path prefix; all routes include it.
BASE_PATH = "/shazet"

SEGMENT_LENGTH_SECONDS = 60
RECOGNITION_RETRIES = 4
RECOGNITION_RETRY_DELAY = 1.5
RECOGNITION_REQUEST_SPACING = 0.35

# Confidence thresholds used by the UI to color badges.
CONFIDENCE_HIGH = 70
CONFIDENCE_LOW = 40


def submit_token() -> str:
    return os.environ.get("SHAZET_TOKEN", "").strip()


def spotify_credentials() -> "tuple[str, str]":
    return (
        os.environ.get("SPOTIFY_CLIENT_ID", "").strip(),
        os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip(),
    )


def tidal_credentials() -> "tuple[str, str]":
    return (
        os.environ.get("TIDAL_CLIENT_ID", "").strip(),
        os.environ.get("TIDAL_CLIENT_SECRET", "").strip(),
    )


def tidal_country() -> str:
    return os.environ.get("TIDAL_COUNTRY", "DE").strip() or "DE"


def ensure_dirs():
    for path in (DATA_DIR, AUDIO_DIR, SEGMENT_DIR):
        path.mkdir(parents=True, exist_ok=True)
