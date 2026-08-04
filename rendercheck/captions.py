"""Do the captions line up with the audio they ship next to?

Existing tools either *fix* drift (ffsubsync, autosubsync) or lint the caption
file on its own (overlapping cues, reading speed, empty rows). Neither answers
the question a build needs answered: does this file, as it stands, match this
audio? That is a gate, and gates are what this library is for.

The method is the one ffsubsync uses, reduced to what a pass/fail needs. Both
sides become a coarse "is anyone talking right now" track -- the captions from
their cue timings, the audio from where it is not silent -- and the two are slid
against each other to find the shift that lines them up best. A shift near zero
means the captions are correct. A large shift means they are late or early by
that much. A shift that *changes* across the file means drift, which is the case
no constant correction fixes.

Everything here is arithmetic over lists of booleans. No numpy, no FFT: a
ten-minute file is six thousand bins, and the whole search is a few hundred
thousand comparisons.
"""

from __future__ import annotations

from statistics import median
from typing import NamedTuple

__all__ = ["Alignment", "Fit", "align", "speech_spans"]

BIN_SECONDS = 0.1
"""Resolution of the comparison.

100 ms is well under the threshold where a viewer notices captions are off, and
coarse enough that the exact edges of a cue -- which no two caption authors agree
on anyway -- do not matter.
"""


class Alignment(NamedTuple):
    """How the captions sit against the audio."""

    offset: float
    """Seconds the captions run late. Negative means early."""

    drift: float | None
    """How much the offset changes from the start of the file to the end.

    A file that needs one shift at the beginning and a different one at the end
    was written against a different clock, not merely mistimed. None when the
    file is too short to fit its two halves independently -- reporting zero
    there would claim a measurement nobody took.
    """

    overlap: float
    """Share of captioned time that lands on speech at the best offset, 0 to 1."""

    distinct: bool
    """Whether one alignment actually beat the others.

    False when every shift scores about the same, which is what continuous
    speech, a music bed, or a file with no silence structure produces. It means
    "cannot tell", never "aligned" -- the caller must skip rather than pass.
    """

    saturated: bool
    """Whether the best fit sat at the edge of the search range.

    `offset` is then a floor, not a measurement: the captions are at least that
    far out and could be much further, or they belong to a different file
    entirely. Still a failure -- just not one to quote a number for.
    """


def _to_bins(spans: list[tuple[float, float]], count: int) -> list[bool]:
    """Mark every bin covered by one of `spans`."""
    bins = [False] * count
    for start, end in spans:
        first = max(0, int(start / BIN_SECONDS))
        last = min(count, int(end / BIN_SECONDS) + 1)
        for index in range(first, last):
            bins[index] = True
    return bins


def speech_spans(
    silences: list[tuple[float, float]], length: float
) -> list[tuple[float, float]]:
    """Invert silence into speech.

    `silencedetect` reports the gaps; everything between them is someone talking.
    """
    spans = []
    cursor = 0.0
    for start, end in sorted(silences):
        if start > cursor:
            spans.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < length:
        spans.append((cursor, length))
    return spans


def _score(
    captions: list[bool], speech: list[bool], shift: int, base: int = 0
) -> float:
    """Share of captioned bins that land on speech when shifted by `shift` bins.

    Shifting positive means "the captions are late" -- to test that, look at the
    audio *earlier* than the cue says. Bins pushed off either end count as
    misses, which is what stops a huge shift from winning by comparing three
    bins against three bins.

    `base` is where `captions` starts within `speech`. It exists so a window can
    be fitted against the *whole* audio track rather than against a matching
    slice of it: slicing both would make every bin near the window edge a
    guaranteed miss, which quietly biases a windowed fit toward smaller shifts.
    """
    hits = total = 0
    for index, captioned in enumerate(captions):
        if not captioned:
            continue
        total += 1
        source = base + index - shift
        if 0 <= source < len(speech) and speech[source]:
            hits += 1
    return hits / total if total else 0.0


