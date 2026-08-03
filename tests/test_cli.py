"""CLI behaviour, including the `--json` shape other pipelines parse.

Framework-free like the rest: stdout is captured with `redirect_stdout` rather
than a pytest fixture, so `python tests/test_cli.py` works on its own.
"""

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silentfail.cli import main

# Absolute, not relative: this file must also run as a plain script.
from tests.test_checks import MINUTE, NOAUDIO, TONE


def run(*argv: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
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
    assert [r["check"] for r in results] == ["looks ok"]
    assert results[0]["status"] == "SKIP"
    assert "no --rubric" in results[0]["detail"]


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
