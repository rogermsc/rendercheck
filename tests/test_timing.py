"""Checks that read timing rather than level: captions, streams, format, presets.

Same shape as the rest of the suite -- real ffmpeg fixtures, no mocks, runnable
with or without pytest:

    python tests/test_timing.py
    pytest tests/ -q
"""

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rendercheck import (
    SilentFail,
    Skipped,
    assert_captions_aligned,
    assert_format,
    assert_streams_aligned,
    assert_true_peak,
    config,
    presets,
)
from rendercheck import captions as _captions
from rendercheck.text import find_captions, read_cues

FIXTURES = Path(tempfile.gettempdir()) / "rendercheck-timing-fixtures"

# Irregular on purpose: an evenly-spaced rhythm can slide onto itself, which
# would make an offset look like a perfect fit at more than one shift.
BURSTS = [(1.0, 4.0), (7.0, 3.5), (13.0, 5.0), (20.0, 3.5), (26.0, 6.0), (34.0, 4.0)]
SPEECH_SECONDS = 40.0


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _stamp(at):
    minutes, seconds = divmod(max(at, 0.0), 60)
    return f"00:{int(minutes):02d}:{seconds:06.3f}"


def _write_cues(dest, shift=lambda start: start):
    rows = ["WEBVTT", ""]
    for index, (start, length) in enumerate(BURSTS, 1):
        rows += [
            str(index),
            f"{_stamp(shift(start))} --> {_stamp(shift(start + length))}",
            f"Line {index}.",
            "",
        ]
    dest.write_text("\n".join(rows))


def build_fixtures():
    FIXTURES.mkdir(exist_ok=True)
    names = (
        "speech.wav",
        "continuous.wav",
        "short-audio.mp4",
        "matched.mp4",
        "aligned.vtt",
        "late.vtt",
        "drift.vtt",
    )
    speech, continuous, short, matched, aligned, late, drift = (
        FIXTURES / n for n in names
    )

    if not speech.exists():
        # A tone gated into sentence-shaped bursts. The caption check matches
        # the shape of the talking, so the fixture needs a shape to match.
        talking = "+".join(f"between(t,{s},{s + d})" for s, d in BURSTS)
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=300:duration={SPEECH_SECONDS:g}",
            "-af",
            f"volume=0:enable='not({talking})',"
            f"loudnorm=I=-16:TP=-1.5:LRA=11,"
            f"afade=t=out:st={SPEECH_SECONDS - 0.4:g}:d=0.4",
            str(speech),
        )
    if not continuous.exists():
        # No silence anywhere: nothing to align against, at any shift.
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=300:duration={SPEECH_SECONDS:g}",
            "-af",
            f"afade=t=out:st={SPEECH_SECONDS - 0.4:g}:d=0.4",
            str(continuous),
        )
    if not short.exists():
        # Eight seconds of picture, five of sound.
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-t",
            "8",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(short),
        )
    if not matched.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            "-t",
            "8",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(matched),
        )

    _write_cues(aligned)
    _write_cues(late, lambda start: start + 4.0)
    # 12% slow: a small error at the start that has grown to several seconds by
    # the end. No single shift corrects it, which is what makes it drift.
    _write_cues(drift, lambda start: start * 1.12)
    return speech, continuous, short, matched, aligned, late, drift


if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    raise unittest.SkipTest("fixtures need ffmpeg and ffprobe on PATH")

SPEECH, CONTINUOUS, SHORT, MATCHED, ALIGNED, LATE, DRIFT = build_fixtures()


def raises(check, *args, **kwargs):
    try:
        check(*args, **kwargs)
    except SilentFail as exc:
        return str(exc)
    raise AssertionError(f"{check.__name__} should have failed on {args!r}")


