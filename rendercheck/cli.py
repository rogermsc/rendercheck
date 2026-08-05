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
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import NamedTuple

from . import __version__, _ffmpeg, config, demo, presets
from ._core import SilentFail, collect_skips
from ._ffmpeg import ToolUnavailable
from .media import (
    assert_audio_format,
    assert_captions_aligned,
    assert_duration,
    assert_format,
    assert_has_sound,
    assert_loudness,
    assert_loudness_range,
    assert_no_black_frames,
    assert_no_clipping,
    assert_no_dead_air,
    assert_no_truncation,
    assert_not_blank,
    assert_not_frozen,
    assert_pace,
    assert_streams_aligned,
    assert_true_peak,
)
from .text import assert_speaker, find_captions
from .vision import looks_ok

_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Suffix rather than a probe: deciding what to plan should not cost a subprocess.
# A video container with no picture still gets planned, and skips honestly.
_VIDEOS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg"}
_AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
_MEDIA = _IMAGES | _VIDEOS | _AUDIO

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# 2 is "you asked for something impossible" (a path that isn't there); 1 is "I
# looked and it is broken". Keeping them apart matters in CI, where a typo and a
# defect need different reactions.
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2

_UNITS = {
    "pace": "WPM",
    "loudness": "LUFS",
    "loudness range": "LU",
    "true peak": "dBTP",
    "blank": "levels of luma spread",
    "duration": "s",
    "dead air": "s silence",
    "truncation": "dB of fall-off at the end",
    "clipping": "samples at 0 dBFS",
    "black frames": "s black",
    "frozen": "s frozen",
    "captions": "s offset",
    "streams": "s between stream endings",
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

    # `collect_skips`, not `warnings.catch_warnings`: files are checked on a
    # thread pool and the warnings machinery is process-global, so one file's
    # skip could be recorded against another -- leaving the check that really
    # skipped looking like a pass. See `_core.collect_skips`.
    with collect_skips() as skipped:
        try:
            result = planned.run()
        except SilentFail as exc:
            return Result(FAIL, planned.name, str(exc))
        except ValueError as exc:
            return Result(SKIP, planned.name, str(exc))

    if result is None and skipped:
        return Result(SKIP, planned.name, skipped[0])
    detail = "; ".join(skipped) if skipped else _measured(planned.name, result)
    return Result(PASS, planned.name, detail)


def _plan(path: Path, args: argparse.Namespace) -> Iterator[Planned]:
    """Everything applicable to this file, given what the caller supplied."""
    if path.suffix.lower() in _IMAGES:
        # Unconditional, and the only check here that needs no key and no rubric.
        # Before it, a still with no --rubric produced a single SKIP and exit 1 --
        # the tool had nothing to say about the most common way an image
        # generator fails, which is to return a valid blank canvas.
        yield Planned(
            "blank",
            partial(assert_not_blank, path, min_spread=args.min_image_spread),
        )
        if args.rubric:
            yield Planned("looks ok", partial(looks_ok, path, args.rubric))
        else:
            yield Planned(
                "looks ok",
                None,
                "no --rubric given; nothing to check the image against",
            )
        # An image has dimensions, so a size requirement applies to it. Returning
        # here without a word would discard something the caller asked for, and
        # the run would go green with the requirement never compared.
        if args.expect_width or args.expect_height:
            yield Planned(
                "format",
                partial(
                    assert_format,
                    path,
                    width=args.expect_width,
                    height=args.expect_height,
                ),
            )
        for flag, given in (
            ("--expect-fps", args.expect_fps),
            ("--expect-seconds", args.expect_seconds),
        ):
            if given:
                yield Planned(
                    flag.lstrip("-"), None, f"{flag} does not apply to a still"
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
        # Only when a ceiling was actually asked for. There is no universal
        # true-peak limit -- it belongs to wherever the file is going -- and
        # inventing one would start failing files that were fine yesterday.
        if args.max_true_peak is not None:
            yield Planned(
                "true peak",
                partial(assert_true_peak, path, max_dbtp=args.max_true_peak),
            )
        yield Planned(
            "loudness range",
            partial(assert_loudness_range, path, max_lra=args.max_lra),
        )
        # Only when asked for, like true peak: there is no universal sample rate
        # or channel count, only the one your delivery spec states.
        if args.expect_sample_rate or args.expect_channels:
            yield Planned(
                "audio format",
                partial(
                    assert_audio_format,
                    path,
                    sample_rate=args.expect_sample_rate,
                    channels=args.expect_channels,
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
        # Sidecar captions are named after their media, so the common case needs
        # no flag. An explicit --captions always wins over what is lying around.
        beside = Path(args.captions) if args.captions else find_captions(path)
        if beside:
            yield Planned(
                "captions",
                partial(
                    assert_captions_aligned,
                    path,
                    beside,
                    max_offset=args.max_caption_offset,
                    max_drift=args.max_caption_drift,
                ),
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
        yield Planned(
            "streams",
            partial(
                assert_streams_aligned,
                path,
                max_gap=args.max_stream_gap,
                max_start_skew=args.max_start_skew,
            ),
        )
        if args.expect_width or args.expect_height or args.expect_fps:
            yield Planned(
                "format",
                partial(
                    assert_format,
                    path,
                    width=args.expect_width,
                    height=args.expect_height,
                    fps=args.expect_fps,
                ),
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rendercheck",
        description="Run every applicable check against one generated media file.",
    )
    parser.add_argument(
        "command",
        choices=["check", "demo", "presets", "mcp"],
        help=(
            "check a file, demo the checks on media generated for the purpose, "
            "list the loudness presets, or serve them over MCP"
        ),
    )
    parser.add_argument(
        "file",
        type=Path,
        nargs="*",
        help="media files to check; a directory checks the media inside it",
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
        "--captions",
        help="a .vtt or .srt to check against the audio; found beside the media "
        "by default",
    )
    parser.add_argument(
        "--preset",
        choices=sorted(presets.PRESETS),
        help="loudness target for where this is going -- see `rendercheck presets`",
    )
    parser.add_argument(
        "--expect-seconds", type=float, help="expected duration, in seconds"
    )
    parser.add_argument("--expect-width", type=int, help="expected picture width")
    parser.add_argument("--expect-height", type=int, help="expected picture height")
    parser.add_argument("--expect-fps", type=float, help="expected frame rate")
    parser.add_argument(
        "--expect-sample-rate", type=int, help="expected audio sample rate, in Hz"
    )
    parser.add_argument(
        "--expect-channels", type=int, help="expected channel count (1 mono, 2 stereo)"
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
        "--max-true-peak",
        type=float,
        help="true-peak ceiling in dBTP; off unless given or implied by --preset",
    )
    parser.add_argument(
        "--max-lra",
        type=float,
        default=15.0,
        help="widest acceptable swing between quiet and loud parts, in LU",
    )
    parser.add_argument(
        "--min-image-spread",
        type=float,
        default=16.0,
        help="luma levels a still must span before it counts as not blank",
    )
    parser.add_argument(
        "--max-caption-offset",
        type=float,
        default=0.75,
        help="seconds the captions may sit away from the speech",
    )
    parser.add_argument(
        "--max-caption-drift",
        type=float,
        default=1.0,
        help="seconds that offset may change between the file's start and end",
    )
    parser.add_argument(
        "--max-stream-gap",
        type=float,
        default=0.5,
        help="seconds sound and picture may differ in length",
    )
    parser.add_argument(
        "--max-start-skew",
        type=float,
        default=0.25,
        help="seconds sound and picture may differ in where they start",
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


def _parse(
    argv: Sequence[str] | None, *, use_config: bool = True
) -> argparse.Namespace:
    """Parse, layering the config file and then the preset under the flags.

    Both go in as `set_defaults` before the real parse, so an explicit
    `--target-lufs` still beats `--preset youtube`, which in turn beats whatever
    `rendercheck.toml` said. A flag someone typed is a decision; the rest are
    starting points.

    `use_config=False` is for the demo, whose output has to be the same
    everywhere -- a threshold from the reader's own project would quietly change
    what the demo claims to prove.
    """
    parser = _parser()
    if use_config:
        settings = _from_config(parser)
        if settings:
            parser.set_defaults(**settings)

    sniffed, _ = parser.parse_known_args(argv)
    if sniffed.preset:
        preset = presets.get(sniffed.preset)
        overrides: dict[str, object] = {
            "target_lufs": preset.target_lufs,
            "loudness_tol": preset.tol,
        }
        # A preset without a stated ceiling leaves the true-peak check off, so
        # naming the default (`--preset web`) really is the no-op that
        # `rendercheck presets` says it is. Setting it unconditionally would
        # make spelling out the default *add* a gate no bare run applies.
        if preset.max_true_peak is not None:
            overrides["max_true_peak"] = preset.max_true_peak
        parser.set_defaults(**overrides)
    return parser.parse_args(argv)


def _from_config(parser: argparse.ArgumentParser) -> dict[str, object]:
    """Settings from the config file, validated the way argparse would have.

    `set_defaults` bypasses every guarantee the flag path gives: no `type=`, no
    `choices`, no `nargs`. Left alone, `known_names = "Karl"` arrives as a string
    the speaker check then iterates letter by letter -- a roster of `{k,a,r,l}`
    that can never match, so the check prints PASS forever. That is precisely
    the "structurally incapable of firing" failure this CLI already refuses to
    ship on the flag path.
    """
    # argparse has no public way to enumerate actions, so a throwaway parse
    # yields the destinations and the parser itself yields their types.
    actions = {a.dest: a for a in parser._actions if a.option_strings}
    settings = config.load(known=set(actions))

    checked: dict[str, object] = {}
    for key, value in settings.items():
        action = actions[key]
        try:
            checked[key] = _as_argparse_would(action, value)
        except (TypeError, ValueError) as exc:
            print(
                f"rendercheck: ignoring {key} in the config file -- {exc}",
                file=sys.stderr,
            )
    return checked


def _as_argparse_would(action: argparse.Action, value: object) -> object:
    """Check one config value the way the flag path would, or say why not.

    Raises `TypeError`/`ValueError` with a message naming what was expected.
    TOML already carries real types, so this validates rather than parses --
    the failure mode worth guarding is a value of the *wrong shape* arriving
    somewhere that will use it without complaint.
    """
    if isinstance(action, argparse._StoreTrueAction | argparse._StoreFalseAction):
        if not isinstance(value, bool):
            raise TypeError(f"expected true or false, got {value!r}")
        return value
    if action.nargs in ("+", "*"):
        if not isinstance(value, list):
            # The single most damaging case, because a scalar where a list
            # belongs is still iterable: `known_names = "Karl"` becomes the
            # roster {k, a, r, l}, which can never match anyone, so the speaker
            # check prints PASS forever instead of refusing to run.
            raise TypeError(f"expected a list, got {value!r}")
        return [str(item) for item in value]
    if isinstance(value, list | dict):
        raise TypeError(f"expected a single value, got {value!r}")
    # `bool` is a subclass of `int`, so it would sail through the number checks
    # below and arrive as 0 or 1.
    if action.type is int:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(f"expected a whole number, got {value!r}")
        return int(value)
    if action.type is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(f"expected a number, got {value!r}")
        return float(value)
    if action.choices is not None and value not in action.choices:
        raise ValueError(
            f"{value!r} is not one of: {', '.join(sorted(map(str, action.choices)))}"
        )
    return value


_COLOURS = {PASS: "\033[32m", FAIL: "\033[31m", SKIP: "\033[33m"}
_RESET = "\033[0m"


def _colour() -> bool:
    """Colour only for a human at a terminal, and never against NO_COLOR."""
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _print_report(results: list[Result]) -> None:
    paint = _colour()
    width = max(len(r.name) for r in results)
    for status, name, detail in results:
        label = f"{_COLOURS[status]}{status}{_RESET}" if paint else status
        print(f"  {label}  {name:<{width}}  {detail}")


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

    bold, plain = ("\033[1m", _RESET) if _colour() else ("", "")
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
            args = _parse(argv, use_config=False)
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


def _expand(paths: list[Path]) -> list[Path]:
    """Directories become the media files inside them, one level down.

    Shells already expand globs; what they do not do is turn `out/` into the
    twelve renders in it, which is the shape a CI step actually has.
    """
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(
                sorted(c for c in path.iterdir() if c.suffix.lower() in _MEDIA)
            )
        else:
            found.append(path)
    return found


def _report(path: Path, results: list[Result], args: argparse.Namespace) -> None:
    if args.json:
        print(
            json.dumps(
                {
                    "file": str(path),
                    # Keys are contract: other pipelines parse this. `check`,
                    # not `name` -- do not "tidy" it into NamedTuple._asdict().
                    "results": [
                        {"status": r.status, "check": r.name, "detail": r.detail}
                        for r in results
                    ],
                    "failed": sum(1 for r in results if r.status == FAIL),
                    "skipped": sum(1 for r in results if r.status == SKIP),
                }
            )
        )
    else:
        _print_report(results)


def _verdict(results: list[Result], strict: bool) -> int:
    failures = sum(1 for r in results if r.status == FAIL)
    skips = sum(1 for r in results if r.status == SKIP)
    passed = len(results) - failures - skips
    # An empty run is the failure this library is named after: every check
    # skipped means nothing was looked at, and a green build would be a lie.
    # That holds without --strict; --strict additionally rejects partial runs.
    if failures or not passed or (strict and skips):
        return EXIT_FAILED
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    if args.command == "demo":
        return _demo()
    if args.command == "presets":
        print("Loudness targets, by where the file is going.\n")
        print(presets.table())
        print(
            f"\n  rendercheck check narration.wav --preset youtube\n\n"
            f"Without one, the target is {presets.get(presets.DEFAULT).target_lufs:g} "
            f"LUFS -- the `{presets.DEFAULT}` row."
        )
        return EXIT_OK
    if args.command == "mcp":
        from .mcp import serve

        return serve()
    if not args.file:
        print("rendercheck: check needs at least one file", file=sys.stderr)
        return EXIT_USAGE
    missing = [p for p in args.file if not p.exists()]
    if missing:
        for path in missing:
            print(f"rendercheck: no such file: {path}", file=sys.stderr)
        return EXIT_USAGE
    targets = _expand(args.file)
    if not targets:
        print("rendercheck: no media files found to check", file=sys.stderr)
        return EXIT_USAGE

    try:
        if len(targets) == 1:
            batch = [(targets[0], [_run(p) for p in _plan(targets[0], args)])]
        else:
            # Decoding is subprocess-bound, so threads are the right tool and
            # nothing here shares state between files.
            with ThreadPoolExecutor() as pool:
                batch = list(
                    pool.map(lambda t: (t, [_run(p) for p in _plan(t, args)]), targets)
                )
    except FileNotFoundError as exc:
        # A path you typed that isn't there is your bug, not the checker's.
        print(f"rendercheck: {exc}", file=sys.stderr)
        return EXIT_USAGE

    worst = EXIT_OK
    for path, results in batch:
        if len(batch) > 1 and not args.json:
            print(f"\n{path}")
        _report(path, results, args)
        worst = max(worst, _verdict(results, args.strict))

    if not args.json:
        every = [r for _, results in batch for r in results]
        failures = sum(1 for r in every if r.status == FAIL)
        skips = sum(1 for r in every if r.status == SKIP)
        passed = len(every) - failures - skips
        scope = f" across {len(batch)} files" if len(batch) > 1 else ""
        print(f"\n{passed} passed, {failures} failed, {skips} skipped{scope}")
        # Only when nothing ran at all. A run that measured something and found a
        # defect has 0 passes too, and telling that caller "nothing could be
        # measured" contradicts the failure printed directly above it.
        if not passed and not failures:
            print("nothing could be measured -- this is not a clean run")

    if worst:
        return worst
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
