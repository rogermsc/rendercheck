"""`rendercheck check <file>` -- run every check that applies to one file.

Dispatches on file type and on which optional inputs you supplied. Checks that
need something you did not give (a script, an expected duration, a rubric) are
skipped and *said out loud*, so an empty run never reads as a clean one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from collections.abc import Callable, Iterator, Sequence
from functools import partial
from pathlib import Path
from typing import NamedTuple

from . import __version__, _ffmpeg, demo
from ._core import SilentFail, Skipped
from ._ffmpeg import ToolUnavailable
from .media import (
    assert_duration,
    assert_has_sound,
    assert_loudness,
    assert_no_black_frames,
    assert_no_clipping,
    assert_no_dead_air,
    assert_no_truncation,
    assert_not_frozen,
    assert_pace,
)
from .text import assert_speaker
from .vision import looks_ok

_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Suffix rather than a probe: deciding what to plan should not cost a subprocess.
# A video container with no picture still gets planned, and skips honestly.
_VIDEOS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg"}

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# 2 is "you asked for something impossible" (a path that isn't there); 1 is "I
# looked and it is broken". Keeping them apart matters in CI, where a typo and a
# defect need different reactions.
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2

_UNITS = {
    "pace": "WPM",
    "loudness": "LUFS",
    "duration": "s",
    "dead air": "s silence",
    "truncation": "dB of fall-off at the end",
    "clipping": "samples at 0 dBFS",
    "black frames": "s black",
    "frozen": "s frozen",
}


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
    if (
        name not in _UNITS
        or isinstance(result, bool)
        or not isinstance(result, int | float)
    ):
        return ""
    # Counts are integers; everything else is a reading with a decimal place.
    shown = f"{result}" if isinstance(result, int) else f"{result:.1f}"
    return f"{shown} {_UNITS[name]}"


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
        except ValueError as exc:
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

    # One probe up front, so a missing audio track is reported once instead of
    # by every audio check in turn. Four copies of the same sentence bury
    # whatever else is wrong with the file.
    try:
        audible = _ffmpeg.has_audio(path)
    except ToolUnavailable:
        audible = True  # cannot tell -- let each check speak for itself

    if not audible:
        yield Planned("has sound", partial(assert_has_sound, path))
    elif args.script:
        yield Planned(
            "pace",
            partial(
                assert_pace,
                path,
                args.script,
                max_wpm=args.max_wpm,
                min_wpm=args.min_wpm,
            ),
        )
        if args.presenter and not args.known_names:
            # Defaulting the roster to [presenter] makes the check structurally
            # incapable of firing -- a name is only wrong if it is on the roster
            # AND is not the assigned one. It would print PASS forever, which is
            # worse than not running it.
            yield Planned(
                "speaker",
                None,
                "--presenter needs --known-names: a roster holding only the "
                "assigned presenter can never report a mismatch",
            )
        elif args.presenter:
            yield Planned(
                "speaker",
                partial(assert_speaker, args.script, args.presenter, args.known_names),
            )
    else:
        yield Planned("pace", None, "no --script given")

    if audible:
        yield Planned(
            "loudness",
            partial(
                assert_loudness,
                path,
                target_lufs=args.target_lufs,
                tol=args.loudness_tol,
            ),
        )
        yield Planned(
            "dead air",
            partial(
                assert_no_dead_air,
                path,
                max_silence=args.max_silence,
                threshold_db=args.silence_threshold,
            ),
        )
        yield Planned(
            "truncation",
            partial(assert_no_truncation, path, min_drop_db=args.min_tail_drop),
        )
        yield Planned(
            "clipping",
            partial(assert_no_clipping, path, max_clipped_samples=args.max_clipped),
        )
    if args.expect_seconds:
        yield Planned(
            "duration",
            partial(
                assert_duration,
                path,
                args.expect_seconds,
                tol=args.duration_tol,
                min_ratio=args.min_ratio,
            ),
        )

    if path.suffix.lower() in _VIDEOS:
        yield Planned(
            "black frames",
            partial(assert_no_black_frames, path, max_seconds=args.max_black),
        )
        yield Planned(
            "frozen", partial(assert_not_frozen, path, max_seconds=args.max_freeze)
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rendercheck",
        description="Run every applicable check against one generated media file.",
    )
    parser.add_argument(
        "command",
        choices=["check", "demo"],
        help="check a file, or demo the checks on media generated for the purpose",
    )
    parser.add_argument(
        "file",
        type=Path,
        nargs="?",
        help="the generated media file to check",
    )
    parser.add_argument(
        "--version", action="version", version=f"rendercheck {__version__}"
    )
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
    parser.add_argument(
        "--expect-seconds", type=float, help="expected duration, in seconds"
    )
    parser.add_argument(
        "--max-wpm", type=float, default=245.0, help="fastest acceptable narration"
    )
    parser.add_argument(
        "--min-wpm", type=float, default=110.0, help="slowest acceptable narration"
    )
    parser.add_argument(
        "--target-lufs", type=float, default=-16.0, help="integrated loudness target"
    )
    parser.add_argument(
        "--loudness-tol", type=float, default=2.0, help="allowed dB either side"
    )
    parser.add_argument(
        "--max-silence", type=float, default=3.0, help="longest acceptable gap"
    )
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=-50.0,
        help="dB below which audio counts as silent; raise for recorded audio",
    )
    parser.add_argument(
        "--min-tail-drop",
        type=float,
        default=6.0,
        help="dB the ending must fall below the file's average; lower for music",
    )
    parser.add_argument(
        "--max-clipped", type=int, default=100, help="samples allowed at full scale"
    )
    parser.add_argument(
        "--max-black", type=float, default=1.0, help="longest acceptable black stretch"
    )
    parser.add_argument(
        "--max-freeze", type=float, default=3.0, help="longest acceptable frozen shot"
    )
    parser.add_argument(
        "--duration-tol", type=float, default=0.5, help="allowed drift, in seconds"
    )
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=0.5,
        help="below this share of the expected length it is a broken encode",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="a check that could not run counts as a failure",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable results on stdout, for calling from another pipeline",
    )
    return parser


def _print_report(results: list[Result]) -> None:
    width = max(len(r.name) for r in results)
    for status, name, detail in results:
        print(f"  {status}  {name:<{width}}  {detail}")


def _demo() -> int:
    """Run the real checks against media generated for the purpose."""
    try:
        cases = demo.build()
    except FileNotFoundError:
        print(
            "rendercheck: the demo needs ffmpeg on PATH to generate its files.\n"
            "  macOS   brew install ffmpeg\n"
            "  Debian  sudo apt-get install ffmpeg\n"
            "  Windows winget install ffmpeg",
            file=sys.stderr,
        )
        return EXIT_USAGE

    bold, plain = ("\033[1m", "\033[0m") if sys.stdout.isatty() else ("", "")
    root = demo.directory()
    print(f"Generated {len(cases)} defective files in {root}\n")

    # Run from inside that directory so the failure messages name the file the
    # way the printed command does. An absolute temp path in every line buries
    # the part that matters.
    previous = Path.cwd()
    os.chdir(root)
    try:
        for case in cases:
            argv = ["check", case.path.name, *case.args]
            args = _parser().parse_args(argv)
            print(f"{bold}{case.title}{plain}")
            print(f"  {case.story}")
            print(f"\n  $ rendercheck {' '.join(argv)}\n")
            _print_report(
                [_run(planned) for planned in _plan(Path(case.path.name), args)]
            )
            print()
    finally:
        os.chdir(previous)

    print(
        "Every line above is a real measurement of a real file -- nothing here\n"
        "is sample output. Point it at your own renders the same way."
    )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "demo":
        return _demo()
    if args.file is None:
        print("rendercheck: check needs a file to check", file=sys.stderr)
        return EXIT_USAGE
    if not args.file.exists():
        print(f"rendercheck: no such file: {args.file}", file=sys.stderr)
        return EXIT_USAGE
    try:
        results = [_run(planned) for planned in _plan(args.file, args)]
    except FileNotFoundError as exc:
        # A path you typed that isn't there is your bug, not the checker's.
        print(f"rendercheck: {exc}", file=sys.stderr)
        return EXIT_USAGE

    failures = sum(1 for r in results if r.status == FAIL)
    skips = sum(1 for r in results if r.status == SKIP)
    passed = len(results) - failures - skips

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
        _print_report(results)
        print(f"\n{passed} passed, {failures} failed, {skips} skipped")
        if not passed:
            print("nothing could be measured -- this is not a clean run")

    # An empty run is the failure this library is named after: every check
    # skipped means nothing was looked at, and a green build would be a lie.
    # That holds without --strict; --strict additionally rejects partial runs.
    if failures or not passed or (args.strict and skips):
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
