"""Turn per-segment detections into tracklist entries and export formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class Entry:
    artist: str
    title: str
    track_key: str
    genre: str
    cover_url: str
    start_seconds: int
    end_seconds: int
    confidence: Optional[int]
    flags: list[str] = field(default_factory=list)
    segment_count: int = 1

    @property
    def is_range(self) -> bool:
        return self.segment_count > 1


def format_timestamp(seconds: int) -> str:
    return f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"


def build_entries(segments: Sequence[dict], segment_length: int) -> list[Entry]:
    entries: list[Entry] = []
    for segment in segments:
        if not segment.get("matched"):
            continue
        offset = int(segment["offset_seconds"])
        key = str(segment.get("track_key") or "")
        last = entries[-1] if entries else None
        if last is not None and last.track_key == key and last.end_seconds == offset:
            last.end_seconds = offset + segment_length
            last.segment_count += 1
            if segment.get("confidence") is not None:
                last.confidence = max(last.confidence or 0, int(segment["confidence"]))
            continue
        entries.append(
            Entry(
                artist=str(segment.get("artist") or ""),
                title=str(segment.get("title") or ""),
                track_key=key,
                genre=str(segment.get("genre") or ""),
                cover_url=str(segment.get("cover_url") or ""),
                start_seconds=offset,
                end_seconds=offset + segment_length,
                confidence=segment.get("confidence"),
                flags=list(segment.get("flags") or []),
            )
        )
    return entries


def entries_to_text(entries: Sequence[Entry]) -> str:
    """setseeker-compatible tracklist text ("[HH:MM:SS] Artist - Title")."""
    lines = ["Final Tracklist:"]
    for entry in entries:
        start = format_timestamp(entry.start_seconds)
        if entry.is_range:
            timestamp = f"{start}-{format_timestamp(entry.end_seconds)}"
        else:
            timestamp = start
        lines.append(f"[{timestamp}] {entry.artist} - {entry.title}")
    return "\n".join(lines) + "\n"


def entries_to_cue(entries: Sequence[Entry], set_title: str) -> str:
    def cue_time(seconds: int) -> str:
        minutes = seconds // 60
        remainder = seconds % 60
        return f"{minutes:02}:{remainder:02}:00"

    lines = [f'TITLE "{set_title}"', 'FILE "set.mp3" MP3']
    for index, entry in enumerate(entries, start=1):
        lines.append(f"  TRACK {index:02} AUDIO")
        lines.append(f'    TITLE "{entry.title}"')
        lines.append(f'    PERFORMER "{entry.artist}"')
        lines.append(f"    INDEX 01 {cue_time(entry.start_seconds)}")
    return "\n".join(lines) + "\n"
