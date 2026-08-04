"""rendercheck -- assertions for AI-generated media.

The worst bugs in generated media don't throw. The narration is too fast, the
voice track is 20 dB quieter than the avatar it's cut against, the segment
rendered at 40% length, the wrong presenter is on screen for a whole module.
None of these raise an exception, so the detector ends up being a human
watching the finished output -- the most expensive one available.

These are plain assert functions. They raise, so they work in pytest, in CI,
or in a bare script, with nothing to learn:

    from rendercheck import assert_pace, assert_loudness, looks_ok

    assert_pace("lesson-1.2.mp4", "lesson-1.2.vtt")
    assert_loudness("lesson-1.2.mp4")
    looks_ok("slide-14.png", ["the title fits on one line"])

Everything except `looks_ok` is deterministic: no key, no network, no model.
Every check fails *open* on infrastructure trouble -- if it cannot measure, it
warns and passes rather than blocking your pipeline on its own breakage.
"""

from ._core import SilentFail, Skipped
from .media import (
    assert_captions_aligned,
    assert_duration,
    assert_format,
    assert_has_sound,
    assert_loudness,
    assert_no_black_frames,
    assert_no_clipping,
    assert_no_dead_air,
    assert_no_truncation,
    assert_not_frozen,
    assert_pace,
    assert_streams_aligned,
    assert_true_peak,
)
from .presets import PRESETS, Preset
from .text import assert_speaker
from .vision import looks_ok

__version__ = "0.3.0"

__all__ = [
    "PRESETS",
    "Preset",
    "SilentFail",
    "Skipped",
    "assert_captions_aligned",
    "assert_duration",
    "assert_format",
    "assert_has_sound",
    "assert_loudness",
    "assert_no_black_frames",
    "assert_no_clipping",
    "assert_no_dead_air",
    "assert_no_truncation",
    "assert_not_frozen",
    "assert_pace",
    "assert_speaker",
    "assert_streams_aligned",
    "assert_true_peak",
    "looks_ok",
]
