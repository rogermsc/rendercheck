"""Does the mouth move when the voice does?

A talking head whose picture and speech have come apart is the defect every
avatar pipeline ships and no file-level check can see: the container is correct,
both streams are valid, the durations agree, and the render is unwatchable.
`assert_streams_aligned` reads the *container's* timing and is right to; this
reads the *content's*.

The method is a correlation, not a model. Picture motion becomes one number per
frame -- how much changed since the frame before -- and the audio becomes its
amplitude envelope over the same grid. A talking head drives both: the mouth
moves while sound comes out, and stops while it does not. Sliding one against
the other, the shift that lines them up is the offset.

**Why this is not `captions.align`.** That function slides two *boolean* tracks
and scores their overlap, which is right for cue timings and wrong here:
narration occupies about 79% of a spoken-word file, so every shift scores ~0.8
and the best beats the median by 0.10 -- no peak to read a number off. Measured
on fourteen real clips before this module existed. Correlating the *envelopes*
keeps the magnitude structure and produces a peak. Everything else about the two
is deliberately the same shape -- the tie-break, the thirds, the saturation
guard -- so they read as a matched pair.

**What this cannot do.** It cannot certify sync. EBU R37 and ITU-R BT.1359 put
detectability at +40 ms of audio lead and -60 ms of lag; this method's spread
across the clips it can measure is 76 ms, and its threshold is 300 ms. It finds
a broken pipeline, it does not bless a good one. It also declines to answer on
most files -- see `MIN_RATIO`.
"""

from __future__ import annotations

import statistics
from typing import NamedTuple

__all__ = ["Fit", "Sync", "align", "duty_cycle", "high_pass", "motion_from_cells"]

ANALYSIS_FPS = 25.0
"""Frames per second the comparison runs at, whatever the source rate.

Normalising here rather than reading the file's rate is load-bearing: with a
variable-rate source, frame index maps to time non-linearly, and a non-linear
clock *manufactures drift* -- which is the headline output of this check.
"""

BIN_SECONDS = 1.0 / ANALYSIS_FPS

SMOOTH_SECONDS = 0.5
"""Half-width of the moving average removed from both curves before correlating.

Both tracks carry a slow common trend -- a clip is busier and louder in its
middle than at its edges -- and left in, that trend correlates with itself at
every shift and flattens the peak. Removing it leaves the syllable-rate
structure, which is the part that actually carries the timing.

Measured: at 0.5 s the offset spread across clips is 0.071 s; at 0.25 s the
estimate moves by 0.11 s and at 0.15 s it falls apart (spread 0.447 s).
"""

MAX_SHIFT = 3.0
"""How far, in seconds, to slide one track against the other.

Wider than any offset worth reporting, and deliberately so. At +/-1.5 s an
offset *past* the range did not clamp -- the search simply found the highest
noise peak inside it and reported a confident small number. Recall fell from 98%
at 0.5 s to 88% at 2.0 s because of it. Widening to 3.0 s made recall monotonic
and 100% at every offset from 0.75 s up.
"""

LOBE_SECONDS = 0.6
"""How far from the peak a score has to be before it counts as a rival.

Roughly the width of the peak itself, which is set by the smoothing above.
Scores within this are the same peak seen from one side.
"""

MIN_RATIO = 2.10
"""How far the best alignment must stand above the best rival to be believed.

**The number that decides whether this check speaks at all**, and the one that
took the most measurement. Peak correlation on its own establishes nothing: a
clip correlated against *a different clip's* audio scores r = 0.27 with a median
of 0.00 across the search -- the top of the range real clips produce. Absolute r
is not evidence of alignment. The ratio of the peak to the best rival elsewhere
on the curve is.

Set from 80 real avatar renders against 80 cross-paired nulls (each clip's
picture against another clip's sound): this is the lowest threshold at which no
null engages. It admits about 30% of real talking-head clips.

**Abstention is the design, not a shortfall.** The other 70% get a skip, because
on those the peak does not stand clear enough to read a number off, and a number
nobody measured is worse than no number.
"""

BIAS_SECONDS = 0.36
"""What this method reports for a file that is actually in sync.

Frame-differencing peaks where the picture *changes* -- the mouth opening --
while amplitude peaks a beat later, in the middle of the vowel. A correctly
synced file therefore reads as sound running about a third of a second late, and
the constant is removed so that zero means zero.

Measured across 80 HeyGen avatar renders, on the 24 the ratio gate admitted:
mean 0.360 s, median 0.400 s, stdev 0.076 s, range 0.200-0.440, positive every
time. Every one of those clips came from one provider at 1080p25 through one
pipeline, so this is a *calibration*, not a constant of nature: pass `bias=` for
material it was not measured against, or `bias=0.0` to see the raw reading.
"""

