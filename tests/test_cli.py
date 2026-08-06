"""CLI behaviour, including the `--json` shape other pipelines parse.

Framework-free like the rest: stdout is captured with `redirect_stdout` rather
than a pytest fixture, so `python tests/test_cli.py` works on its own.
"""

import argparse
import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rendercheck.cli import main

# Absolute, not relative: this file must also run as a plain script.
from tests.test_checks import MINUTE, NOAUDIO, TONE


def run(*argv: str) -> tuple[int, str]:
    # stderr lands in the same buffer: usage errors are reported there, and a
    # test asserting on them should not care which stream carried the text.
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        code = main(["check", *argv])
    return code, buffer.getvalue()


def test_a_clean_file_exits_zero():
    code, out = run(str(TONE), "--expect-seconds", "10")
    assert code == 0, out
    assert "0 failed" in out, out


def test_a_defective_file_exits_one():
    # No audio track at all -- the regression that named the library.
    code, out = run(str(NOAUDIO))
    assert code == 1, out
    assert "no audio stream" in out, out


def test_json_keeps_the_keys_other_pipelines_parse():
    # This shape is a contract: the course-pipeline bridge reads `status` and
    # `check` off each result. Renaming either silently breaks that consumer.
    _, out = run(str(TONE), "--json")
    report = json.loads(out)
    assert set(report) == {"file", "results", "failed", "skipped"}
    for result in report["results"]:
        assert set(result) == {"status", "check", "detail"}
        assert result["status"] in {"PASS", "FAIL", "SKIP"}


def test_json_exit_code_and_failure_count_agree():
    code, out = run(str(NOAUDIO), "--json")
    report = json.loads(out)
    assert report["failed"] > 0
    assert code == 1


def test_a_skipped_check_is_declared_not_hidden():
    # An empty run must never read as a clean one.
    _, out = run(str(TONE), "--json")
    report = json.loads(out)
    pace = next(r for r in report["results"] if r["check"] == "pace")
    assert pace["status"] == "SKIP"
    assert "no --script given" in pace["detail"]
    assert report["skipped"] >= 1


def test_pace_runs_when_a_script_is_supplied():
    _, out = run(str(MINUTE), "--script", " ".join(["word"] * 200), "--json")
    pace = next(r for r in json.loads(out)["results"] if r["check"] == "pace")
    assert pace["status"] == "PASS"
    assert "WPM" in pace["detail"]


def test_an_image_without_a_rubric_says_why_it_skipped():
    from tests.test_checks import _slide

    _, out = run(str(_slide()), "--json")
    results = json.loads(out)["results"]
    reasons = {r["check"]: r for r in results}
    assert reasons["looks ok"]["status"] == "SKIP"
    assert "no --rubric" in reasons["looks ok"]["detail"]


def test_a_still_is_measured_without_a_rubric_or_a_key():
    # The whole point of the blank check: before it, every image needed an API
    # key and a rubric before this tool would say anything at all about it, so
    # `rendercheck check slide.png` was one SKIP and a red exit code.
    from tests.test_checks import _slide

    code, out = run(str(_slide()), "--json")
    results = json.loads(out)["results"]
    blank = next(r for r in results if r["check"] == "blank")
    assert blank["status"] == "PASS", out
    assert code == 0, out


def test_a_blank_still_is_a_defect_not_a_pass():
    # The failure image generators actually produce: right dimensions, valid
    # PNG, no error, nothing on it.
    from tests.test_checks import _ffmpeg

    with tempfile.TemporaryDirectory() as scratch:
        empty = Path(scratch) / "empty.png"
        _ffmpeg(
            "-f", "lavfi", "-i", "color=c=black:s=320x180", "-frames:v", "1", str(empty)
        )
        code, out = run(str(empty))
    assert code == 1, out
    assert "blank canvas" in out, out


# --- the exit-code contract -------------------------------------------------
# Every one of these returned 0 before. A green build is a claim, and each of
# these was the tool making that claim without having looked at anything.


def test_a_missing_file_is_a_usage_error_not_a_pass():
    code, out = run("/tmp/rendercheck-definitely-not-here.mp4")
    assert code == 2, out
    assert "no such file" in out, out


def test_a_run_that_measured_nothing_is_not_a_pass():
    # Asserted against `_verdict` directly rather than through a file. This used
    # to be reachable end to end with an image and no --rubric, but a still now
    # always has the blank check to run, so no ordinary input reaches it. The
    # rule it protects is unchanged and is the one the library is named after:
    # every check skipping means nothing was looked at, and green would be a lie.
    from rendercheck.cli import EXIT_FAILED, EXIT_OK, Result, _verdict

    nothing = [Result("SKIP", "loudness", "ffmpeg is not on PATH")]
    assert _verdict(nothing, strict=False) == EXIT_FAILED
    assert _verdict([Result("PASS", "loudness", "-16.0 LUFS")], strict=False) == EXIT_OK


def test_a_defect_is_never_reported_as_nothing_measured():
    # A blank still fails its one runnable check and skips the other, so the run
    # has no passes -- but it was emphatically not unmeasurable. Printing
    # "nothing could be measured" there contradicts the failure printed directly
    # above it, which is what happened until the count was split from the reason.
    from tests.test_checks import _ffmpeg

    with tempfile.TemporaryDirectory() as scratch:
        empty = Path(scratch) / "blank.png"
        _ffmpeg(
            "-f", "lavfi", "-i", "color=c=black:s=320x180", "-frames:v", "1", str(empty)
        )
        code, out = run(str(empty))
    assert code == 1, out
    assert "0 passed, 1 failed" in out, out
    assert "nothing could be measured" not in out, out


