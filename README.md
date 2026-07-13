# shazet

Web tracklist identifier and browser — the "tracklist half" of
[setseeker](https://github.com/jaenib/setseeker) as a standalone service.
Feed it a SoundCloud/YouTube URL or an audio upload; it segments the set,
identifies each minute with Shazam, and builds a browsable tracklist with a
per-track **confidence score**. Lives at `https://jaenib.com/shazet/`.

## What it does

- **Ingest**: any yt-dlp-supported URL (SoundCloud, YouTube, Mixcloud, ...)
  or direct audio upload (mp3/wav/flac/m4a/...). Every submission carries a
  user tag (who added it), shown on set lists and searchable.
- **Playlists**: SoundCloud sets, Spotify and Tidal playlist URLs are
  ingested by metadata alone — no audio is downloaded or shazammed, the
  tracks go straight into the library and the map. SoundCloud needs no
  credentials; Spotify is read keyless via its embed page (~first 50–100
  tracks; the Web API path needs `SPOTIFY_CLIENT_ID/SECRET` *and* a
  Premium app owner since Spotify's Feb 2026 lockdown, and falls back to
  the embed on any failure); Tidal requires `TIDAL_CLIENT_ID/SECRET`.
- **Pasted tracklists**: "or paste a tracklist" on the home form takes
  plain `Artist - Title` lines (numbering/timestamps tolerated) or an
  Exportify CSV — the platform-proof way to ingest long playlists.
- **Segment**: 60-second chunks, ffmpeg stream copy (seconds, not minutes).
- **History first, Shazam second**:
  - the same source URL returns the stored tracklist instantly;
  - identical audio (sha256) is linked to the earlier analysis;
  - every segment's fingerprint hash is cached, so re-runs of overlapping
    audio never re-shazam a segment that was answered before ("force fresh
    run" re-processes but still hits the segment cache).
- **Confidence scoring** (5–99 per tracklist entry, with human-readable
  flags explaining each score):
  - *run support* — one isolated 60s hit is weak; 3+ consecutive hits strong;
  - *sandwich* — a single hit interrupting a continuous run of another track
    (`A A x A A`) is very likely a wrong match and penalized hardest;
  - *scattered repeats* — the same track surfacing as isolated hits in
    several distant places smells like fingerprint confusion;
  - *genre coherence* — detections whose genre disagrees with the set's
    dominant genre profile lose points;
  - *BPM coherence* — when BPM metadata is available, tracks far from the
    set's median BPM lose points (half/double time counts as matching).
- **Browser**: all sets, all recognized tracks across sets (with occurrence
  counts and which sets they appeared in), full-text search, genre chips,
  cover art, SoundCloud/YouTube lookup links.
- **Map**: every artist on the library map carries its sources (sets and
  playlists); the "sources" panel toggles each one on and off.
- **Exports**: plain text (drop-in compatible with setseeker's `tracklists/`
  format) and `.cue`.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn shazet.app:app --reload
# open http://127.0.0.1:8000/shazet/
```

ffmpeg/ffprobe must be on PATH.

Tests (no network, no Shazam):

```bash
python3 -m unittest discover -s tests
```

## Deploy (jaenib.com)

The app runs as systemd unit `shazet` (uvicorn on `127.0.0.1:8321`) and is
proxied by nginx from the `jaenib.com` server block:

- `/srv/apps/shazet` — clone of this repo, `.venv`, and `data/` (sqlite DB,
  transient audio).
- `deploy/shazet.service` → `/etc/systemd/system/shazet.service`; secrets in
  `/srv/apps/shazet/.env` (`SHAZET_TOKEN=...`).
- `deploy/nginx-shazet.conf` → `/etc/nginx/snippets/shazet.conf`, included
  from `/etc/nginx/sites-enabled/jaenib.conf`.

Redeploy after a push:

```bash
ssh root@82.165.45.100 "cd /srv/apps/shazet && git pull --ff-only \
  && .venv/bin/pip install -q -r requirements.txt \
  && systemctl restart shazet"
```

## Configuration (env)

| Variable | Meaning |
|----------|---------|
| `SHAZET_TOKEN` | Access code required to submit jobs (browsing is public). Empty = open submissions. |
| `SHAZET_DATA_DIR` | Data directory (default `./data`). |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Spotify Web API app credentials for full playlist ingestion (optional; keyless embed fallback otherwise). |
| `TIDAL_CLIENT_ID` / `TIDAL_CLIENT_SECRET` | Tidal developer credentials, required for Tidal playlists. |
| `TIDAL_COUNTRY` | Country code for Tidal catalogue lookups (default `DE`). |

Audio is a working file only: downloads, uploads, and segments are always
deleted once a run finishes (or fails), and startup sweeps away anything
left behind by interrupted runs. Only the sqlite DB (tracklists, hashes for
dedupe, fingerprint cache) persists.
