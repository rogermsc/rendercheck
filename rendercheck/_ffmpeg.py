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


def has_audio(path: str | Path) -> bool:
    """Whether the file carries at least one audio stream.

    Load-bearing, not a convenience. `silencedetect` on a file with no audio
    track reports nothing at all, which is indistinguishable from "no silence
    found" -- so without this guard a video with the audio missing entirely
    reads as clean. That is the exact failure mode this library exists to
    catch, so every audio check asks this question first.
    """
    proc = _run(
        "ffprobe",
        [
            "-v",
            "error",
            "-select_streams",
            "a",
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
        raise ToolUnavailable(
            f"ffprobe could not read {path}: {proc.stderr.strip().splitlines()[-1:]}"
        )
    return "audio" in proc.stdout


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
    proc = _run(
        "ffmpeg",
        [
            "-nostdin",
            "-i",
            path,
            "-af",
            (
                f"loudnorm=print_format=json,"
                f"silencedetect=noise={threshold_db}dB:d={min_seconds}"
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
