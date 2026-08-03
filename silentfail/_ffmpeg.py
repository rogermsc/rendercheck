"""Measurement via ffprobe/ffmpeg subprocesses.

We shell out instead of depending on pyloudnorm/librosa/soundfile: anyone who
has generated media already has ffmpeg installed, and shelling out is what the
production pipeline these checks were extracted from already does. One fewer
thing to keep in sync, and `loudnorm` is the same filter used to *fix* the
problem, so measurement and remedy agree by construction.
"""

import json
import re
import shutil
import subprocess

_TIMEOUT = 300


class ToolUnavailable(Exception):
    """ffmpeg/ffprobe is missing or produced no measurement.

    Callers turn this into a `skip()` -- it means "could not measure", never
    "measured and it was fine".
    """


def _run(binary: str, args: list[str]) -> subprocess.CompletedProcess:
    if shutil.which(binary) is None:
        raise ToolUnavailable(f"{binary} is not on PATH")
    try:
        return subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolUnavailable(f"{binary} failed: {exc}") from exc


def duration(path) -> float:
    """Length of the media at `path`, in seconds."""
    proc = _run(
        "ffprobe",
        [
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise ToolUnavailable(f"ffprobe read no duration from {path}") from None


def loudness(path) -> float:
    """Integrated loudness in LUFS. Digital silence comes back as `-inf`."""
    proc = _run(
        "ffmpeg",
        ["-nostdin", "-i", str(path), "-af", "loudnorm=print_format=json",
         "-f", "null", "-"],
    )
    # loudnorm prints its JSON to stderr, after ffmpeg's own banner.
    blob = re.search(r'\{[^{}]*"input_i"[^{}]*\}', proc.stderr, re.S)
    if not blob:
        raise ToolUnavailable(f"loudnorm measured nothing in {path}")
    try:
        # float() parses loudnorm's "-inf" for silence directly.
        return float(json.loads(blob.group(0))["input_i"])
    except (ValueError, KeyError, json.JSONDecodeError):
        raise ToolUnavailable(f"loudnorm output was unreadable for {path}") from None


def silences(path, threshold_db: float, min_seconds: float) -> list[tuple[float, float]]:
    """`(start, end)` of every silent stretch at least `min_seconds` long."""
    proc = _run(
        "ffmpeg",
        ["-nostdin", "-i", str(path), "-af",
         f"silencedetect=noise={threshold_db}dB:d={min_seconds}", "-f", "null", "-"],
    )
    starts = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(v) for v in re.findall(r"silence_end: (-?[\d.]+)", proc.stderr)]
    if not starts:
        return []
    # A silence running to the end of the file has a start and no end.
    if len(ends) < len(starts):
        ends = ends + [duration(path)]
    return list(zip(starts, ends))
