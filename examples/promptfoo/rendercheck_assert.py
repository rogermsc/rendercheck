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
    collect_skips,
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

    ran = 0
    try:
        # Inside the try on purpose: a test naming a preset that does not exist,
        # or a `script` var pointing at a file that is not there, must be graded
        # as a failure with the reason rather than blow up the whole eval run.
        with collect_skips() as skipped:
            target = preset(str(variables.get("preset") or PRESET))
            checks = [
                lambda: assert_has_sound(path),
                lambda: assert_loudness(
                    path, target_lufs=target.target_lufs, tol=target.tol
                ),
                lambda: assert_no_dead_air(path),
                lambda: assert_no_truncation(path),
                lambda: assert_no_clipping(path),
            ]
            if target.max_true_peak is not None:
                checks.append(
                    lambda: assert_true_peak(path, max_dbtp=target.max_true_peak)
                )
            if script:
                checks.append(lambda: assert_pace(path, script))
            if captions:
                checks.append(lambda: assert_captions_aligned(path, captions))
            ran = len(checks)
            for check in checks:
                check()
    except SilentFail as exc:
        return {"pass": False, "score": 0, "reason": str(exc)}
    except (ValueError, OSError) as exc:
        # OSError covers FileNotFoundError from a `script` or `captions` var
        # naming something that is not there -- a test's own mistake, which
        # should fail that test rather than abort every other one.
        return {"pass": False, "score": 0, "reason": str(exc)}

    # The check that makes this assertion worth anything. Without ffmpeg every
    # check above skips, nothing raises, and the eval goes green having measured
    # nothing -- which is precisely the failure rendercheck exists to catch, so
    # it would be absurd to commit it here.
    if len(skipped) >= ran:
        return {
            "pass": False,
            "score": 0,
            "reason": (
                f"nothing could be measured about {path} -- all {ran} checks "
                f"skipped, so this is not a clean result. First reason: "
                f"{skipped[0] if skipped else 'unknown'}"
            ),
        }

    measured = ran - len(skipped)
    note = f" ({len(skipped)} skipped)" if skipped else ""
    return {
        "pass": True,
        "score": 1,
        "reason": f"rendercheck measured {measured} checks and found no defects{note}",
    }
