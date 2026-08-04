"""rendercheck as a promptfoo assertion.

promptfoo evaluates *text*: its assertions receive a string and compare it to
another string. That is the right shape for a prompt and the wrong shape for a
rendered file, so the output under test here is a **path** to the media your
provider produced, and rendercheck measures the file behind it.

Wire it up in promptfooconfig.yaml:

    assert:
      - type: python
        value: file://rendercheck_assert.py:get_assert

The exception message is passed through untouched as `reason`, because that
message is the actual product: it names the measured value, the threshold it
broke, and what a listener would notice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rendercheck import (
    SilentFail,
    assert_captions_aligned,
    assert_has_sound,
    assert_loudness,
    assert_no_clipping,
    assert_no_dead_air,
    assert_no_truncation,
    assert_pace,
    assert_true_peak,
)
from rendercheck.presets import get as preset
from rendercheck.text import find_captions

# Where the file is going, which is what decides how loud it should be. Any name
# from `rendercheck presets`: youtube, spotify, tiktok, podcast, apple, web, ebu,
# atsc, netflix. A test can override it per-case with a `preset` var.
PRESET = "web"


def get_assert(output: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return promptfoo's GradingResult for one generated media file."""
    path = Path(str(output).strip())
    if not path.exists():
        return {
            "pass": False,
            "score": 0,
            "reason": (
                f"{path} does not exist -- this assertion expects the provider's "
                f"output to be a path to the rendered file"
            ),
        }

    # A script in the test's vars turns on the pace check; without one there is
    # nothing to time the delivery against. Captions likewise -- and a `.vtt` or
    # `.srt` sitting beside the media is found without being named.
    variables = (context or {}).get("vars", {})
    script = variables.get("script")
    captions = variables.get("captions") or find_captions(path)

    try:
        # Inside the try on purpose: a test naming a preset that does not exist
        # must be graded as a failure with the reason, not blow up the eval run.
        target = preset(str(variables.get("preset") or PRESET))
        assert_has_sound(path)
        assert_loudness(path, target_lufs=target.target_lufs, tol=target.tol)
        assert_true_peak(path, max_dbtp=target.max_true_peak)
        assert_no_dead_air(path)
        assert_no_truncation(path)
        assert_no_clipping(path)
        if script:
            assert_pace(path, script)
        if captions:
            assert_captions_aligned(path, captions)
    except SilentFail as exc:
        return {"pass": False, "score": 0, "reason": str(exc)}
    except ValueError as exc:
        # An unknown preset name, most likely. A test that names one that does
        # not exist should say so rather than quietly grade against the default.
        return {"pass": False, "score": 0, "reason": str(exc)}

    return {"pass": True, "score": 1, "reason": "rendercheck found no defects"}
