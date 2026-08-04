"""Defective media, synthesised on the spot, so the checks can be seen firing.

The problem this solves: every check here only fires on *broken* output, so
evaluating the library used to require already owning a broken file. That is a
strange thing to ask of someone deciding whether to install it.

Nothing is downloaded and nothing is committed as a binary -- ffmpeg generates
each defect from a description, which also means the failures printed by
`rendercheck demo` are real measurements of real files, not sample output
written by hand.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple


class Case(NamedTuple):
    """One defect, the incident behind it, and how to check for it."""

    title: str
    story: str
    path: Path
    args: list[str]


def directory() -> Path:
    """Stable, so a second run reuses the files instead of re-encoding them."""
    return Path(tempfile.gettempdir()) / "rendercheck-demo"


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _sine(
    seconds: float, lufs: float, dest: Path, *, ends_cleanly: bool = True
) -> None:
    """A tone at a known loudness.

    `ends_cleanly` fades the last moment out. Without it the file stops at full
    level, which is itself a defect (see the truncation case) -- so every file
    that is meant to demonstrate something *else* has to end properly, or it
    would fail two checks and muddle the point.
    """
    fade = f",afade=t=out:st={seconds - 0.4:g}:d=0.4" if ends_cleanly else ""
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
        "-af",
        f"loudnorm=I={lufs}:TP=-1.5:LRA=11{fade}",
        str(dest),
    )


def _narration(words: int, dest: Path) -> None:
    """A .vtt, because subtitles sit next to generated media far more often
    than a clean transcript does -- and it shows the cue-stripping working."""
    half = " ".join(["word"] * (words // 2))
    dest.write_text(
        "WEBVTT\n\n"
        f"1\n00:00:00.000 --> 00:00:30.000\n<v Alex>{half}\n\n"
        f"2\n00:00:30.000 --> 00:01:00.000\n{half}\n"
    )


def build() -> list[Case]:
    """Generate every defect once, and describe what each one demonstrates."""
    dest = directory()
    dest.mkdir(exist_ok=True)

    fast, quiet, dropout, silent, truncated, cutoff = (
        dest / n
        for n in (
            "machine-gun.wav",
            "too-quiet.wav",
            "dropout.wav",
            "silent-video.mp4",
            "truncated.wav",
            "cut-off.wav",
        )
    )
    script = dest / "narration.vtt"

    if not fast.exists():
        _sine(60, -16, fast)
    if not script.exists():
        _narration(300, script)  # 300 words over 60s == 300 WPM
    if not quiet.exists():
        _sine(10, -34, quiet)
    if not dropout.exists():
        # Tone, a six-second hole, tone. Right length, right average loudness --
        # the defect exists only in the middle, which is why an average misses it.
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono:d=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-filter_complex",
            "[0][1][2]concat=n=3:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11,"
            "afade=t=out:st=15.6:d=0.4",
            str(dropout),
        )
    if not silent.exists():
        # Video with no audio *track* -- not a silent track, no track. This is
        # what an upscale step that drops audio produces, and it is the defect
        # that reads as success in every pipeline that only checks for errors.
        #
        # A moving test pattern rather than a flat colour: the picture here is
        # meant to be *fine*, and a still dark frame would trip blackdetect and
        # freezedetect as well, which would blur what this case demonstrates.
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-t",
            "8",
            "-pix_fmt",
            "yuv420p",
            str(silent),
        )
    if not truncated.exists():
        _sine(10, -16, truncated)
    if not cutoff.exists():
        # Stops dead at full level -- no decay, no trailing silence. This is
        # what a dropped final sentence leaves behind.
        _sine(6, -16, cutoff, ends_cleanly=False)

    return [
        Case(
            "Narration too fast",
            "A voice picked to match a presenter's face read English at "
            "machine-gun speed. Valid audio, correct timing, perfectly in sync.",
            fast,
            ["--script", script.name],
        ),
        Case(
            "Levels that don't match",
            "Synthesised narration landed 18 dB under the footage it was cut "
            "against. Nobody noticed until viewers rode the volume knob.",
            quiet,
            [],
        ),
        Case(
            "A hole in the middle",
            "Compositing failed transiently and silently. Right length, right "
            "average loudness, six dead seconds in the middle.",
            dropout,
            [],
        ),
        Case(
            "No audio at all",
            "An upscale step dropped the audio track. The API returned success "
            "and the file plays -- in silence.",
            silent,
            [],
        ),
        Case(
            "The last sentence is missing",
            "The most reported defect in generated speech: the audio stops "
            "mid-thought and the API still returns success.",
            cutoff,
            [],
        ),
        Case(
            "Truncated render cached as a success",
            "An encode failure produced a clip a fraction of its intended "
            "length. Retries only re-ran the ones that had errored; this hadn't.",
            truncated,
            ["--expect-seconds", "24"],
        ),
    ]
