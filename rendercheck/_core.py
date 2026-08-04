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
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_collecting: ContextVar[list[str] | None] = ContextVar(
    "rendercheck_skips", default=None
)


class SilentFail(AssertionError):
    """A defect that would not otherwise have thrown.

    Subclasses ``AssertionError`` so pytest reports it like any other failed
    assertion, while callers who want to catch only these still can.
    """


class Skipped(UserWarning):
    """A check could not run. Warned, never raised -- see rule 2."""


def skip(reason: str) -> None:
    """Fail open: this check could not run, so it does not get a vote.

    Warns, unless something is `collect_skips()`-ing on this thread -- see there
    for why the runner cannot use the warnings machinery for this.
    """
    sink = _collecting.get()
    if sink is not None:
        sink.append(reason)
        return
    warnings.warn(reason, Skipped, stacklevel=3)


@contextmanager
def collect_skips() -> Iterator[list[str]]:
    """Gather the skips raised on *this thread*, without touching global state.

    `warnings.catch_warnings(record=True)` is documented as not thread-safe: it
    swaps process-wide filters, so two threads inside it at once can have their
    warnings land in each other's lists. The runner checks several files on a
    thread pool, and a skip that lands in the wrong list leaves the check that
    actually skipped looking like it returned nothing and reported nothing --
    which the runner then records as a **pass**. An unmeasurable check counted
    as green is the exact failure this library exists to catch, so the runner
    collects through a `ContextVar` instead, which is per-thread by definition.

    Library callers are unaffected: with no collector active `skip()` warns
    exactly as it always has.
    """
    sink: list[str] = []
    token = _collecting.set(sink)
    try:
        yield sink
    finally:
        _collecting.reset(token)


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
