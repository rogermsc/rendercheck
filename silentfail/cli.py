"""`silentfail check <file>` -- run every check that applies to one file.

Dispatches on file type and on which optional inputs you supplied. Checks that
need something you did not give (a script, an expected duration, a rubric) are
skipped and *said out loud*, so an empty run never reads as a clean one.
"""

import argparse
import sys
import warnings
from pathlib import Path

from ._core import SilentFail, Skipped
from .media import assert_duration, assert_loudness, assert_no_dead_air, assert_pace
from .text import assert_speaker
from .vision import looks_ok

_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def _run(name, check, *args, **kwargs):
    """Run one check, returning (status, name, detail) and never raising."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", Skipped)
        try:
            result = check(*args, **kwargs)
        except SilentFail as exc:
            return FAIL, name, str(exc)
        except (FileNotFoundError, ValueError) as exc:
            return SKIP, name, str(exc)
        skipped = [str(w.message) for w in caught if issubclass(w.category, Skipped)]
    if result is None and skipped:
        return SKIP, name, skipped[0]
    detail = "; ".join(skipped) if skipped else _measured(name, result)
    return PASS, name, detail


def _measured(name, result):
    if result is None:
        return ""
    units = {"pace": "WPM", "loudness": "LUFS", "duration": "s", "dead air": "s silence"}
    return f"{result:.1f} {units[name]}" if name in units else ""


def _checks(path, args):
    """Yield (name, callable, args, kwargs) for everything applicable."""
    if path.suffix.lower() in _IMAGES:
        if args.rubric:
            yield "looks ok", looks_ok, (path, args.rubric), {}
        else:
            yield SKIP, "looks ok", "no --rubric given; nothing to check the image against"
        return

    if args.script:
        yield "pace", assert_pace, (path, args.script), {"max_wpm": args.max_wpm}
        if args.presenter:
            yield "speaker", assert_speaker, (
                args.script, args.presenter, args.known_names or [args.presenter]
            ), {}
    else:
        yield SKIP, "pace", "no --script given"
    yield "loudness", assert_loudness, (path,), {"target_lufs": args.target_lufs}
    yield "dead air", assert_no_dead_air, (path,), {"max_silence": args.max_silence}
    if args.expect_seconds:
        yield "duration", assert_duration, (path, args.expect_seconds), {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="silentfail", description=__doc__.splitlines()[0]
    )
    parser.add_argument("command", choices=["check"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--script", help="narration text, or a path to a transcript/.vtt/.srt")
    parser.add_argument("--presenter", help="name of the presenter who should be on screen")
    parser.add_argument("--known-names", nargs="+", metavar="NAME",
                        help="roster of real presenters, so story characters don't trip the check")
    parser.add_argument("--rubric", nargs="+", metavar="CLAIM",
                        help="plain-English claims an image must satisfy")
    parser.add_argument("--expect-seconds", type=float, help="expected duration")
    parser.add_argument("--max-wpm", type=float, default=245.0)
    parser.add_argument("--target-lufs", type=float, default=-16.0)
    parser.add_argument("--max-silence", type=float, default=3.0)
    args = parser.parse_args(argv)

    results = []
    for item in _checks(args.file, args):
        if item[0] == SKIP:  # pre-declared skip, nothing to run
            results.append(item)
            continue
        name, check, call_args, call_kwargs = item
        results.append(_run(name, check, *call_args, **call_kwargs))

    width = max(len(name) for _, name, _ in results)
    for status, name, detail in results:
        print(f"  {status}  {name:<{width}}  {detail}")

    failures = sum(1 for status, _, _ in results if status == FAIL)
    skips = sum(1 for status, _, _ in results if status == SKIP)
    print(f"\n{len(results) - failures - skips} passed, {failures} failed, {skips} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
