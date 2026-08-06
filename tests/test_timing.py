"""Checks that read timing rather than level: captions, streams, format, presets.

Same shape as the rest of the suite -- real ffmpeg fixtures, no mocks, runnable
with or without pytest:

    python tests/test_timing.py
    pytest tests/ -q
"""

import hashlib
import inspect
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
    assert_not_blank,
    assert_streams_aligned,
    assert_true_peak,
    config,
    presets,
)
from rendercheck import captions as _captions
from rendercheck.text import find_captions, read_cues

# Irregular on purpose: an evenly-spaced rhythm can slide onto itself, which
# would make an offset look like a perfect fit at more than one shift.
BURSTS = [(1.0, 4.0), (7.0, 3.5), (13.0, 5.0), (20.0, 3.5), (26.0, 6.0), (34.0, 4.0)]
SPEECH_SECONDS = 40.0

# The audio is expensive and cached between runs; the .vtt files are rewritten
# every run from these same constants. Keying the directory on them means
# editing BURSTS rebuilds the audio too, instead of leaving the two halves of
# every caption test describing different things with nothing to say so.
_SHAPE = hashlib.sha256(repr((BURSTS, SPEECH_SECONDS)).encode()).hexdigest()[:8]
FIXTURES = Path(tempfile.gettempdir()) / f"rendercheck-timing-{_SHAPE}"


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


# --- the regressions found by review, each one a false verdict --------------


def test_an_offset_past_the_search_range_is_not_reported_as_a_number():
    # It saturated at the edge instead: the real offset is somewhere past the
    # range and was never measured. Quoting the edge value would be a confident
    # wrong number, and at 12s out the sign inverted -- captions 12s LATE were
    # reported as 1s early, then blamed on unfixable clock drift.
    cues = [(1.0, 5.0), (7.0, 10.5), (13.0, 18.0), (20.0, 23.5), (26.0, 32.0)]
    sil = [
        (0.0, 1.0),
        (5.0, 7.0),
        (10.5, 13.0),
        (18.0, 20.0),
        (23.5, 26.0),
        (32.0, 60.0),
    ]
    for shift in (12.0, 20.0):
        moved = [(a + shift, b + shift) for a, b in cues]
        fit = _captions.align(moved, sil, 60.0)
        assert fit is not None and fit.saturated, (shift, fit)
        # And no drift number built on an offset nobody measured.
        assert fit.drift is None, (shift, fit)


def test_captions_for_a_different_cut_are_named_as_that_rather_than_mistimed():
    # Cues outside the media get clamped into the first or last bin, so they
    # pile up at one end and yield a confident, small, wrong offset. Caught
    # before the fit instead.
    outside = FIXTURES / "different-cut.vtt"
    _write_cues(outside, lambda start: start + 30.0)  # SPEECH is only 40s long
    message = raises(assert_captions_aligned, SPEECH, outside)
    assert "does not exist" in message, message
    assert "different cut" in message, message

    # ...but captions that are merely late overhang the end by exactly how late
    # they are, and must still be reported as late rather than as a wrong cut.
    assert "4.0s late" in raises(assert_captions_aligned, SPEECH, LATE)


def test_a_third_with_no_cues_does_not_fabricate_drift():
    # THE false FAIL. Almost every file ends on a beat of silence with no cue
    # over it; that window scores 0.0 at every shift, the tie resolved to the
    # largest, and a perfectly-aligned file was told to re-generate its captions.
    cues = [(1.0, 5.0), (7.0, 10.5), (13.0, 18.0), (20.0, 23.5), (26.0, 32.0)]
    sil = [
        (0.0, 1.0),
        (5.0, 7.0),
        (10.5, 13.0),
        (18.0, 20.0),
        (23.5, 26.0),
        (32.0, 60.0),
    ]
    fit = _captions.align(cues, sil, 60.0)
    assert fit is not None, fit
    assert abs(fit.offset) < 0.2, fit
    assert fit.drift is None, f"drift invented from a window with no cues: {fit}"


def test_a_tie_resolves_toward_no_offset_rather_than_toward_late():
    # With two equally good shifts, claiming the larger one turns every
    # ambiguous fit into a confident accusation that the captions are late.
    speech = [True] * 200
    captions = [False] * 90 + [True] * 20 + [False] * 90
    fit = _captions._best_shift(captions, speech, reach=50)
    assert fit.shift == 0, fit


