"""Guards on the copies of library data that live outside the library.

The playground reimplements a subset of the checks in JavaScript, because it
runs ffmpeg.wasm in a browser rather than shelling out. That is a deliberate
duplicate and it is not going away -- but a duplicate nobody diffs is a
duplicate that drifts, and this one already had:

  * a preset table missing the true-peak column entirely, so choosing "YouTube"
    on the page did not switch on the check the README says it does; and
  * the freeze-to-EOF bug, fixed on the Python side in 0.3.1 and left live in
    the browser, where it reported PASS on a video frozen to the end of file.

Neither was caught by anything. These tests are the cheapest thing that would
have caught the first, and they run in CI on every push.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rendercheck import presets

ROOT = Path(__file__).resolve().parent.parent
PLAYGROUND = ROOT / "docs" / "playground" / "index.html"


def _js_presets() -> dict[str, tuple[float, float, float | None]]:
    """The `PRESETS` object out of the page, as Python.

    Parsed rather than executed: pulling one well-known object literal out with
    a regex is less machinery than a JS engine, and if the shape ever changes
    enough to defeat this, that is itself worth failing on.
    """
    source = PLAYGROUND.read_text(encoding="utf-8")
    block = re.search(r"const PRESETS = \{(.*?)\n      \};", source, re.S)
    assert block, "could not find the PRESETS table in the playground"

    found = {}
    for name, body in re.findall(r"(\w+): \[([^\]]*)\]", block.group(1)):
        # JSON rather than eval: `null` and numbers both parse, and nothing in
        # the file gets executed.
        target, tol, peak, _note = json.loads(f"[{body}]")
        found[name] = (target, tol, peak)
    return found


def test_the_playground_preset_table_matches_the_library():
    # The page says in a comment that this is "kept in sync by hand". This is
    # what makes that claim checkable.
    mine = _js_presets()
    theirs = {
        name: (row.target_lufs, row.tol, row.max_true_peak)
        for name, row in presets.PRESETS.items()
    }
    assert mine == theirs, (
        "docs/playground/index.html has drifted from rendercheck/presets.py.\n"
        f"  page:    {sorted(mine.items())}\n"
        f"  library: {sorted(theirs.items())}"
    )


def test_the_playground_terminates_a_freeze_that_runs_to_the_end_of_file():
    # freezedetect prints `freeze_start` with no `freeze_duration` when the
    # picture is still frozen at EOF. Requiring both -- which the page did --
    # drops the event and reports the defect as a pass.
    source = PLAYGROUND.read_text(encoding="utf-8")
    assert "fd.length < fs.length" in source, (
        "the playground no longer terminates an unfinished freeze; a video "
        "frozen to the end of the file will report PASS"
    )


def test_the_playground_declares_every_check_it_does_not_run():
    # An incomplete run must not look like a whole one. Every check the library
    # exposes has to appear on the page: measured, or explicitly skipped with a
    # reason. This is the same rule the CLI's exit codes enforce.
    source = PLAYGROUND.read_text(encoding="utf-8")
    named = set(re.findall(r'"(?:PASS|FAIL|SKIP)", "([a-z ]+)"', source))
    named |= set(re.findall(r'^\s*\["([a-z ]+)", "', source, re.M))

    expected = {
        "has sound",
        "loudness",
        "loudness range",
        "true peak",
        "dead air",
        "truncation",
        "clipping",
        "duration",
        "captions",
        "streams",
        "format",
        "audio format",
        "black frames",
        "frozen",
        "blank",
        "pace",
        "speaker",
        "looks ok",
    }
    missing = expected - named
    assert not missing, (
        f"the playground neither runs nor declares: {sorted(missing)}. "
        f"A check that is simply absent reads as a check that passed."
    )


if __name__ == "__main__":
    failures = 0
    for name, test in sorted(dict(globals()).items()):
        if name.startswith("test_") and callable(test):
            try:
                test()
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("docs ok" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)
