"""Where this library's thresholds come from.

Every check here has a number in its signature, and until this script existed
most of those numbers were an anecdote written up in prose. This measures them:
take media with a known answer, inject a defect of known size, and count what
the check actually said.

    python bench/bench.py sync            # synthetic only: no network, ~1 min
    python bench/bench.py sync --corpus ~/renders --limit 40
    python bench/bench.py dead-air --corpus ~/renders
    python bench/bench.py captions

Output is markdown on stdout, plus a `recommended:` line. Redirect it into
docs/calibration.md and commit that, so the published numbers carry the version,
the date and the ffmpeg that produced them.

Two rules this script follows and that anyone extending it should keep:

**Abstention goes in the denominator.** These checks have three outcomes, not
two, so recall is `caught / everything injected` -- never `caught / (caught +
missed)`. Moving skips out of the denominator is how a tool flatters itself, and
`Skipped` is a first-class outcome in this library.

**A zero gets its upper bound printed next to it.** "0 false positives in 24
clips" is a fact; "0.0% false positive rate" from 24 clips is a fabrication. The
rule of three puts the 95% bound at 3/n, and printing it is one line.
"""

from __future__ import annotations

import argparse
import inspect
import shutil
import statistics
import subprocess
import sys
import tempfile
import warnings
from collections.abc import Callable, Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rendercheck import SilentFail, Skipped, __version__, media

CAUGHT, ABSTAINED, MISSED = "caught", "abstained", "missed"

_UNREACHABLE = 1e9
"""A threshold no measurement can exceed, for reading a value without judging it."""


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *args], check=True, capture_output=True
    )