def test_a_frozen_stretch_running_to_the_end_of_the_file_is_caught():
    # freezedetect prints freeze_start with no freeze_duration when the freeze
    # is still running at EOF. Zipping without a terminator dropped it, so a
    # video that stopped producing frames read as clean -- the headline defect
    # the check exists for.
    frozen = FIXTURES / "frozen-to-eof.mp4"
    if not frozen.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=3",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=320x240:rate=10:duration=8",
            "-filter_complex",
            "[0][1]concat=n=2:v=1:a=0",
            "-pix_fmt",
            "yuv420p",
            str(frozen),
        )
    from rendercheck import assert_not_frozen

    assert "stops moving" in raises(assert_not_frozen, frozen)


def test_a_timestamp_quoted_inside_caption_text_is_not_a_cue():
    quoting = FIXTURES / "quoting.vtt"
    quoting.write_text(
        "WEBVTT\n\n"
        "1\n00:00:01.000 --> 00:00:04.000\n"
        "It runs from 00:00:30.000 --> 00:00:40.000, roughly.\n"
    )
    assert read_cues(quoting) == [(1.0, 4.0)], read_cues(quoting)


# --- stream alignment -------------------------------------------------------


def test_sound_that_runs_out_before_the_picture_is_caught():
    message = raises(assert_streams_aligned, SHORT)
    assert "sound stops 3.0s early" in message, message


def test_streams_of_the_same_length_pass():
    gap = assert_streams_aligned(MATCHED)
    assert gap is not None and abs(gap) < 0.5, gap


def test_a_file_with_only_sound_skips_rather_than_passing():
    assert "only one of" in skips(assert_streams_aligned, SPEECH)


def test_the_gap_is_measured_between_stream_endings_not_between_lengths():
    # A stream ends at start + duration. Comparing durations alone reported a
    # match for a file whose sound genuinely runs 1.5s past the picture -- and
    # widening --max-start-skew to tolerate a known pre-roll silently threw the
    # ending check away entirely.
    from unittest.mock import patch

    from rendercheck import _ffmpeg as probe
    from rendercheck import media as m

    offset = (
        probe.Stream("video", 0.0, 8.0, 320, 240, 10.0, 10.0),
        probe.Stream("audio", 1.5, 8.0, None, None, None, None),
    )
    with (
        patch.object(probe, "streams", return_value=offset),
        patch.object(m, "existing", lambda path: Path(path)),
    ):
        assert "sound" in raises(
            assert_streams_aligned, "offset.mp4", max_start_skew=5.0
        )


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


def test_a_wrong_frame_rate_is_reported_with_both_numbers():
    message = raises(assert_format, MATCHED, fps=30.0)
    assert "10.000 fps, not 30" in message, message


def test_a_variable_rate_file_is_caught_by_asking_for_its_nominal_rate():
    # The VFR branch is a headline feature in the README and had no test. A file
    # whose nominal and average rates disagree plays at a rate that changes as it
    # goes, which is a standard cause of audio drifting against picture -- and it
    # passes a plain rate comparison, because the nominal rate is correct.
    vfr = FIXTURES / "variable-rate.mp4"
    if not vfr.exists():
        # Two segments at different rates joined into one file: the actual cause
        # in the wild, not a synthetic approximation of it. The container ends up
        # declaring 30 fps and averaging about 20.
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=2",
            "-filter_complex",
            "[0][1]concat=n=2:v=1:a=0",
            "-fps_mode",
            "vfr",
            "-pix_fmt",
            "yuv420p",
            str(vfr),
        )

    from rendercheck import _ffmpeg as probe

    video = next(s for s in probe.streams(vfr) if s.kind == "video")
    if video.fps is None or video.average_fps is None:
        return  # this ffmpeg did not record both rates; nothing to compare
    if abs(video.fps - video.average_fps) <= 0.01:
        return  # this build re-timed it to constant rate on the way out

    message = raises(assert_format, vfr, fps=video.fps)
    assert "variable-rate" in message, message


def test_format_checks_only_what_it_was_asked_about():
    # Passing nothing must not invent an expectation to fail against.
    assert_format(MATCHED)
    assert_format(MATCHED, width=320)


def test_a_file_with_no_picture_skips_rather_than_passing():
    assert "no video stream" in skips(assert_format, SPEECH, width=1920)


