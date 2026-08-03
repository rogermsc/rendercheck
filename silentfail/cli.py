"""`silentfail check <file>` -- run every check that applies to one file.

Dispatches on file type and on which optional inputs you supplied. Checks that
need something you did not give (a script, an expected duration, a rubric) are
skipped and *said out loud*, so an empty run never reads as a clean one.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections.abc import Callable, Iterator, Sequence
from functools import partial
from pathlib import Path
from typing import NamedTuple

from ._core import SilentFail, Skipped
from .media import assert_duration, assert_loudness, assert_no_dead_air, assert_pace
from .text import assert_speaker
from .vision import looks_ok

_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

_UNITS = {"pace": "WPM", "loudness": "LUFS", "duration": "s", "dead air": "s silence"}


class Planned(NamedTuple):
    """A check to run, or a reason it was never runnable."""

    name: str
    run: Callable[[], object] | None
    reason: str = ""


class Result(NamedTuple):
    status: str
    name: str
    detail: str


def _measured(name: str, result: object) -> str:
    if not isinstance(result, float) or name not in _UNITS:
        return ""
    return f"{result:.1f} {_UNITS[name]}"


def _run(planned: Planned) -> Result:
    """Run one check. Never raises -- the report is the product."""
    if planned.run is None:
        return Result(SKIP, planned.name, planned.reason)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", Skipped)
        try:
            result = planned.run()
        except SilentFail as exc:
            return Result(FAIL, planned.name, str(exc))
        except (FileNotFoundError, ValueError) as exc:
            return Result(SKIP, planned.name, str(exc))
        skipped = [str(w.message) for w in caught if issubclass(w.category, Skipped)]

    if result is None and skipped:
        return Result(SKIP, planned.name, skipped[0])
    detail = "; ".join(skipped) if skipped else _measured(planned.name, result)
    return Result(PASS, planned.name, detail)


def _plan(path: Path, args: argparse.Namespace) -> Iterator[Planned]:
    """Everything applicable to this file, given what the caller supplied."""
    if path.suffix.lower() in _IMAGES:
        if args.rubric:
            yield Planned("looks ok", partial(looks_ok, path, args.rubric))
        else:
            yield Planned(
                "looks ok",
                None,
                "no --rubric given; nothing to check the image against",
            )
        return

    if args.script:
        yield Planned(
            "pace", partial(assert_pace, path, args.script, max_wpm=args.max_wpm)
        )
        if args.presenter:
            yield Planned(
                "speaker",
                partial(
                    assert_speaker,
                    args.script,
                    args.presenter,
                    args.known_names or [args.presenter],
                ),
            )
    else:
        yield Planned("pace", None, "no --script given")

    yield Planned(
        "loudness", partial(assert_loudness, path, target_lufs=args.target_lufs)
    )
    yield Planned(
        "dead air", partial(assert_no_dead_air, path, max_silence=args.max_silence)
    )
    if args.expect_seconds:
        yield Planned("duration", partial(assert_duration, path, args.expect_seconds))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="silentfail",
        description="Run every applicable check against one generated media file.",
    )
    parser.add_argument("command", choices=["check"])
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--script", help="narration text, or a path to a transcript/.vtt/.srt"
    )
    parser.add_argument(
        "--presenter", help="name of the presenter who should be on screen"
    )
    parser.add_argument(
        "--known-names",
        nargs="+",
        metavar="NAME",
        help="roster of real presenters, so story characters don't trip the check",
    )
    parser.add_argument(
        "--rubric",
        nargs="+",
        metavar="CLAIM",
        help="plain-English claims an image must satisfy",
    )
    parser.add_argument("--expect-seconds", type=float, help="expected duration")
    parser.add_argument("--max-wpm", type=float, default=245.0)
    parser.add_argument("--target-lufs", type=float, default=-16.0)
    parser.add_argument("--max-silence", type=float, default=3.0)
    parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable results on stdout, for calling from another pipeline",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    results = [_run(planned) for planned in _plan(args.file, args)]

    failures = sum(1 for r in results if r.status == FAIL)
    skips = sum(1 for r in results if r.status == SKIP)

    if args.json:
        print(
            json.dumps(
                {
                    "file": str(args.file),
                    # Keys are contract: other pipelines parse this. `check`,
                    # not `name` -- do not "tidy" it into NamedTuple._asdict().
                    "results": [
                        {"status": r.status, "check": r.name, "detail": r.detail}
                        for r in results
                    ],
                    "failed": failures,
                    "skipped": skips,
                }
            )
        )
    else:
        width = max(len(r.name) for r in results)
        for status, name, detail in results:
            print(f"  {status}  {name:<{width}}  {detail}")
        passed = len(results) - failures - skips
        print(f"\n{passed} passed, {failures} failed, {skips} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
