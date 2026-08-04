"""Loudness targets, per platform.

The single most common question about a generated audio file is "how loud should
this be?", and the answer is never one number -- it depends entirely on where the
file is going. YouTube plays back at a different level than a broadcast chain,
and a file mastered for one sounds wrong on the other.

None of these figures are ours. They are the published normalisation targets of
the platforms themselves, and the point of putting them here is that a target
you can *name* is a target that ends up in CI. `-16` in a workflow file is a
magic number the next person will not dare change; `--preset podcast` is a
decision they can read.

Every row carries a true-peak ceiling as well as an integrated target. Loudness
and peak are separate problems: a correctly-levelled file can still clip when a
lossy codec reconstructs it, which is why the ceilings sit below 0 dBTP rather
than at it.
"""

from __future__ import annotations

from typing import NamedTuple


class Preset(NamedTuple):
    """One platform's published delivery spec."""

    target_lufs: float
    """Integrated loudness the platform normalises to."""

    tol: float
    """How far either side still lands acceptably."""

    max_true_peak: float
    """Ceiling in dBTP, below 0 to leave the codec room to reconstruct."""

    note: str
    """What this row is for, and where the number comes from."""


# Tolerances are ours, not the platforms': a spec states a target, and what a
# gate needs is the width of the band around it. They widen where the content
# itself is more variable -- speech-only material holds a target far more tightly
# than a mixed programme does.
PRESETS: dict[str, Preset] = {
    "youtube": Preset(-14.0, 1.0, -1.0, "YouTube normalises playback to -14 LUFS"),
    "spotify": Preset(-14.0, 1.0, -1.0, "Spotify, including podcasts, at -14 LUFS"),
    "tiktok": Preset(
        -14.0, 1.5, -1.0, "TikTok and Instagram, measured rather than published"
    ),
    "podcast": Preset(
        -16.0, 1.0, -1.0, "AES71 / Apple Podcasts: -16 LUFS stereo, -19 mono"
    ),
    "apple": Preset(-16.0, 1.0, -1.0, "Apple Music Sound Check, -16 LUFS"),
    "web": Preset(
        -16.0, 2.0, -1.0, "spoken-word web video -- rendercheck's default target"
    ),
    "ebu": Preset(-23.0, 1.0, -1.0, "EBU R128, European broadcast"),
    "atsc": Preset(-24.0, 2.0, -2.0, "ATSC A/85, North American broadcast"),
    "netflix": Preset(-27.0, 2.0, -2.0, "Netflix delivery, dialog-gated"),
}

DEFAULT = "web"
"""The preset the library's own defaults correspond to.

`web` exists so the default is nameable. Changing it would change every existing
caller's behaviour silently, which is the class of thing this library is against.
"""


def get(name: str) -> Preset:
    """Look up a preset, listing the alternatives when the name is wrong."""
    try:
        return PRESETS[name.strip().lower()]
    except KeyError:
        raise ValueError(
            f"unknown preset {name!r} -- available: {', '.join(sorted(PRESETS))}"
        ) from None


def table() -> str:
    """The whole table, formatted for a terminal."""
    width = max(len(name) for name in PRESETS)
    lines = [f"  {'preset':<{width}}  {'target':>7}  {'tol':>6}  {'peak':>7}  source"]
    for name, preset in PRESETS.items():
        lines.append(
            f"  {name:<{width}}  {preset.target_lufs:>6.0f}L  "
            f"{preset.tol:>4.1f}dB  {preset.max_true_peak:>5.1f}TP  {preset.note}"
        )
    return "\n".join(lines)
