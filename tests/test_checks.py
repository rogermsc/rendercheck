"""One runnable check per piece of non-trivial logic.

Fixtures are generated with ffmpeg rather than committed as binaries, so the
tests exercise the real measurement path instead of a mock of it.

    python tests/test_checks.py     # no pytest needed
    pytest tests/ -q                # also works
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rendercheck import (
    SilentFail,
    Skipped,
    assert_duration,
    assert_has_sound,
    assert_loudness,
    assert_no_black_frames,
    assert_no_clipping,
    assert_no_dead_air,
    assert_no_truncation,
    assert_pace,
    assert_speaker,
)
from rendercheck.text import read_script

FIXTURES = Path(tempfile.gettempdir()) / "rendercheck-fixtures"


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def build_fixtures():
    FIXTURES.mkdir(exist_ok=True)
    tone, quiet, silence, minute, bogus, noaudio = (
        FIXTURES / n
        for n in (
            "tone.wav",
            "quiet.wav",
            "silence.wav",
            "minute.wav",
            "bogus.mp4",
            "noaudio.mp4",
        )
    )
    if not noaudio.exists():
        # Video with no audio track at all — not a silent track, no track.
        _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=320x240", "-t", "5", str(noaudio))
    if not tone.exists():
        # Fades out over its last 0.4s. This is the "clean audio" fixture, and
        # audio that stops dead at full level is not clean -- it is what a
        # truncated render looks like, and assert_no_truncation says so.
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=out:st=9.6:d=0.4",
            str(tone),
        )
    if not quiet.exists():
        # -34 LUFS: the real incident level, cut against avatar footage at -13.
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-af",
            "loudnorm=I=-34:TP=-1.5:LRA=11",
            str(quiet),
        )
    if not silence.exists():
        _ffmpeg(
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "10", str(silence)
        )
    if not minute.exists():
        _ffmpeg(
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "60", str(minute)
        )
    bogus.write_text("this is not a video")  # for the fail-open check
    return tone, quiet, silence, minute, bogus, noaudio


if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    # Fixtures are generated, not committed. Without ffmpeg this module used to
    # die during collection with a bare FileNotFoundError; SkipTest is stdlib and
    # pytest reads it as "skip the module", which is what a contributor deserves.
    raise unittest.SkipTest("fixtures need ffmpeg and ffprobe on PATH")

TONE, QUIET, SILENCE, MINUTE, BOGUS, NOAUDIO = build_fixtures()


def measured(value: float | None) -> float:
    """Assert the check actually measured something, and narrow the type.

    A check that skips returns None. Without this, `round(None)` would be the
    only signal — and a silently-skipped check masquerading as a pass is the
    exact failure mode this library exists to prevent, so the tests refuse it.
    """
    assert value is not None, "check skipped when it should have measured"
    return value


def raises(check: Callable[..., object], *args: object, **kwargs: object) -> str:
    """Assert `check` raises SilentFail, and hand back the message."""
    try:
        check(*args, **kwargs)
    except SilentFail as exc:
        return str(exc)
    name = getattr(check, "__name__", repr(check))
    raise AssertionError(f"{name} should have failed on {args!r}")


# --- pace: the machine-gun defect ------------------------------------------


def test_pace_flags_machine_gun():
    # 300 words over exactly 60s = 300 WPM.
    message = raises(assert_pace, MINUTE, " ".join(["word"] * 300))
    assert "300 WPM" in message and "exceeds 245" in message, message


def test_pace_accepts_a_normal_delivery():
    assert round(measured(assert_pace(MINUTE, " ".join(["word"] * 200)))) == 200


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
    assert abs(measured(assert_loudness(TONE, tol=3.0)) - (-16.0)) <= 3.0


# --- duration: the cached-truncated-segment defect --------------------------


def test_duration_distinguishes_truncation_from_a_short_take():
    truncated = raises(assert_duration, TONE, 30.0)
    assert "silent encode failure" in truncated, truncated
    drift = raises(assert_duration, TONE, 12.0)
    assert "drift out of sync" in drift, drift


def test_duration_accepts_the_expected_length():
    assert round(measured(assert_duration(TONE, 10.0))) == 10


# --- dead air: the silent-compositing-failure defect ------------------------


def test_dead_air_flags_a_hole():
    assert "of silence starting at 0:00" in raises(assert_no_dead_air, SILENCE)


def test_dead_air_accepts_continuous_audio():
    assert assert_no_dead_air(TONE) == 0.0


def test_dead_air_is_found_in_quiet_audio_too():
    """Regression: loudnorm used to run before silencedetect in the chain.

    It is a filter, not just a meter, so the gap detector saw *normalised*
    audio -- a quiet file was boosted on the way through and its near-silent
    stretches lifted above the threshold. Measured on this fixture: zero gaps
    found the old way, one the new way. Quiet material is precisely where this
    defect lives, so the miss was aimed at the worst possible population.
    """
    quiet_gap = FIXTURES / "quiet-gap.wav"
    if not quiet_gap.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-filter_complex",
            "[0]volume=-40dB[a];[1]volume=-70dB[b];[a][b]concat=n=2:v=0:a=1",
            str(quiet_gap),
        )
    assert "of silence starting at" in raises(assert_no_dead_air, quiet_gap)


# --- speaker: the wrong-face-for-a-whole-module defect ----------------------

ROSTER = ["Alex", "Jordan", "Sam"]


def test_speaker_flags_the_wrong_presenter():
    message = raises(assert_speaker, "Hi, I'm Jordan, your instructor.", "Alex", ROSTER)
    assert "Jordan" in message and "Alex is assigned" in message, message


def test_speaker_ignores_characters_who_are_not_on_the_roster():
    # The whole point of the roster: a scenario character must not trip this.
    assert_speaker("I'm Rosa, a senior nurse, and I use AI daily.", "Alex", ROSTER)


def test_speaker_accepts_the_right_presenter():
    assert_speaker(
        "My name is Alex and I'll be walking you through this.", "Alex", ROSTER
    )


def test_speaker_requires_a_roster():
    try:
        assert_speaker("I'm Jordan.", "Alex", [])
    except ValueError as exc:
        assert "roster" in str(exc)
    else:
        raise AssertionError("an empty roster must be rejected, not silently trusted")


# --- the wider set: truncation, clipping, black, frozen --------------------


def _cutoff() -> Path:
    """Audio that stops dead at full level, with no decay and no trailing gap."""
    path = FIXTURES / "cutoff.wav"
    if not path.exists():
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=5", str(path))
    return path


def _clipped() -> Path:
    path = FIXTURES / "clipped.wav"
    if not path.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-af",
            "volume=20dB",
            str(path),
        )
    return path


def _blackvideo() -> Path:
    """Moving picture, then three seconds of nothing."""
    path = FIXTURES / "goesblack.mp4"
    if not path.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=3:r=10",
            "-filter_complex",
            "[0][1]concat=n=2:v=1:a=0",
            "-pix_fmt",
            "yuv420p",
            str(path),
        )
    return path


def test_truncation_flags_audio_that_stops_at_full_level():
    assert "was cut rather than finished" in raises(assert_no_truncation, _cutoff())


def test_truncation_accepts_audio_that_was_allowed_to_finish():
    # Measured against the file's own average, so this holds for quiet content
    # as well as loud -- TONE fades out over its last 0.4s.
    assert measured(assert_no_truncation(TONE)) >= 6.0


def test_clipping_flags_a_flattened_waveform():
    assert "pinned at 0 dBFS" in raises(assert_no_clipping, _clipped())


def test_clipping_accepts_a_clean_mix():
    assert assert_no_clipping(TONE) == 0


def test_black_frames_flag_a_render_that_stopped_producing_picture():
    assert "solid black" in raises(assert_no_black_frames, _blackvideo())


def test_video_checks_skip_a_file_with_no_picture_rather_than_passing_it():
    # The lesson from the missing-audio-track regression, applied to video:
    # blackdetect on a .wav reports nothing, and nothing must not read as clean.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", Skipped)
        assert assert_no_black_frames(TONE) is None
    assert any("no video stream" in str(w.message) for w in caught), caught


def test_a_missing_audio_track_is_not_reported_as_mere_quiet():
    assert "no audio stream at all" in raises(assert_has_sound, NOAUDIO)


def test_has_sound_flags_an_all_zero_track():
    assert "every sample in it is zero" in raises(assert_has_sound, SILENCE)


def test_a_typod_script_path_is_not_read_as_narration():
    # It used to fall through to "this is transcript text", making a bad path
    # one word of narration and reporting "1 WPM is below 110" -- a confident,
    # wrong verdict about the audio for a mistake in the *script* argument.
    try:
        read_script("episode-12.vtt")
    except FileNotFoundError as exc:
        assert "no such script file" in str(exc)
    else:
        raise AssertionError("a missing .vtt must raise, not become narration")


def test_real_narration_is_still_read_as_text():
    assert read_script("Hi, I'm Jordan, and today we cover pricing.").startswith("Hi")


# --- looks_ok: severity handling, with a stub in place of the model ---------
# The model's judgement is not ours to test; how we act on it is.


def _stub_client(findings, stop_reason="end_turn"):
    import json
    import types

    response = types.SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            types.SimpleNamespace(type="text", text=json.dumps({"findings": findings}))
        ],
    )
    return types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kwargs: response)
    )


def _slide():
    png = FIXTURES / "slide.png"
    if not png.exists():
        _ffmpeg(
            "-f", "lavfi", "-i", "color=c=white:s=320x180", "-frames:v", "1", str(png)
        )
    return png


def test_looks_ok_passes_a_clean_image():
    from rendercheck import looks_ok

    assert looks_ok(_slide(), ["title fits"], client=_stub_client([])) == []


def test_looks_ok_warns_on_minor_but_does_not_raise():
    from rendercheck import looks_ok

    minor = [{"severity": "minor", "rubric_item": "title fits", "note": "1px off"}]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        looks_ok(_slide(), ["title fits"], client=_stub_client(minor))
    assert any(issubclass(w.category, Skipped) for w in caught)


def test_looks_ok_leads_with_the_critical_finding():
    from rendercheck import looks_ok

    both = [
        {"severity": "major", "rubric_item": "a", "note": "overflows"},
        {"severity": "critical", "rubric_item": "b", "note": "text cut off"},
    ]
    message = raises(looks_ok, _slide(), ["a", "b"], client=_stub_client(both))
    assert message.startswith("[critical]") and "+1 more" in message, message


def test_looks_ok_fails_open_on_a_refusal():
    from rendercheck import looks_ok

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert looks_ok(_slide(), ["x"], client=_stub_client([], "refusal")) is None
    assert any(issubclass(w.category, Skipped) for w in caught)


def test_looks_ok_rejects_an_empty_rubric():
    from rendercheck import looks_ok

    try:
        looks_ok(_slide(), [])
    except ValueError as exc:
        assert "rubric is empty" in str(exc)
    else:
        raise AssertionError("an empty rubric checks nothing and must be rejected")


# --- the regression that named the library ----------------------------------
# v0.1.0 returned PASS for a file with no audio track: silencedetect reports
# nothing when there is nothing to analyse, and that read as "no silence found".
# A silent failure inside rendercheck. It must never come back.


def test_a_missing_audio_track_is_a_failure_not_a_pass():
    for check in (assert_no_dead_air, assert_loudness):
        message = raises(check, NOAUDIO)
        assert "no audio stream" in message, f"{check.__name__}: {message}"


def test_pace_says_why_it_cannot_judge_a_silent_video():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert assert_pace(NOAUDIO, "a handful of words to time") is None
    assert any("no audio stream" in str(w.message) for w in caught)


def test_digital_silence_reads_differently_from_a_missing_track():
    # Same symptom to a viewer, different cause and different fix.
    assert "digital silence" in raises(assert_loudness, SILENCE)
    assert "missing track" in raises(assert_loudness, NOAUDIO)


# --- one decode, not two ----------------------------------------------------


def test_loudness_and_dead_air_share_a_single_decode():
    from rendercheck import _ffmpeg

    _ffmpeg._measure.cache_clear()
    before = _ffmpeg._measure.cache_info().misses
    assert_loudness(TONE)
    assert_no_dead_air(TONE)
    decodes = _ffmpeg._measure.cache_info().misses - before
    assert decodes == 1, f"expected 1 decode for two checks, did {decodes}"


def test_a_rewritten_file_is_measured_again():
    # The cache is keyed on (path, mtime, size), so measure -> fix -> re-measure
    # in one process reports the new file, not the stale reading.

    scratch = FIXTURES / "rewritten.wav"
    scratch.write_bytes(TONE.read_bytes())
    assert abs(measured(assert_loudness(scratch, tol=3.0)) - (-16.0)) <= 3.0
    scratch.write_bytes(QUIET.read_bytes())
    assert "quieter than" in raises(assert_loudness, scratch)


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
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
