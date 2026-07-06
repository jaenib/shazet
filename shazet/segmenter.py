"""Split set audio into fixed-length mp3 segments with ffmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import config


class SegmentError(Exception):
    pass


def build_split_command(input_file: Path, segment_pattern: str, segment_length: int, reencode: bool) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-y",
        "-i",
        str(input_file),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-f",
        "segment",
        "-segment_time",
        str(segment_length),
        "-segment_format",
        "mp3",
    ]
    if reencode:
        command += ["-ar", "44100", "-ac", "2", "-b:a", "192k"]
    else:
        # mp3 sources are split by stream copy: seconds instead of a re-encode.
        command += ["-c:a", "copy"]
    command.append(segment_pattern)
    return command


def split_audio(input_file: Path, set_id: int, segment_length: int = config.SEGMENT_LENGTH_SECONDS) -> list[Path]:
    config.ensure_dirs()
    out_dir = config.SEGMENT_DIR / str(set_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.mp3"):
        stale.unlink()

    pattern = str(out_dir / "segment_%04d.mp3")
    reencode = input_file.suffix.lower() != ".mp3"
    try:
        subprocess.run(build_split_command(input_file, pattern, segment_length, reencode), check=True)
    except subprocess.CalledProcessError:
        if not reencode:
            for stale in out_dir.glob("*.mp3"):
                stale.unlink()
            subprocess.run(build_split_command(input_file, pattern, segment_length, reencode=True), check=True)
        else:
            raise

    segments = sorted(out_dir.glob("segment_*.mp3"))
    if not segments:
        raise SegmentError("ffmpeg produced no segments")
    return segments


def cleanup_segments(set_id: int):
    out_dir = config.SEGMENT_DIR / str(set_id)
    if not out_dir.exists():
        return
    for path in out_dir.glob("*.mp3"):
        try:
            path.unlink()
        except OSError:
            pass
    try:
        out_dir.rmdir()
    except OSError:
        pass