MIN_DUTY = 0.70
"""Share of frames that must be moving for this to be a talking head at all.

A presenter's face moves continuously; a slide deck holds still and cuts. The
separation is not subtle -- across 150 renders and 150 composed lesson videos
the medians are 0.871 and 0.007 -- but the ranges do overlap at the top, because
composed video often has an avatar composited into it, which is a talking head
and should be measured.

At this threshold every one of the 150 talking-head clips is admitted and 4% of
the others are. It is an applicability test, not a defect test: a file below it
gets a skip that says why.
"""

MIN_SECONDS = 20.0
"""Below this there is not enough signal to correlate.

Noise on a correlation falls as 1/sqrt(N). At 20 s the noise on r is about 0.22,
which is larger than the peak most real clips produce, so a short clip's "best"
shift is whichever way the noise fell. Measured directly: the same clip cut to
5 s reported a confident +1.00 s against its own true -0.32 s.
"""


def duty_cycle(motion: list[float]) -> float:
    """Share of frames whose motion clears a third of the clip's own mean.

    Relative to the clip, so it does not care about resolution, codec or how
    brightly lit the subject is -- only whether the picture is in more or less
    continuous movement.
    """
    if not motion:
        return 0.0
    mean = sum(motion) / len(motion)
    if mean <= 0:
        return 0.0
    return sum(1 for value in motion if value > 0.3 * mean) / len(motion)


