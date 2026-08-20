"""Picture motion against the speech envelope: `assert_lip_sync`.

Same shape as the rest of the suite -- real ffmpeg fixtures, no mocks, runnable
with or without pytest:

    python tests/test_lipsync.py
    pytest tests/ -q

The fixture is the fiddly part and two obvious constructions do not work; see
`build_fixtures` for what was measured and why this one is built the way it is.
"""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rendercheck import SilentFail, Skipped, assert_lip_sync
from rendercheck import lipsync as _lipsync

# Irregular on purpose: an evenly-spaced rhythm slides onto itself, which makes
# an offset look like a perfect fit at more than one shift.
BURSTS = [(1.0, 4.0), (7.0, 3.5), (13.0, 5.0), (20.0, 3.5), (26.0, 6.0), (34.0, 4.0)]
SPEECH_SECONDS = 40.0
FPS = 25

# Somebody else's audio, for the "wrong track" fixture. Two things about it are
# deliberate. It is not a *shifted* BURSTS -- a shifted gate is a real constant
# offset, which the check is right to report. And it is denser and shorter-
# breathed rather than merely rearranged: with only six long bursts in forty
# seconds, two rhythms of the same shape overlap so much at zero that they
# genuinely correlate. Measured on this one: peak 0.16 (inside the range real
# clips produce) but ratio 1.10, well under the gate -- which is the whole point
# of gating on the ratio instead of the peak.
OTHER_BURSTS = [
    (0.3, 0.8),
    (2.0, 1.2),
    (4.5, 0.9),
    (6.2, 1.5),
    (9.0, 0.7),
    (11.0, 1.8),
    (14.5, 1.0),
    (17.0, 0.6),
    (19.0, 2.2),
    (23.0, 0.9),
    (25.5, 1.4),
    (28.0, 0.8),
    (31.0, 1.6),
    (34.5, 0.9),
    (37.0, 1.2),
]

_SHAPE = hashlib.sha256(
    repr((BURSTS, OTHER_BURSTS, SPEECH_SECONDS, FPS)).encode()
).hexdigest()[:8]
FIXTURES = Path(tempfile.gettempdir()) / f"rendercheck-lipsync-{_SHAPE}"


def _ffmpeg(*args):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *args], check=True, capture_output=True
    )


def _gate(shift=0.0, bursts=None):
    """The `enable` expression for "someone is talking now"."""
    return "+".join(
        f"between(t,{start + shift:g},{start + length + shift:g})"
        for start, length in (bursts or BURSTS)
    )


def _talking(dest, *, video_shift=0.0, audio_shift=0.0, audio_bursts=None):
    """A synthetic talking head whose motion and sound share one rhythm.

    Two constructions that look right and are not, both measured before this one:

    * `drawbox` with an animated height. The expression is evaluated **once at
      init**, and ffmpeg 9 has no `eval` option on drawbox, so the box never
      moves and motion appears only at the two burst edges.
    * A box merely *present* for the burst. Motion is then two impulses per
      burst, and because frame-differencing is rectified, the edge going in and
      the edge going out are both positive while the audio ramps are opposite in
      sign -- so they cancel. Measured: peak 0.02 and a recovered offset of
      -1.44 s, i.e. worse than useless.

    What works is a mouth that opens and shuts on *every* frame while talking,
    giving a sustained rectangular envelope rather than two spikes. The second,
    smaller box alternates for the whole clip: a real presenter is never
    perfectly still, and without it the fixture's duty cycle is 0.66 and the
    applicability gate correctly refuses to look at it.
    """
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=320x240:r={FPS}:d={SPEECH_SECONDS:g}",
        "-f",
        "lavfi",
        "-i",
        f"sine=f=300:r=48000:d={SPEECH_SECONDS:g}",
        "-filter_complex",
        f"[0:v]drawbox=x=110:y=205:w=100:h=20:color=white:t=fill:"
        f"enable='eq(mod(n\\,2)\\,0)',"
        f"drawbox=x=110:y=140:w=100:h=60:color=white:t=fill:"
        f"enable='({_gate(video_shift)})*eq(mod(n\\,2)\\,0)'[v];"
        f"[1:a]volume=0:enable='not({_gate(audio_shift, audio_bursts)})',"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"afade=t=out:st={SPEECH_SECONDS - 0.4:g}:d=0.4[a]",
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


