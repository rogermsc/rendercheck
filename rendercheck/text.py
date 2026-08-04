"""Checks on the narration script, and shared script loading.

Text is where the cheapest catches live: a mismatch here costs a regex, while
the same mistake caught after rendering costs a full synthesis and avatar run.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from ._core import SilentFail

# Cue-numbers, blank lines, WEBVTT/NOTE headers, and timestamp rows.
_CUE_LINE = re.compile(r"^\s*(\d+|WEBVTT.*|NOTE.*)?\s*$|-->")
_TAG = re.compile(r"<[^>]+>")

# "I'm Alex", "I am Alex", "My name is Alex" -- how a script states, in words,
# who is supposed to be on screen.
_SELF_INTRO = re.compile(r"\b(?:I'm|I am|My name is)\s+([A-Z][a-z]+)")

# Extensions that mean "this string is a filename". Narration text never ends
# in one, so a non-existent path with one of these is a typo worth raising on.
_SCRIPT_SUFFIXES = {".vtt", ".srt", ".txt", ".md", ".json", ".text"}

CAPTION_SUFFIXES = (".vtt", ".srt")
"""Caption formats we can read timings out of, most preferred first."""

# `00:01:02.500 --> 00:01:05.000`, with hours optional and either separator.
# VTT allows cue settings after the end time (`align:start position:50%`), so
# the tail is deliberately unanchored.
_CUE_RANGE = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
    r"\s*-->\s*"
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)


def _strip_cues(text: str) -> str:
    kept = [line for line in text.splitlines() if not _CUE_LINE.search(line)]
    return _TAG.sub(" ", "\n".join(kept))


def read_script(script: str | Path) -> str:
    """Accept raw text, or a path to a transcript, .vtt, or .srt.

    Subtitles sit next to the media far more often than a clean transcript does,
    so accepting a .vtt directly is the difference between this being used and
    every caller writing the same twelve-line parser.
    """
    text = str(script)
    try:
        path = Path(text)
        is_file = len(text) < 4096 and path.is_file()
    except (OSError, ValueError):
        is_file = False  # too long, or contains NULs: it is transcript text
    if not is_file:
        # A one-line string ending in a transcript extension is a path someone
        # typo'd, not narration. Treating it as narration is worse than useless:
        # it reads as one word and reports a confident, wrong verdict about the
        # *audio* ("1 WPM is below 110") for what is really a bad path.
        if "\n" not in text and Path(text).suffix.lower() in _SCRIPT_SUFFIXES:
            raise FileNotFoundError(
                f"no such script file: {text} -- this ends in a transcript "
                f"extension, so it is read as a path, not as narration text"
            )
        return text
    text = path.read_text(encoding="utf-8", errors="replace")
    return _strip_cues(text) if path.suffix.lower() in (".vtt", ".srt") else text


def read_cues(captions: str | Path) -> list[tuple[float, float]]:
    """`(start, end)` in seconds for every cue in a .vtt or .srt.

    `read_script` throws these timings away on purpose -- it wants the words. The
    timings are the other half of the file, and the only way to ask whether the
    captions describe the audio they ship next to.

    Returns them in file order. Malformed rows are skipped rather than raised on:
    a caption file with one bad line still has plenty to align against, and
    refusing to look at any of it would be the less useful answer.
    """
    path = Path(captions)
    if not path.is_file():
        raise FileNotFoundError(f"no such caption file: {captions}")
    text = path.read_text(encoding="utf-8", errors="replace")

    cues = []
    for row in _CUE_RANGE.finditer(text):
        start = _clock(*row.group(1, 2, 3, 4))
        end = _clock(*row.group(5, 6, 7, 8))
        if end > start:
            cues.append((start, end))
    return cues


def _clock(hours: str | None, minutes: str, seconds: str, fraction: str) -> float:
    # `.5` and `.500` both mean half a second; pad rather than divide by a
    # power that depends on how many digits the writer felt like emitting.
    return (
        int(hours or 0) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction.ljust(3, "0")) / 1000
    )


def find_captions(media: str | Path) -> Path | None:
    """A caption file sitting next to `media` under the same stem, if there is one.

    Sidecar captions are named after their media -- `lesson-1.mp4` beside
    `lesson-1.vtt` -- so the common case needs no flag at all.
    """
    stem = Path(media)
    for suffix in CAPTION_SUFFIXES:
        beside = stem.with_suffix(suffix)
        if beside.is_file():
            return beside
    return None


def assert_speaker(
    script: str | Path, expected: str, known_names: Iterable[str]
) -> None:
    """The script's self-introduction against the presenter actually assigned.

    The incident: a script said "I'm Jordan" while the registry assigned Alex.
    An entire module rendered with the wrong face and the wrong gender.
    **Every other gate passed** -- the audio was clean, the timing was right,
    the avatar was valid. Only the identity was wrong, and nothing was looking.

    `known_names` is the roster of real presenters, and it is the whole trick:
    without it, a character in a scenario saying "I'm Rosa, a nurse" trips the
    check on every script that tells a story. Only a name belonging to someone
    who could actually have been cast counts as a claim about the presenter.
    It is required for that reason -- there is no safe default.
    """
    roster = {
        str(name).strip().split()[0].casefold()
        for name in known_names
        if str(name).strip()
    }
    if not roster:
        raise ValueError(
            "known_names is empty: without a roster of real presenters this "
            "check fires on every character in every scenario"
        )
    assigned = str(expected).strip().split()[0].casefold()

    claimed = dict.fromkeys(
        _SELF_INTRO.findall(read_script(script))
    )  # ordered, deduped
    wrong = [n for n in claimed if n.casefold() in roster and n.casefold() != assigned]
    if wrong:
        others = f" (also: {', '.join(wrong[1:])})" if len(wrong) > 1 else ""
        raise SilentFail(
            f'the script introduces the presenter as "{wrong[0]}" but '
            f"{expected} is assigned{others} -- the rendered avatar would "
            f"introduce itself with someone else's name. Fix whichever is "
            f"wrong: the assigned presenter, or the name in the script"
        )
