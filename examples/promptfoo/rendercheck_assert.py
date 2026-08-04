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
    assert_has_sound,
    assert_loudness,
    assert_no_clipping,
    assert_no_dead_air,
    assert_no_truncation,
    assert_pace,
)

# The one number here worth arguing about. -16 LUFS suits speech cut against
# other speech; -14 is the streaming-music convention, -19 to -23 broadcast.
# Set it to whatever the rest of your audio is mastered to.
TARGET_LUFS = -16.0


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
    # nothing to time the delivery against.
    script = (context or {}).get("vars", {}).get("script")

    try:
        assert_has_sound(path)
        assert_loudness(path, target_lufs=TARGET_LUFS)
        assert_no_dead_air(path)
        assert_no_truncation(path)
        assert_no_clipping(path)
        if script:
            assert_pace(path, script)
    except SilentFail as exc:
        return {"pass": False, "score": 0, "reason": str(exc)}

    return {"pass": True, "score": 1, "reason": "rendercheck found no defects"}