def build_fixtures():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    synced = FIXTURES / "synced.mp4"
    late = FIXTURES / "late.mp4"
    early = FIXTURES / "early.mp4"
    mismatched = FIXTURES / "mismatched.mp4"
    still = FIXTURES / "still.mp4"
    silent = FIXTURES / "silent.mp4"
    short = FIXTURES / "short.mp4"
    slideshow = FIXTURES / "slideshow.mp4"
    noaudio = FIXTURES / "noaudio.mp4"

    if not synced.exists():
        _talking(synced)
    if not late.exists():
        # The audio's rhythm moved, the picture's did not: the sound now arrives
        # half a second after the mouth that should have made it.
        _talking(late, audio_shift=0.5)
    if not early.exists():
        _talking(early, audio_shift=-0.5)
    if not mismatched.exists():
        # Same content statistics, genuinely UNRELATED rhythm -- a head
        # lip-synced to somebody else's audio. Note this is not the same thing
        # as shifting the gate: a shifted gate is a real constant offset and the
        # check is right to report it. The check must decline here, not invent
        # an offset.
        _talking(mismatched, audio_bursts=OTHER_BURSTS)
    if not still.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s=320x240:r={FPS}:d={SPEECH_SECONDS:g}",
            "-f",
            "lavfi",
            "-i",
            f"sine=f=300:r=48000:d={SPEECH_SECONDS:g}",
            "-filter_complex",
            f"[1:a]volume=0:enable='not({_gate()})'[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(still),
        )
    if not silent.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x240:r={FPS}:d={SPEECH_SECONDS:g}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=mono:d={SPEECH_SECONDS:g}",
            "-filter_complex",
            "[0:v]drawbox=x=110:y=140:w=100:h=60:color=white:t=fill:"
            "enable='eq(mod(n\\,2)\\,0)'[v]",
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-t",
            f"{SPEECH_SECONDS:g}",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(silent),
        )
    if not short.exists():
        _ffmpeg("-i", str(synced), "-t", "10", "-c", "copy", str(short))
    if not slideshow.exists():
        # A held frame that cuts twice: the shape of a slide deck, and the thing
        # the duty-cycle gate exists to keep this check away from.
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x240:r={FPS}:d={SPEECH_SECONDS:g}",
            "-f",
            "lavfi",
            "-i",
            f"sine=f=300:r=48000:d={SPEECH_SECONDS:g}",
            "-filter_complex",
            f"[0:v]drawbox=x=20:y=20:w=280:h=200:color=white:t=fill:"
            f"enable='between(t,12,26)'[v];"
            f"[1:a]volume=0:enable='not({_gate()})'[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(slideshow),
        )
    if not noaudio.exists():
        _ffmpeg("-i", str(synced), "-an", "-c:v", "copy", str(noaudio))
    return synced, late, early, mismatched, still, silent, short, slideshow, noaudio


if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    raise unittest.SkipTest("fixtures need ffmpeg and ffprobe on PATH")

SYNCED, LATE, EARLY, MISMATCHED, STILL, SILENT, SHORT, SLIDESHOW, NOAUDIO = (
    build_fixtures()
)

# The fixture is not HeyGen and must not inherit HeyGen's calibration -- its
# mouth and its sound share one clock by construction, so its true offset is 0.
NO_BIAS = {"bias": 0.0}


def raises(check, *args, **kwargs):
    try:
        check(*args, **kwargs)
    except SilentFail as exc:
        return str(exc)
    raise AssertionError(f"{check.__name__} should have failed on {args!r}")


