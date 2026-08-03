"""Deterministic checks on generated audio and video.

No model, no API key, no network. These are the checks that catch the defects
a test suite cannot see because nothing throws: the narration is too fast, the
voice track is inaudible next to the avatar, the segment rendered at 40% length,
the composite left a hole in the middle of the lesson.

Every default here is a threshold that was set by a real defect, not chosen for
symmetry. Change them freely -- they are arguments, not constants.
"""

from . import _ffmpeg
from ._core import SilentFail, existing, skip, timestamp
from ._ffmpeg import ToolUnavailable
from .text import read_script


def assert_pace(media, script, *, max_wpm=245.0, min_wpm=110.0) -> float | None:
    """Narration speed, in words per minute.

    The incident: a voice chosen to match a presenter's *face* narrated English
    at ~280 WPM. Every other gate passed -- the audio was valid, the right
    length, and perfectly in sync. It just sounded like a machine gun, and the
    only detector was a human listening to a finished module.

    `script` is raw text, or a path to a transcript, .vtt, or .srt.
    Returns the measured WPM, or None if it could not be measured.
    """
    existing(media)
    words = len(read_script(script).split())
    if not words:
        skip(f"pace: no words found in the script for {media}")
        return None
    try:
        seconds = _ffmpeg.duration(media)
    except ToolUnavailable as exc:
        skip(f"pace: {exc}")
        return None
    if seconds <= 0:
        skip(f"pace: {media} reports zero duration")
        return None

    wpm = words / (seconds / 60)
    if wpm > max_wpm:
        raise SilentFail(
            f"narration pace {wpm:.0f} WPM exceeds {max_wpm:g} "
            f"({words} words in {seconds:.1f}s) -- this reads as machine-gun "
            f"delivery and listeners cannot follow it: {media}"
        )
    if wpm < min_wpm:
        raise SilentFail(
            f"narration pace {wpm:.0f} WPM is below {min_wpm:g} "
            f"({words} words in {seconds:.1f}s) -- this drags, and usually means "
            f"the audio contains silence the script does not account for: {media}"
        )
    return wpm


def assert_loudness(media, *, target_lufs=-16.0, tol=2.0) -> float | None:
    """Integrated loudness, in LUFS.

    The incident: synthesised narration landed at -34 LUFS and was concatenated
    with avatar footage at -13. Same file, same lesson, a 20 dB step in the
    middle. Nothing failed; viewers just rode the volume knob for 45 minutes.

    -16 LUFS is the usual target for spoken-word web video. Returns the
    measured loudness, or None if it could not be measured.
    """
    existing(media)
    try:
        measured = _ffmpeg.loudness(media)
    except ToolUnavailable as exc:
        skip(f"loudness: {exc}")
        return None

    if measured == float("-inf"):
        raise SilentFail(
            f"{media} is digital silence -- there is no audio in it at all, "
            f"which is what a failed mux or a dropped audio stream looks like"
        )
    delta = measured - target_lufs
    if abs(delta) > tol:
        direction = "quieter than" if delta < 0 else "louder than"
        perceived = "inaudible" if delta < 0 else "blaring"
        raise SilentFail(
            f"{measured:.1f} LUFS is {abs(delta):.1f} dB {direction} the "
            f"{target_lufs:g} target -- it will sound {perceived} next to "
            f"correctly-levelled audio cut alongside it: {media}"
        )
    return measured


def assert_duration(media, expected_seconds, *, tol=0.5, min_ratio=0.5) -> float | None:
    """Actual length against what was expected.

    The incident: encode failures produced segments a fraction of their intended
    length, which the pipeline then cached as *successful*. The truncation
    survived every retry, because retries only re-ran the segments that had
    errored -- and these had not.

    `min_ratio` separates the two cases: a badly-short render is a broken
    encode, not a short take, and deserves a different message.
    Returns the measured duration, or None if it could not be measured.
    """
    existing(media)
    if expected_seconds <= 0:
        raise ValueError(f"expected_seconds must be positive, got {expected_seconds}")
    try:
        actual = _ffmpeg.duration(media)
    except ToolUnavailable as exc:
        skip(f"duration: {exc}")
        return None

    ratio = actual / expected_seconds
    if ratio < min_ratio:
        raise SilentFail(
            f"{media} is {actual:.1f}s -- {ratio:.0%} of the expected "
            f"{expected_seconds:.1f}s. A render this short is a silent encode "
            f"failure, not a short take; re-render rather than retry"
        )
    if abs(actual - expected_seconds) > tol:
        raise SilentFail(
            f"{media} is {actual:.1f}s, expected {expected_seconds:.1f}s "
            f"(off by {actual - expected_seconds:+.1f}s) -- audio and slides "
            f"will drift out of sync downstream"
        )
    return actual


def assert_no_dead_air(media, *, max_silence=3.0, threshold_db=-50.0) -> float | None:
    """Longest silent stretch, in seconds.

    The incident: compositing steps failed transiently *and silently*, leaving
    long dead stretches mid-lesson. The file was the right length and the right
    loudness on average -- the hole only existed in the middle.

    Returns the longest silence found (0.0 if none), or None if unmeasurable.
    """
    existing(media)
    try:
        gaps = _ffmpeg.silences(media, threshold_db, max_silence)
    except ToolUnavailable as exc:
        skip(f"dead air: {exc}")
        return None
    if not gaps:
        return 0.0

    start, end = max(gaps, key=lambda gap: gap[1] - gap[0])
    length = end - start
    raise SilentFail(
        f"{length:.1f}s of silence starting at {timestamp(start)} exceeds the "
        f"{max_silence:g}s limit -- a gap this long mid-file is a dropped "
        f"segment, not a pause ({len(gaps)} found in total): {media}"
    )