def test_undeclared_dimensions_skip_rather_than_pass_the_requirement():
    # Returning silently here made the CLI record PASS for a size nothing was
    # ever compared against -- the one spot in the file where "could not
    # measure" read as "measured and fine".
    from unittest.mock import patch

    from rendercheck import _ffmpeg as probe
    from rendercheck import media as m

    blind = (probe.Stream("video", 0.0, 8.0, None, None, 10.0, 10.0),)
    with (
        patch.object(probe, "streams", return_value=blind),
        patch.object(m, "existing", lambda path: Path(path)),
    ):
        reason = skips(assert_format, "blind.mp4", width=1920, height=1080)
    assert "no picture dimensions" in reason, reason


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
        # None means "no ceiling stated"; a ceiling at 0 dBTP is not a ceiling.
        assert preset.max_true_peak is None or preset.max_true_peak < 0, name
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
    #
    # Read from `__kwdefaults__`, not `__defaults__`: these arguments are
    # keyword-only, so `__defaults__` is None for *any* value and asserting on
    # it passes even after someone changes the target to -18.
    from rendercheck.media import assert_loudness

    default = presets.get(presets.DEFAULT)
    built_in = assert_loudness.__kwdefaults__ or {}
    assert built_in["target_lufs"] == default.target_lufs
    assert built_in["tol"] == default.tol
    # And no peak ceiling, so naming the default is a no-op rather than a gate.
    assert default.max_true_peak is None


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


# --- blank stills -----------------------------------------------------------
#
# The failure image generators actually produce. Every case here is a real file
# on disk, because the whole claim is about what ffmpeg reads back out of one.


def _still(name, *filters):
    png = FIXTURES / f"{name}.png"
    if not png.exists():
        args = ["-f", "lavfi", "-i", "color=c=white:s=320x180", "-frames:v", "1"]
        if filters:
            args += ["-vf", ",".join(filters)]
        _ffmpeg(*args, str(png))
    return png


def _solid(name, colour):
    png = FIXTURES / f"{name}.png"
    if not png.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"color=c={colour}:s=320x180",
            "-frames:v",
            "1",
            str(png),
        )
    return png


def test_a_solid_canvas_fails_whatever_colour_it_is():
    # blackdetect sees only the first of these. A generator that returns an
    # empty white or grey frame is the same defect and just as silent.
    for colour, word in (("black", "solid black"), ("white", "solid white")):
        message = raises(assert_not_blank, _solid(f"blank-{colour}", colour))
        assert word in message, message
        assert "blank canvas" in message, message
    grey = raises(assert_not_blank, _solid("blank-grey", "0x808080"))
    assert "flat tone" in grey, grey


def test_one_stray_pixel_does_not_rescue_a_blank_canvas():
    # THE case the percentile fields exist for. A blank frame carrying a single
    # 4x4 artifact spans the full 16-235 range on YMIN/YMAX, so a min-to-max
    # reading calls it full-contrast content. YLOW/YHIGH still report 235-235.
    stray = _still("blank-stray", "drawbox=x=0:y=0:w=4:h=4:color=black:t=fill")
    assert "blank canvas" in raises(assert_not_blank, stray)


def test_a_still_with_content_passes():
    drawn = _still("drawn", "drawbox=x=20:y=40:w=280:h=100:color=black:t=fill")
    spread = assert_not_blank(drawn)
    assert spread is not None and spread > 200, spread


def test_a_frame_rate_is_not_asserted_against_a_still():
    # ffprobe invents 25 fps for a .png. Comparing against it raises on a number
    # nobody produced, so this has to skip -- and skip loudly enough to say why.
    drawn = _still("drawn", "drawbox=x=20:y=40:w=280:h=100:color=black:t=fill")
    reason = skips(assert_format, drawn, fps=30.0)
    assert "still" in reason, reason


# --- loudness range and audio format ----------------------------------------


def test_loudness_range_has_no_floor_because_level_speech_reads_zero():
    # Consistently-levelled narration measures ~0 LU after gating, and that is
    # correct rather than a defect. A floor would fail every TTS render there is,
    # so there is not one -- this asserts the *absence*, which is a design
    # decision that would otherwise be silently reintroduced.
    from rendercheck import assert_loudness_range

    assert assert_loudness_range(SPEECH) is not None
    # No `min_lra` parameter exists to reintroduce one by accident.
    assert "min_lra" not in inspect.signature(assert_loudness_range).parameters


def test_loudness_range_fires_only_past_its_ceiling():
    from rendercheck import assert_loudness_range

    measured = assert_loudness_range(SPEECH)
    assert measured is not None
    # Tightened below whatever this file actually reads, so the fixture cannot
    # drift out from under the assertion.
    message = raises(assert_loudness_range, SPEECH, max_lra=max(measured - 0.1, 0.0))
    assert "swings" in message and "volume setting" in message, message