# --- the config-file type guard ---------------------------------------------
#
# `parser.set_defaults()` bypasses `type=`, `choices=` and `nargs=` entirely, so
# a value arriving from a TOML file gets none of the checking a typed flag gets.
# This layer puts it back. It is the guard the docs spend a paragraph on and it
# had no test at all, which is a poor combination for code whose whole job is
# refusing to let a wrong value through quietly.


def _action(flag: str) -> argparse.Action:
    from rendercheck.cli import _parser

    return next(a for a in _parser()._actions if flag in a.option_strings)


def test_a_scalar_where_a_roster_belongs_is_refused():
    # The damaging one, because a string is still iterable: `known_names =
    # "Karl"` becomes the roster {k, a, r, l}, which matches nobody, so the
    # speaker check prints PASS forever rather than refusing to run.
    from rendercheck.cli import _as_argparse_would

    try:
        _as_argparse_would(_action("--known-names"), "Karl")
    except TypeError as exc:
        assert "expected a list" in str(exc), exc
    else:
        raise AssertionError("a bare string was accepted as a roster")

    assert _as_argparse_would(_action("--known-names"), ["Alex", "Jordan"]) == [
        "Alex",
        "Jordan",
    ]


def test_a_wrong_typed_threshold_is_refused():
    from rendercheck.cli import _as_argparse_would

    for flag, value, expected in (
        ("--max-wpm", "fast", "expected a number"),
        ("--max-clipped", 1.5, None),  # a float where a count belongs: truncated
        ("--max-clipped", True, "expected a whole number"),
        ("--strict", "yes", "expected true or false"),
        ("--target-lufs", [1, 2], "expected a single value"),
    ):
        try:
            got = _as_argparse_would(_action(flag), value)
        except (TypeError, ValueError) as exc:
            assert expected and expected in str(exc), f"{flag}={value!r}: {exc}"
        else:
            assert expected is None, f"{flag}={value!r} was accepted as {got!r}"


def test_an_invalid_choice_is_refused_with_the_alternatives():
    from rendercheck.cli import _as_argparse_would

    try:
        _as_argparse_would(_action("--preset"), "youtub")
    except ValueError as exc:
        assert "is not one of" in str(exc) and "youtube" in str(exc), exc
    else:
        raise AssertionError("an unknown preset was accepted")


def test_a_bad_config_value_is_reported_and_dropped_not_obeyed():
    # End to end with a real file on disk: the bad key is dropped and named on
    # stderr, the good one survives, and the run carries on with its default.
    from rendercheck import config
    from rendercheck.cli import _from_config, _parser

    if not config.HAVE_TOML:
        return  # 3.10 has no tomllib; the loader announces that instead

    noise = io.StringIO()
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / "rendercheck.toml").write_text(
            'known_names = "Karl"\nmax_wpm = 200.0\n'
        )
        # `start=` rather than chdir: the loader takes one, and a test that
        # changes the process's working directory races every other test in the
        # file the moment anything runs in parallel.
        with redirect_stderr(noise):
            settings = _from_config(_parser(), start=root)

    assert "known_names" not in settings, settings
    assert settings.get("max_wpm") == 200.0, settings
    assert "ignoring known_names" in noise.getvalue(), noise.getvalue()


def test_strict_rejects_a_partial_run_that_normal_mode_allows():
    clean, _ = run(str(TONE))
    strict, out = run(str(TONE), "--strict")
    assert clean == 0
    assert strict == 1, out


def test_presenter_without_a_roster_refuses_instead_of_passing():
    # With a roster of just the assigned presenter the check is structurally
    # incapable of firing, so it used to print PASS on a script naming someone
    # else entirely.
    code, out = run(str(TONE), "--script", "Hi, I'm Jordan.", "--presenter", "Alex")
    speaker = [line for line in out.splitlines() if "speaker" in line]
    assert speaker and "SKIP" in speaker[0], out
    assert "--known-names" in speaker[0], out
    assert code != 0, out


def test_a_roster_makes_the_speaker_check_fire():
    _, out = run(
        str(TONE),
        "--script",
        "Hi, I'm Jordan.",
        "--presenter",
        "Alex",
        "--known-names",
        "Alex",
        "Jordan",
    )
    speaker = [line for line in out.splitlines() if "speaker" in line]
    assert speaker and "FAIL" in speaker[0], out


def test_a_typod_script_path_is_a_usage_error():
    code, out = run(str(TONE), "--script", "episode-12.vtt")
    assert code == 2, out
    assert "no such script file" in out, out


def test_demo_fires_real_checks_on_media_it_generates():
    # The demo is the answer to "I cannot try this without a broken file". If it
    # ever prints something that is not a genuine measurement, it is worse than
    # having no demo at all.
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        code = main(["demo"])
    out = buffer.getvalue()
    assert code == 0, out
    assert "300 WPM exceeds 245" in out, out
    assert "has no audio stream at all" in out, out
    assert out.count("FAIL") >= 5, out


def test_demo_leaves_the_working_directory_where_it_found_it():
    before = Path.cwd()
    with redirect_stdout(io.StringIO()):
        main(["demo"])
    assert Path.cwd() == before


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
