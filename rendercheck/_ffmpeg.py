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

    true_peak: float = float("-inf")
    """Highest true peak in dBTP -- the inter-sample peak, not the sample peak.

    Distinct from `Volume.clipped`, which counts samples already flattened at
    full scale. True peak is what the waveform reaches *between* samples, so a
    file can read under 0 dBFS and still clip once a lossy codec reconstructs
    it. That is the number every platform spec states a ceiling for.
    """

    loudness_range: float | None = None
    """Loudness range in LU, from loudnorm's `input_lra`.

    The spread between the quiet and loud parts of the programme, which is a
    different question from `loudness` (where the middle sits). Two files can
    both measure -16 LUFS while one is flat enough to sound lifeless and the
    other swings so wide its quiet half vanishes under road noise.

    None when the ffmpeg build did not report it -- never a zero, which would
    read as a file with no dynamics at all.
    """


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


class Stream(NamedTuple):
    """What the container claims about one stream, before anything is decoded."""

    kind: str
    """`audio` or `video`."""

    start: float | None
    """Presentation start, in seconds. None when the container does not say."""

    length: float | None
    """Stream duration, in seconds. None when the container does not say."""

    width: int | None
    height: int | None
    fps: float | None
    """Nominal frame rate, from `r_frame_rate`."""

    average_fps: float | None
    """Actual frame rate over the file, from `avg_frame_rate`.

    Diverges from `fps` when the file is variable-rate, which is a common cause
    of audio drifting against picture in an editor that assumes constant rate.
    """

    sample_rate: int | None = None
    """Audio sample rate in Hz. None when the container does not say."""

    channels: int | None = None
    """Audio channel count -- 1 for mono, 2 for stereo."""

    codec: str | None = None
    """Codec name, e.g. `aac`, `pcm_s16le`, `h264`."""

    frames: int | None = None
    """Frame count, where the container bothers to state one."""


def _ratio(value: str | None) -> float | None:
    """ffprobe reports frame rates as `30000/1001`. `0/0` means it has no idea."""
    if not value or "/" not in value:
        return None
    top, _, bottom = value.partition("/")
    try:
        return float(top) / float(bottom)
    except (ValueError, ZeroDivisionError):
        return None


def _number(value: object) -> float | None:
    """A field ffprobe may report as absent, as `N/A`, or as a number.

    Absent is the case that matters: plenty of containers (Matroska especially)
    carry no per-stream duration at all. Returning None keeps that distinct from
    a real reading, so callers can skip rather than compare against a zero they
    invented.
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _whole(value: object) -> int | None:
    """Same contract as `_number`, for a field that is a count rather than a
    measurement. ffprobe reports sample rate as the string `"48000"`."""
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=32)
def _streams(fingerprint: tuple[str, int, int]) -> tuple[Stream, ...]:
    path = fingerprint[0]
    proc = _run(
        "ffprobe",
        [
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,start_time,duration,width,height,"
            "r_frame_rate,avg_frame_rate,sample_rate,channels,nb_frames",
            "-of",
            "json",
            str(path),
        ],
    )
    if proc.returncode != 0:
        reported = proc.stderr.strip().splitlines()
        raise ToolUnavailable(
            f"ffprobe could not read the streams in {path}: "
            f"{reported[-1] if reported else 'ffprobe gave no reason'}"
        )
    try:
        found = json.loads(proc.stdout)["streams"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ToolUnavailable(f"ffprobe listed no streams for {path}") from None

    return tuple(
        Stream(
            kind=str(stream.get("codec_type", "")),
            start=_number(stream.get("start_time")),
            length=_number(stream.get("duration")),
            width=int(stream["width"]) if stream.get("width") else None,
            height=int(stream["height"]) if stream.get("height") else None,
            fps=_ratio(stream.get("r_frame_rate")),
            average_fps=_ratio(stream.get("avg_frame_rate")),
            sample_rate=_whole(stream.get("sample_rate")),
            channels=_whole(stream.get("channels")),
            codec=str(stream["codec_name"]) if stream.get("codec_name") else None,
            frames=_whole(stream.get("nb_frames")),
        )
        for stream in found
    )


def streams(path: str | Path) -> tuple[Stream, ...]:
    """Every stream the container declares, memoised like `measure`.

    One ffprobe call, no decoding at all -- this is the cheapest thing in the
    library, and the checks built on it cost roughly nothing to leave switched on.
    """
    return _streams(_fingerprint(Path(path)))


def tail_level(path: str | Path, seconds: float = 0.25) -> float:
    """Mean volume of the final `seconds`, in dBFS.

    Speech that was allowed to finish decays into near-silence. Speech that was
    cut mid-word stops at full level, which is what this reads.
    """
    proc = _run(
        "ffmpeg",
        [
            "-nostdin",
            "-vn",
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
        # -vn: this reads audio, so decoding the picture is wasted work. On a
        # long video that is most of the runtime of the check.
        ["-nostdin", "-vn", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
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
    # A freeze still running when the file ends has a start and no duration --
    # exactly the shape the silence parser guards against below, and exactly the
    # defect this check exists for. Zipping without this drops it, and a video
    # that stopped producing frames and held one to the end reads as clean.
    if len(freeze_durations) < len(freeze_starts):
        freeze_durations.append(max(duration(path) - freeze_starts[-1], 0.0))
    return VideoMeasurement(
        blacks=blacks,
        freezes=list(zip(freeze_starts, freeze_durations, strict=False)),
    )


def measure_video(
    path: str | Path, *, min_black: float = 1.0, min_freeze: float = 3.0
) -> VideoMeasurement:
    """Read black and frozen stretches from one decode, memoised like `measure`."""
    return _measure_video(_fingerprint(Path(path)), min_black, min_freeze)


class Picture(NamedTuple):
    """How the light in one frame is distributed, from `signalstats`."""

    low: float
    """Luma at the bottom of the distribution (`YLOW`), on ffmpeg's 0-255 scale."""

    high: float
    """Luma at the top of the distribution (`YHIGH`)."""