def test_audio_format_reports_the_rate_and_count_it_found():
    from rendercheck import _ffmpeg as probe
    from rendercheck import assert_audio_format

    # Read from the fixture rather than hardcoded: what matters is that the
    # check agrees with the file, not what ffmpeg happened to pick for it.
    audio = next(s for s in probe.streams(SPEECH) if s.kind == "audio")
    rate, count = audio.sample_rate, audio.channels
    assert rate is not None and count is not None, audio

    # Agreeing with the file raises nothing.
    assert_audio_format(SPEECH, sample_rate=rate, channels=count)

    wrong_rate = raises(assert_audio_format, SPEECH, sample_rate=rate + 100)
    assert f"sample rate of {rate} Hz" in wrong_rate, wrong_rate
    assert "resampled downstream" in wrong_rate, wrong_rate

    wrong_count = raises(assert_audio_format, SPEECH, channels=count + 1)
    assert f"channel count of {count}" in wrong_count, wrong_count


def test_audio_format_checks_only_what_it_was_given():
    from rendercheck import assert_audio_format

    # Neither argument given: nothing to compare, and nothing raised.
    assert_audio_format(SPEECH)


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


# --- what the 0.4.0 review found -------------------------------------------
#
# Every one of these passed its own tests in 0.4.0 and was wrong anyway. They
# are here because a test that only covers the case you thought of is how the
# original defects survived review in the first place.


def _clip(name, *args):
    """A moving picture, cached like the rest of the fixtures."""
    path = FIXTURES / name
    if not path.exists():
        _ffmpeg(*args, str(path))
    return path


def test_an_animated_gif_is_not_judged_on_its_first_frame():
    # A clip opening on a dark leader is not a blank canvas. The CLI routes every
    # image extension into the blank check, so this reported an empty render.
    fade = _clip(
        "dark-leader.gif",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x120:d=1",
        "-f",
        "lavfi",
        "-i",
        "testsrc=s=160x120:d=2",
        "-filter_complex",
        "[0][1]concat=n=2:v=1:a=0",
    )
    reason = skips(assert_not_blank, fade)
    assert "moving picture" in reason, reason


def test_a_single_frame_gif_is_still_treated_as_a_still():
    # The other half of the same question: gif carries either, and the frame
    # count is what settles it. Skipping every gif would lose a real check.
    one = _clip(
        "one-frame.gif",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x120",
        "-frames:v",
        "1",
    )
    assert "blank canvas" in raises(assert_not_blank, one)


def test_real_mjpeg_video_keeps_its_frame_rate_check():
    # `.jpg` and a Matroska of motion JPEG both report codec `mjpeg`, and neither
    # declares a frame count -- so a codec test called this a still and silently
    # switched off the rate and variable-rate checks for the whole family.
    mj = _clip(
        "motion-jpeg.mkv",
        "-f",
        "lavfi",
        "-i",
        "testsrc=s=160x120:rate=10:d=2",
        "-c:v",
        "mjpeg",
    )
    assert "10.000 fps, not 999" in raises(assert_format, mj, fps=999.0)


def test_the_blank_reading_comes_from_the_first_frame():
    # `-frames:v 1` bounds the output, not the filter graph: it emits two
    # metadata blocks, and keeping the last made the verdict depend on how far
    # ahead ffmpeg ran. Frame 0 here is black, frame 1 onwards is not.
    from rendercheck import _ffmpeg as probe

    fade = _clip(
        "dark-leader.gif",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x120:d=1",
        "-f",
        "lavfi",
        "-i",
        "testsrc=s=160x120:d=2",
        "-filter_complex",
        "[0][1]concat=n=2:v=1:a=0",
    )
    picture = probe.signalstats(fade)
    assert picture.high - picture.low == 0.0, picture


def test_luma_is_reported_on_the_same_scale_whatever_the_bit_depth():
    # signalstats reports in the source's own depth: a 10-bit black frame reads
    # 64, not 16, so every threshold here was four times less sensitive on it.
    from rendercheck import _ffmpeg as probe

    ten = _clip(
        "black-10bit.mp4",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x120",
        "-frames:v",
        "1",
        "-pix_fmt",
        "yuv420p10le",
        "-c:v",
        "libx264",
    )
    picture = probe.signalstats(ten)
    assert picture.low < 16.0, picture
    assert picture.high - picture.low == 0.0, picture


def test_audio_format_does_not_report_a_passing_check_as_skipped():
    # One unreadable field used to make the whole check SKIP, so a channel count
    # that was compared and matched read as unmeasured -- and under --strict that
    # turns a correct file into a failure.
    from rendercheck import _ffmpeg as probe
    from rendercheck import assert_audio_format

    audio = next(s for s in probe.streams(SPEECH) if s.kind == "audio")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", Skipped)
        assert_audio_format(SPEECH, channels=audio.channels)
    assert not [w for w in caught if issubclass(w.category, Skipped)], caught