def skips(check, *args, **kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", Skipped)
        result = check(*args, **kwargs)
    reasons = [str(w.message) for w in caught if issubclass(w.category, Skipped)]
    assert result is None, f"expected a skip, got {result!r}"
    assert reasons, "skipped without saying why"
    return reasons[0]


# --- the arithmetic, with no media at all -----------------------------------


def _ramp(length, seed=12345):
    """An aperiodic curve with structure to lock onto.

    Deliberately not periodic: a repeating waveform fits itself at every
    multiple of its period, so a delayed copy has many equally good answers and
    the test would be asserting which tie the tie-break happened to pick.
    """
    value = seed
    out = []
    for _ in range(length):
        value = (value * 1103515245 + 12345) % 2147483648
        out.append(float(value % 1000))
    return out


def test_a_delayed_copy_is_found_at_exactly_that_delay():
    signal = _ramp(600)
    delayed = [signal[0]] * 7 + signal[:-7]
    fit = _lipsync.align(signal, delayed, bias=0.0)
    assert fit is not None
    assert abs(fit.offset - 7 * _lipsync.BIN_SECONDS) < 1e-9, fit.offset


def test_positive_offset_means_the_sound_is_late():
    """The sign convention, pinned.

    Every other timing check here says positive means late, and a sign error
    would invert every verdict this check ever gives while breaking nothing else.
    """
    signal = _ramp(600)
    late = _lipsync.align(signal, [signal[0]] * 10 + signal[:-10], bias=0.0)
    assert late is not None and late.offset > 0, late
    early = _lipsync.align(signal, signal[10:] + [signal[-1]] * 10, bias=0.0)
    assert early is not None and early.offset < 0, early


def test_bias_is_subtracted_in_the_direction_that_makes_a_synced_file_read_zero():
    signal = _ramp(600)
    delayed = [signal[0]] * 9 + signal[:-9]
    shift = 9 * _lipsync.BIN_SECONDS
    fit = _lipsync.align(signal, delayed, bias=shift)
    assert fit is not None and abs(fit.offset) < 1e-9, fit


def test_a_flat_curve_is_not_a_correlation_of_zero():
    """A held frame or digital silence must read as "cannot tell", never as fit."""
    flat = [1.0] * 400
    assert _lipsync.align(flat, _ramp(400)) is None
    assert _lipsync.align(_ramp(400), flat) is None


def test_two_samples_do_not_explode():
    assert _lipsync.align([1.0, 2.0], [1.0, 2.0]) is None


def test_noise_against_noise_does_not_stand_out():
    """The gate that decides whether the check speaks at all."""
    left = [float((index * 7919) % 101) for index in range(900)]
    right = [float((index * 6247) % 97) for index in range(900)]
    fit = _lipsync.align(left, right)
    assert fit is not None
    assert not fit.distinct, fit.ratio


def test_a_tie_resolves_toward_no_offset_rather_than_toward_late():
    period = 25
    wave = [float(index % period) for index in range(500)]
    fit = _lipsync._best_shift(wave, wave, int(2.0 / _lipsync.BIN_SECONDS))
    assert abs(fit.shift) <= period, fit.shift


def test_high_pass_removes_a_constant_and_keeps_a_step():
    assert all(abs(value) < 1e-9 for value in _lipsync.high_pass([5.0] * 60, 12))
    stepped = _lipsync.high_pass([0.0] * 60 + [10.0] * 60, 12)
    assert max(stepped) > 1.0


def test_duty_cycle_separates_continuous_motion_from_a_held_shot():
    assert _lipsync.duty_cycle([10.0] * 100) == 1.0
    assert _lipsync.duty_cycle([0.0] * 98 + [500.0, 500.0]) < 0.05
    assert _lipsync.duty_cycle([]) == 0.0
    assert _lipsync.duty_cycle([0.0] * 50) == 0.0


def test_cells_are_chosen_by_variance_and_never_by_the_audio():
    # Two cells: one that never moves, one that does. Only the second can win.
    totals = [0.0, 100.0]
    squares = [0.0, 2000.0]
    assert _lipsync.motion_from_cells(totals, squares, 50) == [1]


def test_drift_is_none_when_a_window_runs_to_the_edge_of_the_search():
    """No drift arithmetic built on an offset nobody measured."""
    signal = _ramp(900)
    far = signal[len(signal) // 2 :] + signal[: len(signal) // 2]
    fit = _lipsync.align(signal, far, max_shift=0.4)
    if fit is not None and fit.saturated:
        assert fit.drift is None


def test_drift_is_none_when_the_file_is_too_short_to_fit_its_own_thirds():
    signal = _ramp(200)
    fit = _lipsync.align(signal, signal, max_shift=3.0, bias=0.0)
    assert fit is None or fit.drift is None


# --- against real media -----------------------------------------------------


def test_a_synced_talking_head_passes():
    offset = assert_lip_sync(SYNCED, **NO_BIAS)
    assert offset is not None
    assert abs(offset) < 0.1, offset


def test_sound_half_a_second_late_fails_and_says_so():
    message = raises(assert_lip_sync, LATE, **NO_BIAS)
    assert "late" in message
    assert "0.5" in message or "0.4" in message, message


def test_sound_half_a_second_early_fails_the_other_way():
    message = raises(assert_lip_sync, EARLY, **NO_BIAS)
    assert "early" in message, message


def test_a_head_synced_to_unrelated_audio_declines_rather_than_inventing_a_number():
    """The most important test here.

    A peak that does not stand clear is not a small offset, it is no
    measurement. Reporting one would be the exact failure this library exists to
    catch -- and absolute correlation cannot tell the two apart: a mismatched
    pair scores as high as a real one.
    """
    reason = skips(assert_lip_sync, MISMATCHED, **NO_BIAS)
    assert "stood out" in reason, reason


def test_a_held_picture_is_not_judged():
    reason = skips(assert_lip_sync, STILL, **NO_BIAS)
    assert "talking head" in reason or "correlate" in reason, reason


def test_a_slide_deck_is_not_judged():
    reason = skips(assert_lip_sync, SLIDESHOW, **NO_BIAS)
    assert "talking head" in reason, reason


def test_digital_silence_is_not_judged():
    reason = skips(assert_lip_sync, SILENT, **NO_BIAS)
    assert reason


def test_a_short_clip_is_not_judged():
    reason = skips(assert_lip_sync, SHORT, **NO_BIAS)
    assert "short" in reason or "under the" in reason, reason


def test_video_with_no_audio_skips_rather_than_failing():
    reason = skips(assert_lip_sync, NOAUDIO, **NO_BIAS)
    assert "no audio stream" in reason, reason


def test_a_missing_file_raises_rather_than_skipping():
    try:
        assert_lip_sync(FIXTURES / "nope.mp4")
    except FileNotFoundError:
        return
    raise AssertionError("a typo'd path must raise, not skip")


if __name__ == "__main__":
    failures = 0
    for name, test in sorted(dict(globals()).items()):
        if name.startswith("test_") and callable(test):
            try:
                test()
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("all lip sync checks passed" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)
