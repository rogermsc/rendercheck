"""Checks on the narration script, and shared script loading.

Text is where the cheapest catches live: a mismatch here costs a regex, while
the same mistake caught after rendering costs a full synthesis and avatar run.
"""

import re
from pathlib import Path

from ._core import SilentFail

# Cue-numbers, blank lines, WEBVTT/NOTE headers, and timestamp rows.
_CUE_LINE = re.compile(r"^\s*(\d+|WEBVTT.*|NOTE.*)?\s*$|-->")
_TAG = re.compile(r"<[^>]+>")

# "I'm Alex", "I am Alex", "My name is Alex" -- how a script states, in words,
# who is supposed to be on screen.
_SELF_INTRO = re.compile(r"\b(?:I'm|I am|My name is)\s+([A-Z][a-z]+)")


def _strip_cues(text: str) -> str:
    kept = [line for line in text.splitlines() if not _CUE_LINE.search(line)]
    return _TAG.sub(" ", "\n".join(kept))


def read_script(script) -> str:
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
        return text
    text = path.read_text(encoding="utf-8", errors="replace")
    return _strip_cues(text) if path.suffix.lower() in (".vtt", ".srt") else text


def assert_speaker(script, expected, known_names) -> None:
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
    roster = {str(name).strip().split()[0].casefold() for name in known_names if str(name).strip()}
    if not roster:
        raise ValueError(
            "known_names is empty: without a roster of real presenters this "
            "check fires on every character in every scenario"
        )
    assigned = str(expected).strip().split()[0].casefold()

    claimed = dict.fromkeys(_SELF_INTRO.findall(read_script(script)))  # ordered, deduped
    wrong = [n for n in claimed if n.casefold() in roster and n.casefold() != assigned]
    if wrong:
        others = f" (also: {', '.join(wrong[1:])})" if len(wrong) > 1 else ""
        raise SilentFail(
            f'the script introduces the presenter as "{wrong[0]}" but '
            f"{expected} is assigned{others} -- the rendered avatar would "
            f"introduce itself with someone else's name. Fix whichever is "
            f"wrong: the assigned presenter, or the name in the script"
        )