def skips(check, *args, **kwargs):
    """Assert the check declined to judge, and hand back the reason.

    The distinction this suite cares about most: a check that cannot measure
    must skip, never pass. A pass would be a claim nobody verified.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", Skipped)
        result = check(*args, **kwargs)
    reasons = [str(w.message) for w in caught if issubclass(w.category, Skipped)]
    assert result is None, f"expected a skip, got {result!r}"
    assert reasons, "skipped without saying why"
    return reasons[0]


# --- cue parsing ------------------------------------------------------------


def test_cues_are_read_with_their_timings():
    cues = read_cues(ALIGNED)
    assert len(cues) == len(BURSTS), cues
    assert cues[0] == (1.0, 5.0), cues[0]


def test_srt_commas_parse_the_same_as_vtt_periods():
    srt = FIXTURES / "commas.srt"
    srt.write_text("1\n00:00:01,500 --> 00:00:04,250\nLine.\n")
    assert read_cues(srt) == [(1.5, 4.25)]


def test_a_short_fraction_is_milliseconds_not_a_fraction_of_a_second():
    # `.5` means half a second, not five milliseconds. Getting this backwards
    # would put every cue in a hand-written file 495ms out.
    srt = FIXTURES / "short-fraction.srt"
    srt.write_text("1\n00:00:01.5 --> 00:00:04.2\nLine.\n")
    assert read_cues(srt) == [(1.5, 4.2)]


def test_hours_are_not_dropped():
    srt = FIXTURES / "hours.srt"
    srt.write_text("1\n01:02:03.000 --> 01:02:04.000\nLine.\n")
    assert read_cues(srt) == [(3723.0, 3724.0)]


def test_a_malformed_row_does_not_lose_the_rest_of_the_file():
    broken = FIXTURES / "broken.vtt"
    broken.write_text(
        "WEBVTT\n\n1\nnot a timestamp --> also not one\nLine.\n\n"
        "2\n00:00:05.000 --> 00:00:07.000\nLine.\n"
    )
    assert read_cues(broken) == [(5.0, 7.0)]


def test_a_zero_length_cue_is_not_a_span():
    empty = FIXTURES / "zero.vtt"
    empty.write_text("WEBVTT\n\n1\n00:00:05.000 --> 00:00:05.000\nLine.\n")
    assert read_cues(empty) == []


# --- caption alignment ------------------------------------------------------


def test_captions_that_match_the_audio_pass():
    offset = assert_captions_aligned(SPEECH, ALIGNED)
    assert offset is not None and abs(offset) < 0.2, offset


def test_captions_running_late_are_caught_with_the_amount():
    message = raises(assert_captions_aligned, SPEECH, LATE)
    assert "4.0s late" in message, message


def test_drift_is_reported_as_drift_rather_than_as_an_offset():
    # A drifting file also has an average offset, so testing offset first would
    # report the symptom and bury the cause. The message must name the cause.
    message = raises(assert_captions_aligned, SPEECH, DRIFT)
    assert "drifts" in message, message
    assert "no single offset" in message, message


def test_audio_with_no_silence_skips_rather_than_claiming_alignment():
    # THE case this check could most easily get wrong. Every shift scores
    # identically against wall-to-wall speech, so a naive implementation reports
    # a confident zero offset and passes. There is nothing here to measure.
    reason = skips(assert_captions_aligned, CONTINUOUS, ALIGNED)
    assert "no alignment stood out" in reason, reason


def test_an_empty_caption_file_skips_rather_than_passing():
    empty = FIXTURES / "empty.vtt"
    empty.write_text("WEBVTT\n")
    assert "no cues" in skips(assert_captions_aligned, SPEECH, empty)


def test_a_missing_caption_file_raises_rather_than_skipping():
    try:
        assert_captions_aligned(SPEECH, FIXTURES / "not-here.vtt")
    except FileNotFoundError:
        return
    raise AssertionError("a typo'd caption path should not be a skip")


def test_alignment_reports_no_drift_when_the_file_is_too_short_to_tell():
    # Rather than reporting zero, which would claim a measurement nobody took.
    fit = _captions.align([(1.0, 2.0)], [(0.0, 1.0), (2.0, 3.0)], 3.0)
    assert fit is not None and fit.drift is None, fit


def test_captions_are_found_beside_the_media_without_a_flag():
    beside = SPEECH.with_suffix(".vtt")
    _write_cues(beside)
    try:
        assert find_captions(SPEECH) == beside
        offset = assert_captions_aligned(SPEECH, beside)
        assert offset is not None and abs(offset) < 0.2, offset
    finally:
        beside.unlink()
    assert find_captions(SPEECH) is None


# --- stream alignment -------------------------------------------------------


def test_sound_that_runs_out_before_the_picture_is_caught():
    message = raises(assert_streams_aligned, SHORT)
    assert "sound stops 3.0s early" in message, message


def test_streams_of_the_same_length_pass():
    gap = assert_streams_aligned(MATCHED)
    assert gap is not None and abs(gap) < 0.5, gap


def test_a_file_with_only_sound_skips_rather_than_passing():
    assert "only one of" in skips(assert_streams_aligned, SPEECH)


def test_an_undeclared_duration_skips_rather_than_comparing_against_nothing():
    # Matroska carries no per-stream duration. Comparing against a number we
    # invented would be a confident wrong answer.
    mkv = FIXTURES / "no-durations.mkv"
    if not mkv.exists():
        # aac rather than libopus: every ffmpeg build has it, and Matroska
        # declares no per-stream duration either way. A test that depends on an
        # optional encoder fails on the runner rather than on the code.
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-t",
            "5",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(mkv),
        )
    from rendercheck import _ffmpeg as probe

    declared = [s.length for s in probe.streams(mkv)]
    if all(length is not None for length in declared):
        return  # this build of ffmpeg does declare them; nothing to assert
    assert "declares no per-stream duration" in skips(assert_streams_aligned, mkv)


# --- format -----------------------------------------------------------------


def test_a_render_at_the_wrong_size_is_caught():
    message = raises(assert_format, MATCHED, width=1920, height=1080)
    assert "320x240" in message, message


def test_the_right_size_and_rate_pass():
    assert_format(MATCHED, width=320, height=240, fps=10)


def test_format_checks_only_what_it_was_asked_about():
    # Passing nothing must not invent an expectation to fail against.
    assert_format(MATCHED)
    assert_format(MATCHED, width=320)


def test_a_file_with_no_picture_skips_rather_than_passing():
    assert "no video stream" in skips(assert_format, SPEECH, width=1920)


# --- true peak and presets --------------------------------------------------


def test_true_peak_reads_a_level_from_a_normalised_file():
    peak = assert_true_peak(SPEECH, max_dbtp=0.0)
    assert peak is not None and peak < 0.0, peak


def test_a_ceiling_below_the_measured_peak_fails():
    message = raises(assert_true_peak, SPEECH, max_dbtp=-40.0)
    assert "dBTP" in message and "after upload" in message, message


def test_every_preset_is_a_usable_target():
    for name in presets.PRESETS:
        preset = presets.get(name)
        assert preset.tol > 0, name
        assert preset.max_true_peak < 0, name  # a ceiling at 0 is not a ceiling
        assert preset.note, name


def test_an_unknown_preset_names_the_alternatives():
    try:
        presets.get("youtub")
    except ValueError as exc:
        assert "youtube" in str(exc), exc
        return
    raise AssertionError("a typo'd preset should not resolve")


def test_the_default_preset_matches_the_library_default():
    # `web` exists so the built-in default has a name. If they ever disagree,
    # `--preset web` would silently change behaviour rather than describe it.
    from rendercheck.media import assert_loudness

    assert presets.get(presets.DEFAULT).target_lufs == -16.0
    assert assert_loudness.__defaults__ is None  # keyword-only, as intended


# --- config -----------------------------------------------------------------
#
# Reading a config file needs `tomllib`, which arrived in 3.11. Both branches
# are real behaviour and both are tested -- on 3.10 the *only* correct outcome
# is the announced degradation, and asserting the 3.11 behaviour there would be
# testing an interpreter we do not run on.


def test_config_is_read_from_the_nearest_file_upward():
    if not config.HAVE_TOML:
        return
    root = FIXTURES / "project"
    (root / "out").mkdir(parents=True, exist_ok=True)
    (root / "rendercheck.toml").write_text('preset = "ebu"\nmax-silence = 9.0\n')
    settings = config.load(start=root / "out", known={"preset", "max_silence"})
    assert settings == {"preset": "ebu", "max_silence": 9.0}, settings


def test_an_unknown_config_key_is_reported_rather_than_ignored():
    if not config.HAVE_TOML:
        return
    root = FIXTURES / "typo"
    root.mkdir(parents=True, exist_ok=True)
    (root / "rendercheck.toml").write_text("max_slience = 9.0\n")
    noise = io.StringIO()
    with redirect_stderr(noise):
        settings = config.load(start=root, known={"max_silence"})
    assert settings == {}, settings
    assert "max_slience" in noise.getvalue(), noise.getvalue()


def test_a_pyproject_section_works_the_same_as_a_standalone_file():
    if not config.HAVE_TOML:
        return
    root = FIXTURES / "pyproject-only"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\n\n[tool.rendercheck]\nmax_silence = 4.0\n'
    )
    assert config.load(start=root, known={"max_silence"}) == {"max_silence": 4.0}


def test_python_310_says_it_is_ignoring_the_file_rather_than_doing_it_silently():
    # The whole point: a config file that quietly does nothing is the failure
    # this library is named after. On 3.10 it has to announce itself.
    if config.HAVE_TOML:
        return
    root = FIXTURES / "no-toml"
    root.mkdir(parents=True, exist_ok=True)
    (root / "rendercheck.toml").write_text("max_silence = 9.0\n")
    noise = io.StringIO()
    with redirect_stderr(noise):
        settings = config.load(start=root, known={"max_silence"})
    assert settings == {}, settings
    assert "3.11" in noise.getvalue(), noise.getvalue()


def test_a_missing_config_is_not_an_error():
    # Held outside the version guard: this is the common case on every version.
    assert config.load(start=Path(FIXTURES.anchor), known=set()) == {}


if __name__ == "__main__":
    failures = 0
    for name, test in sorted(dict(globals()).items()):
        if name.startswith("test_") and callable(test):
            try:
                test()
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("all timing checks passed" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)