def motion_from_cells(
    totals: list[float], squares: list[float], count: int
) -> list[int]:
    """Which cells to build the motion track from, by their variance alone.

    The busiest tenth of the frame is where the mouth is on a framed talking
    head, and picking it lifts the share of clips this check can speak about
    from 22% to 30%.

    **Selected on variance, never on agreement with the audio.** With 256
    candidate cells and a few hundred effectively independent samples, choosing
    cells by their correlation would manufacture a peak on any input, including
    silence. Everything here is audio-blind by construction.
    """
    variances = [
        squares[cell] / count - (totals[cell] / count) ** 2
        for cell in range(len(totals))
    ]
    keep = max(1, len(totals) // 10)
    return sorted(range(len(totals)), key=lambda cell: -variances[cell])[:keep]


def high_pass(curve: list[float], span: int) -> list[float]:
    """Subtract a moving average, leaving the fast structure. See `SMOOTH_SECONDS`."""
    out = []
    for index in range(len(curve)):
        low = max(0, index - span)
        high = min(len(curve), index + span + 1)
        out.append(curve[index] - sum(curve[low:high]) / (high - low))
    return out


def _score(
    motion: list[float], envelope: list[float], shift: int, base: int = 0
) -> float:
    """Correlation between the two curves when one is slid by `shift` frames.

    `base` is where `motion` starts inside `envelope`, so a window can be fitted
    against the *whole* audio track rather than a matching slice of it -- slicing
    both would make every sample near the window edge a guaranteed miss, which
    biases a windowed fit toward smaller shifts. Same reasoning, and the same
    bug, as `captions._score`.
    """
    left, right = [], []
    for index, value in enumerate(motion):
        source = base + index - shift
        if 0 <= source < len(envelope):
            left.append(value)
            right.append(envelope[source])
    if len(left) < 2:
        return 0.0
    try:
        return statistics.correlation(left, right)
    except statistics.StatisticsError:
        # One side is constant: a held frame, or digital silence. Not a
        # correlation of zero -- an absence of one.
        return 0.0


class Fit(NamedTuple):
    """The outcome of sliding one window of motion against the audio."""

    shift: int
    """Best-scoring shift, in frames."""

    peak: float
    """Its correlation."""

    rival: float
    """The best correlation more than one lobe away -- what the peak must beat."""

    saturated: bool
    """Whether the best shift sat at the edge of the search range."""

    flat: bool
    """Whether there was nothing to fit: no motion, or no sound."""

    @property
    def ratio(self) -> float:
        """Peak over rival. See `MIN_RATIO` -- this, not `peak`, is the evidence."""
        if self.rival <= 0:
            return 0.0 if self.peak <= 0 else float("inf")
        return self.peak / self.rival


class Sync(NamedTuple):
    """How the picture sits against the sound."""

    offset: float
    """Seconds the sound runs late, bias removed. Negative means early."""

    drift: float | None
    """How much the offset changes from the start of the file to the end.

    None when it could not be established -- too short, or one of the ends had
    nothing to fit. Reporting zero there would claim a measurement nobody took.
    """

    peak: float
    ratio: float

    distinct: bool
    """Whether one alignment actually stood out. False means "cannot tell"."""

    saturated: bool
    """Whether the fit sat at the edge of the search. `offset` is then a floor."""


def _best_shift(
    motion: list[float], envelope: list[float], reach: int, base: int = 0
) -> Fit:
    """Slide `motion` across `envelope` and report where it fits best."""
    if len(motion) < 2 or len(set(motion)) < 2:
        return Fit(shift=0, peak=0.0, rival=0.0, saturated=False, flat=True)

    scores = [
        (_score(motion, envelope, shift, base), shift)
        for shift in range(-reach, reach + 1)
    ]
    if not any(score for score, _ in scores):
        return Fit(shift=0, peak=0.0, rival=0.0, saturated=False, flat=True)

    # Ties break toward the *smallest* shift. `max()` over (score, shift) would
    # pick the largest among equals, turning every ambiguous fit into a
    # confident claim that the sound is late. Copied from `captions._best_shift`
    # because the failure it prevents is identical.
    peak, shift = max(scores, key=lambda pair: (pair[0], -abs(pair[1])))
    lobe = int(LOBE_SECONDS / BIN_SECONDS)
    rival = max((score for score, at in scores if abs(at - shift) > lobe), default=0.0)
    return Fit(
        shift=shift,
        peak=peak,
        rival=rival,
        saturated=abs(shift) == reach,
        flat=False,
    )


def align(
    motion: list[float],
    envelope: list[float],
    *,
    max_shift: float = MAX_SHIFT,
    bias: float = BIAS_SECONDS,
    min_ratio: float = MIN_RATIO,
) -> Sync | None:
    """Fit picture motion against the speech envelope.

    Returns None when there is nothing to fit. Returns a `Sync` with
    `distinct=False` when a fit was attempted and no alignment stood out -- that
    is a measurement failure, not a clean result, and the caller must skip.
    """
    if not motion or not envelope:
        return None

    span = int(SMOOTH_SECONDS / BIN_SECONDS)
    length = min(len(motion), len(envelope))
    reach_needed = int(max_shift / BIN_SECONDS)
    if length <= 2 * reach_needed:
        # Shorter than twice the distance we mean to slide. Two samples
        # correlate perfectly by definition, three nearly so, and a "best fit"
        # read off that is arithmetic rather than measurement. The caller has a
        # duration guard too; this one protects the arithmetic on its own.
        return None
    picture = high_pass(motion[:length], span)
    sound = high_pass(envelope[:length], span)
    reach = int(max_shift / BIN_SECONDS)

    whole = _best_shift(picture, sound, reach)
    if whole.flat:
        return None

    # Sliding the picture forward means testing the audio *earlier*, so a
    # positive shift is sound running EARLY. Report the opposite, because every
    # other timing check in this library says "positive means late". Pinned by
    # test_lipsync: delaying the audio by 0.2 s must move the reading by +0.2 s.
    raw = -whole.shift * BIN_SECONDS
    distinct = whole.ratio >= min_ratio and not whole.saturated

    # Drift: fit the two ends separately. A clip whose sync degrades as it runs
    # needs a different correction at the end than at the start, and no single
    # delay fixes it. Drift is also immune to `bias` -- both thirds carry the
    # same constant and it cancels in the difference -- which makes it the more
    # trustworthy of the two numbers even where it is the noisier.
    #
    # Three things disqualify it, all ordinary: a window shorter than the search
    # range, a window with nothing in it, or a window whose own fit ran to the
    # edge. Any of them and drift is None rather than arithmetic nobody can back.
    third = length // 3
    drift = None
    if distinct and third > reach:
        head = _best_shift(picture[:third], sound, reach)
        tail = _best_shift(picture[-third:], sound, reach, base=length - third)
        if not any((head.flat, tail.flat, head.saturated, tail.saturated)):
            drift = -(tail.shift - head.shift) * BIN_SECONDS

    return Sync(
        offset=raw - bias,
        drift=drift,
        peak=whole.peak,
        ratio=whole.ratio,
        distinct=distinct,
        saturated=whole.saturated,
    )