class Fit(NamedTuple):
    """The outcome of sliding one window of captions against the audio."""

    shift: int
    """Best-scoring shift, in bins."""

    score: float
    """Its score, 0 to 1."""

    typical: float
    """The median score across the search, which the best has to beat."""

    saturated: bool
    """Whether the best shift sat at the edge of the search range.

    A fit at the edge means the real answer is *at least* this far out and
    possibly much further -- the search simply ran out of room. Reporting the
    edge value as a measurement would state a number nobody established.
    """

    empty: bool
    """Whether the window held no captions at all.

    Every shift then scores 0.0, the tie resolves arbitrarily, and the result is
    noise. Common and harmless in the file: any stretch nobody spoke over.
    """


def _best_shift(
    captions: list[bool], speech: list[bool], reach: int, base: int = 0
) -> Fit:
    """Slide `captions` across `speech` and report where it fits best."""
    if not any(captions):
        return Fit(shift=0, score=0.0, typical=0.0, saturated=False, empty=True)

    scores = [
        (_score(captions, speech, shift, base), shift)
        for shift in range(-reach, reach + 1)
    ]
    # Ties break toward the *smallest* shift, not the largest. `max()` over
    # (score, shift) would pick the biggest shift among equals, which turns
    # every ambiguous fit into a confident claim that the captions are late.
    best, shift = max(scores, key=lambda pair: (pair[0], -abs(pair[1])))
    return Fit(
        shift=shift,
        score=best,
        typical=median(score for score, _ in scores),
        saturated=abs(shift) == reach,
        empty=False,
    )


def align(
    cues: list[tuple[float, float]],
    silences: list[tuple[float, float]],
    length: float,
    *,
    max_shift: float = 5.0,
    min_overlap: float = 0.5,
    min_margin: float = 0.1,
) -> Alignment | None:
    """Fit `cues` against the speech in a file of `length` seconds.

    Returns None when there is nothing to fit -- no cues, or no duration.
    Returns an `Alignment` with `distinct=False` when a fit was attempted and no
    shift stood out; that is a measurement failure, not a clean result.
    """
    if not cues or length <= 0:
        return None

    count = int(length / BIN_SECONDS) + 1
    captioned = _to_bins(cues, count)
    spoken = _to_bins(speech_spans(silences, length), count)
    if not any(captioned) or not any(spoken):
        return None

    reach = int(max_shift / BIN_SECONDS)
    whole = _best_shift(captioned, spoken, reach)

    # Two ways to have learned nothing. A best fit that still misses half the
    # captions means these are not the captions for this audio (or the audio has
    # no usable silence structure). A best fit no better than the typical one
    # means every shift is equally good, which is what wall-to-wall speech looks
    # like -- there is no peak to read a number off.
    distinct = (
        whole.score >= min_overlap and (whole.score - whole.typical) >= min_margin
    )

    # Drift: fit the two ends separately. A file recorded against one clock and
    # captioned against another needs a different shift at the end than at the
    # start, and no single correction fixes it.
    #
    # Both windows are fitted against the whole audio track, not against a
    # matching slice of it -- see `_score`. Three things disqualify the reading,
    # and all three are ordinary rather than exotic:
    #
    #   * a window shorter than the search range, where the fit means little;
    #   * a window holding no cues -- a title sequence, or the silence almost
    #     every file ends on. Every shift scores 0.0 there, so the "best" one is
    #     whichever the tie-break happened to pick;
    #   * a window whose fit sat at the edge of the search range, where the real
    #     shift is somewhere past it and unmeasured.
    #
    # Any of them and drift is None. Reporting the arithmetic anyway is how a
    # perfectly-aligned file with a quiet ending gets told to re-generate its
    # captions.
    third = count // 3
    drift = None
    if distinct and third > reach and not whole.saturated:
        head = _best_shift(captioned[:third], spoken, reach)
        tail = _best_shift(captioned[-third:], spoken, reach, base=count - third)
        if not any((head.empty, tail.empty, head.saturated, tail.saturated)):
            drift = (tail.shift - head.shift) * BIN_SECONDS

    return Alignment(
        offset=whole.shift * BIN_SECONDS,
        drift=drift,
        overlap=whole.score,
        distinct=distinct,
        saturated=whole.saturated,
    )
