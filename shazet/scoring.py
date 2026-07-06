"""Confidence scoring for per-segment Shazam detections.

Each matched segment gets a 5..99 confidence and a list of human-readable
flags explaining the score. Signals:

- Run support: how many consecutive segments agree on the same track. A
  single isolated hit is weak evidence; three or more in a row is strong.
- Sandwich: a single hit that interrupts an otherwise continuous run of one
  other track (A A X A A) is very likely a wrong match.
- Scattered repeats: the same track surfacing as isolated hits in several
  distant places suggests fingerprint confusion rather than a replay.
- Genre coherence: DJ sets are usually genre-coherent. A detection whose
  genre disagrees with the set's dominant genre profile loses points.
- BPM coherence: when BPM metadata is available, detections far from the
  set's median BPM lose points (half/double-time counts as matching).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence

BASE_SCORE = 50
MIN_SCORE = 5
MAX_SCORE = 99

GENRE_PROFILE_MIN_RUNS = 3
BPM_PROFILE_MIN_VALUES = 3
BPM_MATCH_TOLERANCE = 0.06
BPM_OUTLIER_TOLERANCE = 0.15


@dataclass
class Run:
    track_key: str
    genre: str
    bpm: Optional[float]
    start_idx: int
    end_idx: int
    segment_ids: list[int] = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.end_idx - self.start_idx + 1


def build_runs(segments: Sequence[dict]) -> list[Run]:
    """Group matched segments into runs of consecutive indices with the same track."""
    runs: list[Run] = []
    for segment in segments:
        if not segment.get("matched"):
            continue
        track_key = str(segment.get("track_key") or "")
        idx = int(segment["idx"])
        if runs and runs[-1].track_key == track_key and runs[-1].end_idx == idx - 1:
            runs[-1].end_idx = idx
            runs[-1].segment_ids.append(segment["id"])
            continue
        runs.append(
            Run(
                track_key=track_key,
                genre=str(segment.get("genre") or ""),
                bpm=segment.get("bpm"),
                start_idx=idx,
                end_idx=idx,
                segment_ids=[segment["id"]],
            )
        )
    return runs


def dominant_genres(runs: Sequence[Run]) -> set[str]:
    """Genres that together cover >= 60% of matched run-length, largest first."""
    weights: dict[str, int] = {}
    total = 0
    for run in runs:
        if not run.genre:
            continue
        weights[run.genre] = weights.get(run.genre, 0) + run.length
        total += run.length
    if not weights or total <= 0:
        return set()

    dominant: set[str] = set()
    covered = 0
    for genre, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True):
        dominant.add(genre)
        covered += weight
        if covered / total >= 0.6:
            break
    return dominant


def median_bpm(runs: Sequence[Run]) -> Optional[float]:
    values = [run.bpm for run in runs if run.bpm]
    if len(values) < BPM_PROFILE_MIN_VALUES:
        return None
    return float(statistics.median(values))


def _bpm_ratio_off(bpm: float, reference: float) -> float:
    """Distance from the reference tempo, treating half/double time as equivalent."""
    best = abs(bpm - reference) / reference
    for factor in (0.5, 2.0):
        adjusted = bpm * factor
        best = min(best, abs(adjusted - reference) / reference)
    return best


def score_run(run: Run, run_index: int, runs: Sequence[Run], genres: set[str], set_bpm: Optional[float]) -> tuple[int, list[str]]:
    score = float(BASE_SCORE)
    flags: list[str] = []

    if run.length == 1:
        score -= 18
        flags.append("single-segment hit")
    elif run.length == 2:
        score += 4
        flags.append("2 consecutive hits")
    else:
        score += 12
        flags.append(f"{run.length} consecutive hits")

    previous = runs[run_index - 1] if run_index > 0 else None
    following = runs[run_index + 1] if run_index + 1 < len(runs) else None
    if (
        run.length == 1
        and previous is not None
        and following is not None
        and previous.track_key == following.track_key
        and previous.track_key != run.track_key
        and previous.end_idx == run.start_idx - 1
        and following.start_idx == run.end_idx + 1
    ):
        score -= 25
        flags.append("interrupts a continuous run of another track (likely wrong match)")

    if run.length == 1:
        other_runs = [
            other
            for index, other in enumerate(runs)
            if index != run_index and other.track_key == run.track_key
        ]
        if other_runs:
            score -= 8
            flags.append("same track pops up in scattered places")

    if run.genre and len(runs) >= GENRE_PROFILE_MIN_RUNS and genres:
        if run.genre in genres:
            score += 8
            flags.append(f"genre matches set profile ({run.genre})")
        else:
            score -= 10
            flags.append(f"genre off-profile ({run.genre} vs {'/'.join(sorted(genres))})")

    if run.bpm and set_bpm:
        off = _bpm_ratio_off(float(run.bpm), set_bpm)
        if off <= BPM_MATCH_TOLERANCE:
            score += 6
            flags.append(f"BPM fits set median ({run.bpm:.0f} ~ {set_bpm:.0f})")
        elif off > BPM_OUTLIER_TOLERANCE:
            score -= 12
            flags.append(f"BPM far from set median ({run.bpm:.0f} vs {set_bpm:.0f})")

    bounded = int(round(max(MIN_SCORE, min(MAX_SCORE, score))))
    return bounded, flags


def score_segments(segments: Sequence[dict]) -> dict[int, tuple[int, list[str]]]:
    """Score all matched segments of one set.

    Returns {segment_id: (confidence, flags)}.
    """
    runs = build_runs(segments)
    genres = dominant_genres(runs)
    set_bpm = median_bpm(runs)

    scores: dict[int, tuple[int, list[str]]] = {}
    for run_index, run in enumerate(runs):
        confidence, flags = score_run(run, run_index, runs, genres, set_bpm)
        for segment_id in run.segment_ids:
            scores[segment_id] = (confidence, flags)
    return scores