def _verdict(check: Callable[..., object], *args: object, **kwargs: object) -> str:
    """Run one check and reduce it to caught / abstained / missed."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", Skipped)
        try:
            check(*args, **kwargs)
        except SilentFail:
            return CAUGHT
    if any(issubclass(w.category, Skipped) for w in caught):
        return ABSTAINED
    return MISSED


# --- fixtures ---------------------------------------------------------------

_BURSTS = [(1.0, 4.0), (7.0, 3.5), (13.0, 5.0), (20.0, 3.5), (26.0, 6.0), (34.0, 4.0)]
_LENGTH = 40.0


def _gate(bursts: list[tuple[float, float]] | None = None) -> str:
    return "+".join(
        f"between(t,{start:g},{start + run:g})" for start, run in (bursts or _BURSTS)
    )


def _talking_head(dest: Path) -> Path:
    """A synthetic presenter whose mouth and voice share one clock exactly.

    Ground truth is 0 by construction, which is the only tier that can say
    whether the calibration constant describes the estimator or merely describes
    HeyGen. The mouth alternates every frame while talking -- a box that is
    merely *present* for the burst gives two rectified impulses that cancel; see
    tests/test_lipsync.py, where that was measured.
    """
    if dest.exists():
        return dest
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=320x240:r=25:d={_LENGTH:g}",
        "-f",
        "lavfi",
        "-i",
        f"sine=f=300:r=48000:d={_LENGTH:g}",
        "-filter_complex",
        f"[0:v]drawbox=x=110:y=205:w=100:h=20:color=white:t=fill:"
        f"enable='eq(mod(n\\,2)\\,0)',"
        f"drawbox=x=110:y=140:w=100:h=60:color=white:t=fill:"
        f"enable='({_gate()})*eq(mod(n\\,2)\\,0)'[v];"
        f"[1:a]volume=0:enable='not({_gate()})',"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=out:st={_LENGTH - 0.4:g}:d=0.4[a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(dest),
    )
    return dest


def _narration(dest: Path) -> Path:
    """Speech-shaped audio with irregular gaps and a fade, no picture."""
    if dest.exists():
        return dest
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"sine=f=300:r=48000:d={_LENGTH:g}",
        "-af",
        f"volume=0:enable='not({_gate()})',loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"afade=t=out:st={_LENGTH - 0.4:g}:d=0.4",
        str(dest),
    )
    return dest


# --- defect injection -------------------------------------------------------


def inject_desync(src: Path, dest: Path, seconds: float) -> Path:
    """Delay or advance the sound against the picture, keeping the length.

    A *content* desync, with the samples actually rewritten. `-itsoffset` with
    `-c copy` does NOT do this: a positive offset is stored as an mp4 edit list,
    and a decoder reading the streams separately honours it, so the delay
    cancels and the injected defect silently isn't there. Measured on three
    clips -- +0.2 s and +0.5 s both read identical to baseline.
    """
    length = _duration(src)
    shift = (
        f"adelay={int(seconds * 1000)}|{int(seconds * 1000)}"
        if seconds > 0
        else f"atrim=start={-seconds:g},asetpts=PTS-STARTPTS"
    )
    _ffmpeg(
        "-i",
        str(src),
        "-af",
        shift,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-t",
        f"{length:g}",
        str(dest),
    )
    return dest


def inject_silence(src: Path, dest: Path, seconds: float) -> Path:
    """Mute a stretch in the middle, the shape of a dropped segment."""
    start = _duration(src) / 2 - seconds / 2
    _ffmpeg(
        "-i",
        str(src),
        "-af",
        f"volume=0:enable='between(t,{start:g},{start + seconds:g})'",
        "-c:a",
        "aac",
        str(dest),
    )
    return dest


def write_cues(dest: Path, offset: float = 0.0, scale: float = 1.0) -> Path:
    """Captions for `_BURSTS`, optionally mistimed by a known amount."""

    def stamp(at: float) -> str:
        return f"{int(at // 60):02d}:{at % 60:06.3f}"

    lines = ["WEBVTT", ""]
    for index, (start, run) in enumerate(_BURSTS, 1):
        begin = start * scale + offset
        lines += [
            str(index),
            f"{stamp(begin)} --> {stamp(begin + run * scale)}",
            f"line {index}",
            "",
        ]
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def _duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return float(out)


# --- reporting --------------------------------------------------------------


def _bound(zeros: int, total: int) -> str:
    """Rule of three: with 0 events in n trials the 95% bound is 3/n."""
    if zeros or not total:
        return ""
    return f" (95% upper bound {3 / total:.0%})"


def _curve(rows: list[tuple[float, dict[str, int]]], engaged: int) -> None:
    print("| injected | caught | abstained | missed | recall |")
    print("|---|---|---|---|---|")
    for magnitude, tally in rows:
        total = sum(tally.values()) or 1
        print(
            f"| {magnitude:g} | {tally[CAUGHT]} | {tally[ABSTAINED]} | "
            f"{tally[MISSED]} | {tally[CAUGHT] / total:.0%} |"
        )
    floor = next(
        (m for m, t in rows if t[CAUGHT] / (sum(t.values()) or 1) >= 0.5), None
    )
    print()
    print(
        f"Detection floor (recall first crosses 50%): "
        f"{f'{floor:g}' if floor is not None else 'never within the range tested'}"
    )
    print(f"Clips the check engaged on: {engaged}")


def _header(title: str, note: str) -> None:
    print(f"\n## {title}\n")
    print(f"{note}\n")


# --- studies ----------------------------------------------------------------


def study_sync(clips: list[Path], work: Path, offsets: Iterable[float]) -> None:
    from rendercheck import lipsync

    _header(
        "assert_lip_sync",
        "Ground truth by re-encoded audio delay (`adelay`/`atrim`), not "
        "`-itsoffset` -- see `inject_desync` for why that silently injects "
        "nothing.",
    )
    clean, offsets_seen = [], []
    for clip in clips:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", Skipped)
            try:
                # bias=0 to read the RAW offset, and the thresholds lifted out
                # of the way so the check cannot raise. Without that, every
                # clean clip whose raw reading exceeds the shipped max_offset
                # is thrown out of the very set being used to calibrate the
                # bias -- which truncates the sample from below and drags the
                # estimate toward zero. Measured when this script had that bug:
                # 6 of 40 clips "clean" at +0.227 s, against 14 of 40 at
                # +0.323 s once the thresholds were lifted.
                result = media.assert_lip_sync(
                    clip, bias=0.0, max_offset=_UNREACHABLE, max_drift=_UNREACHABLE
                )
            except SilentFail:  # pragma: no cover -- cannot raise with the above
                result = None
        if result is not None and not any(
            issubclass(w.category, Skipped) for w in caught
        ):
            clean.append(clip)
            offsets_seen.append(result)
    if not clean:
        print("No clip produced a distinct alignment. Nothing to calibrate.")
        return

    bias = statistics.mean(offsets_seen)
    spread = statistics.pstdev(offsets_seen) if len(offsets_seen) > 1 else 0.0
    print(
        f"Engaged on {len(clean)} of {len(clips)} clips. Raw offset "
        f"(bias not removed): mean {bias:+.3f}s, stdev {spread:.3f}s, "
        f"range {min(offsets_seen):+.3f}..{max(offsets_seen):+.3f}s.\n"
    )

    # Leave-one-out: the clean set defines zero AND measures false positives, so
    # each clip is judged against a zero computed from the others. Using the
    # same clips for both would be training on the test set, and the resulting
    # false-positive rate would mean nothing.
    if len(clean) < 3:
        print(
            f"False positives: not measurable from {len(clean)} clip(s) -- "
            f"leave-one-out needs a population to leave one out of. The "
            f"synthetic tier cannot produce this number; use `--corpus`.\n"
        )
    else:
        false = 0
        for index in range(len(clean)):
            others = [o for i, o in enumerate(offsets_seen) if i != index]
            if abs(offsets_seen[index] - statistics.mean(others)) > shipped(
                media.assert_lip_sync, "max_offset"
            ):
                false += 1
        print(
            f"False positives on the clean set, leave-one-out: {false}/{len(clean)}"
            f"{_bound(false, len(clean))}\n"
        )

    rows = []
    for magnitude in offsets:
        tally = {CAUGHT: 0, ABSTAINED: 0, MISSED: 0}
        for clip in clean:
            for sign in (1, -1):
                spoiled = work / f"sync_{clip.stem}_{sign * magnitude:+g}.mp4"
                inject_desync(clip, spoiled, sign * magnitude)
                tally[_verdict(media.assert_lip_sync, spoiled, bias=bias)] += 1
                spoiled.unlink(missing_ok=True)
        rows.append((magnitude, tally))
    _curve(rows, len(clean))
    print(
        f"\nrecommended: bias={bias:.2f}, max_offset={max(0.30, 3 * spread):.2f} "
        f"(n={len(clean)} engaged of {len(clips)}; stdev {spread:.3f}s; "
        f"min_ratio={lipsync.MIN_RATIO}, min_duty={lipsync.MIN_DUTY})"
    )


def shipped(check: Callable[..., object], name: str) -> float:
    """A shipped default, read from the signature rather than retyped here.

    Retyping it is how a benchmark ends up grading a threshold the library no
    longer uses.
    """
    return float(inspect.signature(check).parameters[name].default)


def study_dead_air(clips: list[Path], work: Path, lengths: Iterable[float]) -> None:
    _header(
        "assert_no_dead_air",
        "The useful number here is not the detection curve -- `silencedetect` "
        "resolves a gap exactly -- but how often *real* narration contains a "
        "natural pause long enough to trip the default.",
    )
    default = shipped(media.assert_no_dead_air, "max_silence")
    false = sum(
        1 for clip in clips if _verdict(media.assert_no_dead_air, clip) == CAUGHT
    )
    print(
        f"Clean corpus of {len(clips)}: {false} flagged at the shipped "
        f"max_silence={default:g}s{_bound(false, len(clips))}.\n"
    )
    rows = []
    for length in lengths:
        tally = {CAUGHT: 0, ABSTAINED: 0, MISSED: 0}
        for clip in clips:
            spoiled = work / f"gap_{clip.stem}_{length:g}{clip.suffix}"
            inject_silence(clip, spoiled, length)
            tally[_verdict(media.assert_no_dead_air, spoiled)] += 1
            spoiled.unlink(missing_ok=True)
        rows.append((length, tally))
    _curve(rows, len(clips))
    print(
        f"\nrecommended: max_silence={default:g} "
        f"(n={len(clips)}, {false} flagged clean)"
    )


def study_captions(work: Path, offsets: Iterable[float]) -> None:
    _header(
        "assert_captions_aligned",
        "Cue timings shifted by a known amount against fixed audio.",
    )
    audio = _narration(work / "narration.wav")
    default = shipped(media.assert_captions_aligned, "max_offset")
    aligned = write_cues(work / "aligned.vtt")
    # On a clean control the labels invert: caught is a false positive, missed
    # is the correct outcome. Say which, rather than printing the raw label.
    clean = _verdict(media.assert_captions_aligned, audio, aligned)
    print(
        "Correctly timed cues on clean audio: "
        + {
            CAUGHT: "**flagged -- a false positive**",
            ABSTAINED: "abstained (could not tell)",
            MISSED: "not flagged, which is correct",
        }[clean]
        + ".\n"
    )
    rows = []
    for magnitude in offsets:
        tally = {CAUGHT: 0, ABSTAINED: 0, MISSED: 0}
        for sign in (1, -1):
            cues = write_cues(
                work / f"cues{sign * magnitude:+g}.vtt", offset=sign * magnitude
            )
            tally[_verdict(media.assert_captions_aligned, audio, cues)] += 1
        rows.append((magnitude, tally))
    _curve(rows, 1)
    print(f"\nrecommended: max_offset={default:g} (synthetic tier, n=1 clip x 2 signs)")


# --- entry point ------------------------------------------------------------


def _argv_without_note(argv: list[str]) -> list[str]:
    """The command, minus `--note` -- its text is already its own paragraph."""
    out, skip = [], False
    for item in argv:
        if skip:
            skip = False
            continue
        if item == "--note":
            skip = True
            continue
        if item.startswith("--note="):
            continue
        out.append(item)
    return out


def _corpus(root: Path | None, limit: int, synthetic: Path) -> list[Path]:
    if root is None:
        return [_talking_head(synthetic)]
    found = sorted(
        path
        for path in root.rglob("*")
        if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
    )
    return found[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.py",
        description="Measure where this library's thresholds come from.",
    )
    parser.add_argument("study", choices=["sync", "dead-air", "captions"])
    parser.add_argument(
        "--corpus",
        type=Path,
        help="directory of real media; omit for the synthetic tier",
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="clips to use from --corpus"
    )
    parser.add_argument(
        "--note",
        default="",
        help="one line describing the corpus, echoed into the header. Private "
        "media cannot be shipped, so the reader has to be told what it was.",
    )
    args = parser.parse_args(argv)

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("ffmpeg and ffprobe are required", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="rendercheck-bench-"))
    ffmpeg_version = subprocess.run(
        ["ffmpeg", "-version"], capture_output=True, text=True, check=True
    ).stdout.splitlines()[0]
    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent,
    ).stdout.strip()

    print("# Calibration\n")
    print(
        "Generated by `bench/bench.py`. Do not edit by hand -- rerun it.\n\n"
        f"- rendercheck {__version__} at `{revision or 'unknown'}`\n"
        f"- {ffmpeg_version}\n"
        f"- argv: `{' '.join(_argv_without_note(sys.argv[1:]))}`\n"
    )
    if args.note:
        print(f"**Corpus.** {args.note}\n")
    if args.corpus is None:
        print(
            "**Synthetic tier.** Ground truth is exact by construction and the "
            "run needs no network, but one synthesised presenter is not a "
            "population: it says whether the estimator is self-consistent, not "
            "what it does on real renders. Point `--corpus` at real media for "
            "that.\n"
        )

    try:
        if args.study == "sync":
            clips = _corpus(args.corpus, args.limit, work / "talking.mp4")
            study_sync(clips, work, [0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0])
        elif args.study == "dead-air":
            clips = _corpus(args.corpus, args.limit, work / "talking.mp4")
            study_dead_air(clips, work, [1.0, 2.0, 2.5, 3.0, 3.5, 4.0, 6.0])
        else:
            study_captions(work, [0.25, 0.5, 0.75, 1.0, 2.0])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
