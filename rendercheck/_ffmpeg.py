"""Measurement via ffprobe/ffmpeg subprocesses.

We shell out instead of depending on pyloudnorm/librosa/soundfile: anyone who
has generated media already has ffmpeg installed, and `loudnorm` is the same
filter used to *fix* a level problem, so measurement and remedy agree by
construction.

Decoding is the expensive part, so loudness and silence are read in a single
pass and memoised against the file's identity — see `measure()`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

_TIMEOUT = 600


class ToolUnavailable(Exception):
    """ffmpeg/ffprobe is missing or produced no measurement.

    Callers turn this into a `skip()` -- it means "could not measure", never
    "measured and it was fine".
    """


class Measurement(NamedTuple):
    """One decode's worth of readings."""

    loudness: float
    """Integrated loudness in LUFS. Digital silence reads as `-inf`."""

    silences: list[tuple[float, float]]
    """`(start, end)` of each silent stretch at or above the requested length."""


def _run(binary: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    if shutil.which(binary) is None:
        raise ToolUnavailable(f"{binary} is not on PATH")
    try:
        return subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolUnavailable(f"{binary} failed: {exc}") from exc


def duration(path: str | Path) -> float:
    """Length of the media at `path`, in seconds."""
    proc = _run(
        "ffprobe",
        [
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise ToolUnavailable(f"ffprobe read no duration from {path}") from None


def _has_stream(path: str | Path, kind: str) -> bool:
    """Whether the file carries at least one stream of `kind` ('a' or 'v').

    Load-bearing, not a convenience. Every detector here reports *findings*, and
    a filter given nothing to analyse reports no findings -- which is
    indistinguishable from "analysed it, all clean". `silencedetect` on a file
    with no audio track says nothing; so does `blackdetect` on a .wav. Without
    this guard the missing thing reads as the healthy thing, which is the exact
    failure mode this library exists to catch.
    """
    proc = _run(
        "ffprobe",
        [
            "-v",
            "error",
            "-select_streams",
            kind,
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
    )
    if proc.returncode != 0:
        # ffprobe could not read the file at all. That is "cannot tell", which
        # must fail open -- reporting "no audio stream" for a file nothing can
        # open would be a confident wrong diagnosis.
        reported = proc.stderr.strip().splitlines()
        raise ToolUnavailable(
            f"ffprobe could not read {path}: "
            f"{reported[-1] if reported else 'ffprobe gave no reason'}"
        )
    return proc.stdout.strip() != ""


def has_audio(path: str | Path) -> bool:
    """Whether the file carries at least one audio stream."""
    return _has_stream(path, "a")


def has_video(path: str | Path) -> bool:
    """Whether the file carries at least one video stream."""
    return _has_stream(path, "v")


def tail_level(path: str | Path, seconds: float = 0.25) -> float:
    """Mean volume of the final `seconds`, in dBFS.

    Speech that was allowed to finish decays into near-silence. Speech that was
    cut mid-word stops at full level, which is what this reads.
    """
    proc = _run(
        "ffmpeg",
        [
            "-nostdin",
            "-sseof",
            f"-{seconds}",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
    )
    found = re.search(r"mean_volume: (-?[\d.]+) dB", proc.stderr)
    if not found:
        raise ToolUnavailable(f"volumedetect read no level from the end of {path}")
    return float(found.group(1))


class Volume(NamedTuple):
    """Whole-file level readings, from one `volumedetect` pass."""

    mean: float
    """Mean volume over the whole file, in dBFS."""

    clipped: int
    """Samples sitting at full scale."""


@lru_cache(maxsize=32)
def _volume(fingerprint: tuple[str, int, int]) -> Volume:
    path = fingerprint[0]
    proc = _run(
        "ffmpeg",
        ["-nostdin", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
    )
    mean = re.search(r"mean_volume: (-?[\d.]+) dB", proc.stderr)
    if not mean:
        raise ToolUnavailable(f"volumedetect measured nothing in {path}")
    clipped = re.search(r"histogram_0db: (\d+)", proc.stderr)
    # No histogram line at all means no samples reached 0 dBFS.
    return Volume(
        mean=float(mean.group(1)), clipped=int(clipped.group(1)) if clipped else 0
    )


def volume(path: str | Path) -> Volume:
    """Mean level and clipped-sample count, memoised like `measure`."""
    return _volume(_fingerprint(Path(path)))


class VideoMeasurement(NamedTuple):
    """One video decode's worth of readings."""

    blacks: list[tuple[float, float]]
    """`(start, duration)` of each all-black stretch."""

    freezes: list[tuple[float, float]]
    """`(start, duration)` of each stretch where the picture stopped moving."""


@lru_cache(maxsize=32)
def _measure_video(
    fingerprint: tuple[str, int, int], min_black: float, min_freeze: float
) -> VideoMeasurement:
    path = fingerprint[0]
    # Chained for the same reason the audio pair is: decoding is the cost.
    proc = _run(
        "ffmpeg",
        [
            "-nostdin",
            "-i",
            path,
            "-vf",
            f"blackdetect=d={min_black}:pic_th=0.98,"
            f"freezedetect=n=-60dB:d={min_freeze}",
            "-f",
            "null",
            "-",
        ],
    )
    blacks = [
        (float(start), float(length))
        for start, length in re.findall(
            r"black_start:(-?[\d.]+) black_end:[\d.]+ black_duration:([\d.]+)",
            proc.stderr,
        )
    ]
    freeze_starts = [
        float(v) for v in re.findall(r"freeze_start: (-?[\d.]+)", proc.stderr)
    ]
    freeze_durations = [
        float(v) for v in re.findall(r"freeze_duration: ([\d.]+)", proc.stderr)
    ]
    return VideoMeasurement(
        blacks=blacks,
        freezes=list(zip(freeze_starts, freeze_durations, strict=False)),
    )


def measure_video(
    path: str | Path, *, min_black: float = 1.0, min_freeze: float = 3.0
) -> VideoMeasurement:
    """Read black and frozen stretches from one decode, memoised like `measure`."""
    return _measure_video(_fingerprint(Path(path)), min_black, min_freeze)


def _fingerprint(path: Path) -> tuple[str, int, int]:
    """Identity of the file *as it is right now*.

    Keying the cache on this rather than the path alone means a re-render
    invalidates it: measuring, fixing, and re-measuring in one process gives
    the new answer, not the old one.
    """
    stat = path.stat()
    return (str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=32)
def _measure(
    fingerprint: tuple[str, int, int], threshold_db: float, min_seconds: float
) -> Measurement:
    path = fingerprint[0]
    # Both filters in one chain: decoding is the cost, and the two readings are
    # almost always wanted together (the CLI asks for both on every file).
    #
    # silencedetect goes FIRST, and the order is load-bearing. loudnorm is a
    # filter, not just a meter: downstream filters see audio it has already
    # normalised. A quiet file gets boosted on the way through, which lifts its
    # near-silent stretches above the threshold and hides them -- measured, on a
    # -40 dBFS file with a five-second gap: one gap found this way, zero the
    # other way. Quiet material is exactly the population most likely to have
    # the defect. silencedetect passes audio through untouched, so loudnorm
    # still reads the same figure from second place.
    proc = _run(
        "ffmpeg",
        [
            "-nostdin",
            "-i",
            path,
            "-af",
            (
                f"silencedetect=noise={threshold_db}dB:d={min_seconds},"
                f"loudnorm=print_format=json"
            ),
            "-f",
            "null",
            "-",
        ],
    )

    # loudnorm prints its JSON to stderr, after ffmpeg's own banner.
    blob = re.search(r'\{[^{}]*"input_i"[^{}]*\}', proc.stderr, re.S)
    if not blob:
        raise ToolUnavailable(f"loudnorm measured nothing in {path}")
    try:
        # float() parses loudnorm's "-inf" for silence directly.
        loudness = float(json.loads(blob.group(0))["input_i"])
    except (ValueError, KeyError, json.JSONDecodeError):
        raise ToolUnavailable(f"loudnorm output was unreadable for {path}") from None

    starts = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(v) for v in re.findall(r"silence_end: (-?[\d.]+)", proc.stderr)]
    if starts and len(ends) < len(starts):
        # A silence running to the end of the file has a start and no end.
        ends = [*ends, duration(path)]
    # strict=False on purpose: ffmpeg's output is the source of truth here, and
    # an unexpected start/end imbalance should degrade to fewer reported gaps,
    # not crash a QA run.
    return Measurement(
        loudness=loudness, silences=list(zip(starts, ends, strict=False))
    )


def measure(
    path: str | Path, *, threshold_db: float = -50.0, min_seconds: float = 3.0
) -> Measurement:
    """Read loudness and silences from one decode.

    Memoised on the file's (path, mtime, size), so two checks over the same
    unchanged file cost one pass. Different silence parameters are a different
    question and get their own decode.
    """
    return _measure(_fingerprint(Path(path)), threshold_db, min_seconds)
