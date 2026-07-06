# shazet

Web tracklist identifier and browser — the "tracklist half" of
[setseeker](https://github.com/jaenib/setseeker) as a standalone service.
Feed it a SoundCloud/YouTube URL or an audio upload; it segments the set,
identifies each minute with Shazam, and builds a browsable tracklist with a
per-track **confidence score**. Lives at `https://jaenib.com/shazet/`.

## What it does

- **Ingest**: any yt-dlp-supported URL (SoundCloud, YouTube, Mixcloud, ...)
  or direct audio upload (mp3/wav/flac/m4a/...).
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
| `SHAZET_KEEP_AUDIO` | `1` keeps downloaded audio after analysis (default: deleted, hash kept for dedupe). |
