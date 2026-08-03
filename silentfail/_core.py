"""Failure semantics shared by every check.

Two rules, both learned the expensive way in a production media pipeline:

1. **The message is a bug report.** Every failure names the measured value, the
   threshold it broke, and what a human would actually perceive. The text of the
   exception is the deliverable -- it is what gets pasted into an issue.

2. **Fail open on infrastructure.** No ffmpeg, no network, no API key, no
   measurement -> warn and pass. A gate that blocks the pipeline because of its
   own breakage gets deleted within a week, and then it protects nothing.
   A *defect* fails closed; the *checker* fails open.
"""

from __future__ import annotations

import warnings
from pathlib import Path


class SilentFail(AssertionError):
    """A defect that would not otherwise have thrown.

    Subclasses ``AssertionError`` so pytest reports it like any other failed
    assertion, while callers who want to catch only these still can.
    """


class Skipped(UserWarning):
    """A check could not run. Warned, never raised -- see rule 2."""


def skip(reason: str) -> None:
    """Fail open: this check could not run, so it does not get a vote."""
    warnings.warn(reason, Skipped, stacklevel=3)


def existing(path: str | Path) -> Path:
    """Resolve `path`, raising if it is missing.

    A missing file is a caller mistake, not an infrastructure failure, so it
    raises rather than skipping -- silently passing a typo'd path is exactly
    the class of bug this library exists to catch.
    """
    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"no such file: {resolved}")
    return resolved


def timestamp(seconds: float) -> str:
    """Seconds -> `m:ss`, so a finding points at where to look."""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"