@lru_cache(maxsize=32)
def _signalstats(fingerprint: tuple[str, int, int]) -> Picture:
    path = fingerprint[0]
    # -frames:v 1 bounds the work *and* the output. signalstats prints a block
    # per frame, so without this a ten-minute video would emit a few hundred
    # thousand lines to be parsed and thrown away.
    proc = _run(
        "ffmpeg",
        [
            "-nostdin",
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            "signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
    )
    # stdout, not stderr. Every other parser in this module reads stderr because
    # that is where ffmpeg's filters log; `metadata=print:file=-` is different --
    # the `-` is a real file argument meaning stdout, and stderr comes back empty.
    found = {
        key: float(value)
        for key, value in re.findall(
            r"lavfi\.signalstats\.Y(LOW|HIGH)=([\d.]+)", proc.stdout
        )
    }
    if "LOW" not in found or "HIGH" not in found:
        raise ToolUnavailable(f"signalstats read no luma distribution from {path}")
    return Picture(low=found["LOW"], high=found["HIGH"])


def signalstats(path: str | Path) -> Picture:
    """Luma distribution of the first frame, memoised like `measure`.

    `YLOW`/`YHIGH` rather than `YMIN`/`YMAX` on purpose: min and max are single
    pixels, so one stray artifact on an otherwise empty canvas gives a full-range
    reading. Measured on a blank white frame carrying a single 4x4 black mark:
    min/max spans 16-235, while low/high correctly reports 235-235.
    """
    return _signalstats(_fingerprint(Path(path)))


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
            "-vn",
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
        readings = json.loads(blob.group(0))
        loudness = float(readings["input_i"])
    except (ValueError, KeyError, json.JSONDecodeError):
        raise ToolUnavailable(f"loudnorm output was unreadable for {path}") from None

    # Parsed separately, and deliberately outside the block above: true peak is
    # an optional extra that older ffmpeg builds may not print, and loudness and
    # dead air both ride on this same decode. Letting an unreadable peak raise
    # would take two working checks down with an optional one. A missing or bad
    # reading is "not measured", never a peak of zero -- which would read as a
    # file sitting right at the ceiling.
    try:
        true_peak = float(readings.get("input_tp", "-inf"))
    except (TypeError, ValueError):
        true_peak = float("-inf")

    # Same treatment, same reason: loudness range is another optional extra, and
    # it must come back as None rather than 0.0 when it is absent. A zero here
    # would read as "no dynamics at all", which is precisely the defect the
    # check built on this looks for -- an unmeasured file would fail it.
    try:
        loudness_range: float | None = float(readings["input_lra"])
    except (TypeError, ValueError, KeyError):
        loudness_range = None

    starts = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(v) for v in re.findall(r"silence_end: (-?[\d.]+)", proc.stderr)]
    if starts and len(ends) < len(starts):
        # A silence running to the end of the file has a start and no end.
        ends = [*ends, duration(path)]
    # strict=False on purpose: ffmpeg's output is the source of truth here, and
    # an unexpected start/end imbalance should degrade to fewer reported gaps,
    # not crash a QA run.
    return Measurement(
        loudness=loudness,
        silences=list(zip(starts, ends, strict=False)),
        true_peak=true_peak,
        loudness_range=loudness_range,
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
