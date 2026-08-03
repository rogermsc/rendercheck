"""One runnable check per piece of non-trivial logic.

Fixtures are generated with ffmpeg rather than committed as binaries, so the
tests exercise the real measurement path instead of a mock of it.

    python tests/test_checks.py     # no pytest needed
    pytest tests/ -q                # also works
"""

import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silentfail import (  # noqa: E402
    SilentFail,
    Skipped,
    assert_duration,
    assert_loudness,
    assert_no_dead_air,
    assert_pace,
    assert_speaker,
)
from silentfail.text import read_script  # noqa: E402

FIXTURES = Path(tempfile.gettempdir()) / "silentfail-fixtures"


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def build_fixtures():
    FIXTURES.mkdir(exist_ok=True)
    tone, quiet, silence, minute, bogus = (
        FIXTURES / n for n in ("tone.wav", "quiet.wav", "silence.wav", "minute.wav", "bogus.mp4")
    )
    if not tone.exists():
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", str(tone))
    if not quiet.exists():
        # -34 LUFS: the real incident level, cut against avatar footage at -13.
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                "-af", "loudnorm=I=-34:TP=-1.5:LRA=11", str(quiet))
    if not silence.exists():
        _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "10", str(silence))
    if not minute.exists():
        _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "60", str(minute))
    bogus.write_text("this is not a video")  # for the fail-open check
    return tone, quiet, silence, minute, bogus


TONE, QUIET, SILENCE, MINUTE, BOGUS = build_fixtures()


def raises(check, *args, **kwargs) -> str:
    """Assert `check` raises SilentFail, and hand back the message."""
    try:
        check(*args, **kwargs)
    except SilentFail as exc:
        return str(exc)
    raise AssertionError(f"{check.__name__} should have failed on {args!r}")


# --- pace: the machine-gun defect ------------------------------------------

def test_pace_flags_machine_gun():
    # 300 words over exactly 60s = 300 WPM.
    message = raises(assert_pace, MINUTE, " ".join(["word"] * 300))
    assert "300 WPM" in message and "exceeds 245" in message, message


def test_pace_accepts_a_normal_delivery():
    assert round(assert_pace(MINUTE, " ".join(["word"] * 200))) == 200


def test_pace_flags_a_drag():
    assert "below 110" in raises(assert_pace, MINUTE, " ".join(["word"] * 50))


def test_pace_reads_a_vtt_and_ignores_its_cues():
    vtt = FIXTURES / "script.vtt"
    vtt.write_text(
        "WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\n<v Alex>two words\n\n"
        "2\n00:00:02.000 --> 00:00:04.000\nthree more words\n"
    )
    # Cue numbers, timestamps, WEBVTT and <v> must not be counted as narration.
    assert len(read_script(vtt).split()) == 5


# --- loudness: the -34-against-a--13 defect ---------------------------------

def test_loudness_flags_an_inaudible_track():
    assert "quieter than" in raises(assert_loudness, QUIET)


def test_loudness_flags_digital_silence():
    assert "digital silence" in raises(assert_loudness, SILENCE)


def test_loudness_accepts_a_normalised_track():
    assert abs(assert_loudness(TONE, tol=3.0) - (-16.0)) <= 3.0


# --- duration: the cached-truncated-segment defect --------------------------

def test_duration_distinguishes_truncation_from_a_short_take():
    truncated = raises(assert_duration, TONE, 30.0)
    assert "silent encode failure" in truncated, truncated
    drift = raises(assert_duration, TONE, 12.0)
    assert "drift out of sync" in drift, drift


def test_duration_accepts_the_expected_length():
    assert round(assert_duration(TONE, 10.0)) == 10


# --- dead air: the silent-compositing-failure defect ------------------------

def test_dead_air_flags_a_hole():
    assert "of silence starting at 0:00" in raises(assert_no_dead_air, SILENCE)


def test_dead_air_accepts_continuous_audio():
    assert assert_no_dead_air(TONE) == 0.0


# --- speaker: the wrong-face-for-a-whole-module defect ----------------------

ROSTER = ["Alex", "Jordan", "Sam"]


def test_speaker_flags_the_wrong_presenter():
    message = raises(assert_speaker, "Hi, I'm Jordan, your instructor.", "Alex", ROSTER)
    assert "Jordan" in message and "Alex is assigned" in message, message


def test_speaker_ignores_characters_who_are_not_on_the_roster():
    # The whole point of the roster: a scenario character must not trip this.
    assert_speaker("I'm Rosa, a senior nurse, and I use AI daily.", "Alex", ROSTER)


def test_speaker_accepts_the_right_presenter():
    assert_speaker("My name is Alex and I'll be walking you through this.", "Alex", ROSTER)


def test_speaker_requires_a_roster():
    try:
        assert_speaker("I'm Jordan.", "Alex", [])
    except ValueError as exc:
        assert "roster" in str(exc)
    else:
        raise AssertionError("an empty roster must be rejected, not silently trusted")


# --- looks_ok: severity handling, with a stub in place of the model ---------
# The model's judgement is not ours to test; how we act on it is.

def _stub_client(findings, stop_reason="end_turn"):
    import json
    import types

    response = types.SimpleNamespace(
        stop_reason=stop_reason,
        content=[types.SimpleNamespace(type="text", text=json.dumps({"findings": findings}))],
    )
    return types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kwargs: response)
    )


def _slide():
    png = FIXTURES / "slide.png"
    if not png.exists():
        _ffmpeg("-f", "lavfi", "-i", "color=c=white:s=320x180", "-frames:v", "1", str(png))
    return png


def test_looks_ok_passes_a_clean_image():
    from silentfail import looks_ok

    assert looks_ok(_slide(), ["title fits"], client=_stub_client([])) == []


def test_looks_ok_warns_on_minor_but_does_not_raise():
    from silentfail import looks_ok

    minor = [{"severity": "minor", "rubric_item": "title fits", "note": "1px off"}]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        looks_ok(_slide(), ["title fits"], client=_stub_client(minor))
    assert any(issubclass(w.category, Skipped) for w in caught)


def test_looks_ok_leads_with_the_critical_finding():
    from silentfail import looks_ok

    both = [
        {"severity": "major", "rubric_item": "a", "note": "overflows"},
        {"severity": "critical", "rubric_item": "b", "note": "text cut off"},
    ]
    message = raises(looks_ok, _slide(), ["a", "b"], client=_stub_client(both))
    assert message.startswith("[critical]") and "+1 more" in message, message


def test_looks_ok_fails_open_on_a_refusal():
    from silentfail import looks_ok

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert looks_ok(_slide(), ["x"], client=_stub_client([], "refusal")) is None
    assert any(issubclass(w.category, Skipped) for w in caught)


def test_looks_ok_rejects_an_empty_rubric():
    from silentfail import looks_ok

    try:
        looks_ok(_slide(), [])
    except ValueError as exc:
        assert "rubric is empty" in str(exc)
    else:
        raise AssertionError("an empty rubric checks nothing and must be rejected")


# --- fail open: the checker never blocks on its own breakage -----------------

def test_unmeasurable_media_warns_instead_of_failing():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert assert_loudness(BOGUS) is None
    assert any(issubclass(w.category, Skipped) for w in caught), "should have warned"


def test_a_missing_file_still_raises_loudly():
    try:
        assert_loudness(FIXTURES / "does-not-exist.wav")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("a typo'd path must raise, not fail open")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except Exception as exc:  # noqa: BLE001 -- this is the test reporter
            failed += 1
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
