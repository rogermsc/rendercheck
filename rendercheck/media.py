"""Deterministic checks on generated audio and video.

No model, no API key, no network. These catch the defects a test suite cannot
see because nothing throws: the narration is too fast, the voice track is
inaudible next to the footage it's cut against, the segment rendered at 40%
length, the composite left a hole in the middle.

Every default here was set by a real defect, not chosen for symmetry. They are
arguments, not constants -- change them freely for your content.
"""

from __future__ import annotations

from pathlib import Path

from . import _ffmpeg
from ._core import SilentFail, existing, skip, timestamp
from ._ffmpeg import ToolUnavailable
from .text import read_script


def _require_audio(media: str | Path) -> None:
    """Fail if the file carries no audio stream.

    Note this fails *closed* while most trouble here fails open. The difference
    is that "there is no audio stream" is a measurement, not a failure to
    measure -- we asked the file and it answered. Failing open is for when we
    cannot tell.
    """
    if not _ffmpeg.has_audio(media):
        raise SilentFail(
            f"{media} has no audio stream at all -- this is not silence, it is a "
            f"missing track, and it usually means a mux step dropped the audio or "
            f"was never given any. Nothing downstream will play sound"
        )


def assert_pace(
    media: str | Path,
    script: str | Path,
    *,
    max_wpm: float = 245.0,
    min_wpm: float = 110.0,
) -> float | None:
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
        if not _ffmpeg.has_audio(media):
            skip(f"pace: {media} has no audio stream -- there is no delivery to time")
            return None
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


def assert_loudness(
    media: str | Path, *, target_lufs: float = -16.0, tol: float = 2.0
) -> float | None:
    """Integrated loudness, in LUFS.

    The incident: synthesised narration landed at -34 LUFS and was concatenated
    with footage at -13. Same file, same lesson, a 20 dB step in the middle.
    Nothing failed; viewers just rode the volume knob for 45 minutes.

    -16 LUFS is the usual target for spoken-word web video. Returns the
    measured loudness, or None if it could not be measured.
    """
    existing(media)
    try:
        _require_audio(media)
        measured = _ffmpeg.measure(media).loudness
    except ToolUnavailable as exc:
        skip(f"loudness: {exc}")
        return None

    if measured == float("-inf"):
        raise SilentFail(
            f"{media} carries an audio stream but it is digital silence -- "
            f"every sample is zero, which is what a failed render or a muted "
            f"mix looks like"
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


def assert_duration(
    media: str | Path,
    expected_seconds: float,
    *,
    tol: float = 0.5,
    min_ratio: float = 0.5,
) -> float | None:
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
            f"(off by {actual - expected_seconds:+.1f}s) -- audio and picture "
            f"will drift out of sync downstream"
        )
    return actual


def assert_no_dead_air(
    media: str | Path, *, max_silence: float = 3.0, threshold_db: float = -50.0
) -> float | None:
    """Longest silent stretch, in seconds.

    The incident: compositing steps failed transiently *and silently*, leaving
    long dead stretches mid-lesson. The file was the right length and the right
    loudness on average -- the hole only existed in the middle.

    Returns the longest silence found (0.0 if none), or None if unmeasurable.
    """
    existing(media)
    try:
        _require_audio(media)
        gaps = _ffmpeg.measure(
            media, threshold_db=threshold_db, min_seconds=max_silence
        ).silences
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


def assert_has_sound(media: str | Path) -> float | None:
    """The file plays sound at all.

    Named separately because it is the defect people arrive searching for: a
    generated clip that returns success and plays in silence. Upscaling steps
    drop the audio track; muxes get pointed at the wrong stream; a failed
    synthesis writes zeroes. `assert_loudness` catches both of these too -- this
    is the same question without an opinion about the level.

    Returns the measured loudness in LUFS, or None if unmeasurable.
    """
    existing(media)
    try:
        _require_audio(media)
        loudness = _ffmpeg.measure(media).loudness
    except ToolUnavailable as exc:
        skip(f"has sound: {exc}")
        return None
    if loudness == float("-inf"):
        raise SilentFail(
            f"{media} carries an audio stream but every sample in it is zero -- "
            f"it will play as silence, which is what a failed synthesis or a "
            f"muted mix looks like from the outside"
        )
    return loudness


def assert_no_truncation(
    media: str | Path, *, tail_seconds: float = 0.25, min_drop_db: float = 6.0
) -> float | None:
    """Whether the audio was allowed to finish.

    The most reported defect in generated speech: the final sentence is missing
    and the API reported success anyway. Speech that finishes tails off -- into
    a decay, or into the small silence a sentence end leaves behind. Speech that
    was cut stops at full level.

    Measured against the file's *own* average rather than a fixed dBFS line, so
    it holds for quiet content and loud content alike. On real files the
    separation is wide: an abrupt cut lands within a decibel of the average,
    while a normal ending sits tens of decibels below it.

    Returns how far the ending fell below the file's average, in dB (larger is
    healthier), or None if unmeasurable. Lower `min_drop_db` for content that
    legitimately ends hot, like a music bed or a hard cut to the next scene.
    """
    existing(media)
    try:
        _require_audio(media)
        tail = _ffmpeg.tail_level(media, tail_seconds)
        overall = _ffmpeg.volume(media).mean
    except ToolUnavailable as exc:
        skip(f"truncation: {exc}")
        return None
    drop = overall - tail
    if drop < min_drop_db:
        raise SilentFail(
            f"{media} is still at its full average level {drop:.1f} dB into the "
            f"final {tail_seconds:g}s (expected at least {min_drop_db:g} dB of "
            f"fall-off) -- audio that stops flat was cut rather than finished, "
            f"which is what a dropped last sentence looks like from outside"
        )
    return drop


def assert_no_clipping(
    media: str | Path, *, max_clipped_samples: int = 100
) -> int | None:
    """Samples pinned at full scale.

    A gain stage somewhere in a TTS chain -- normalisation, a mix, a naive
    volume bump -- pushes the waveform past what the format can represent. The
    result is audible as crackle on consonants, and no amount of turning it
    down afterwards recovers what was flattened.

    Returns the number of clipped samples, or None if unmeasurable.
    """
    existing(media)
    try:
        _require_audio(media)
        clipped = _ffmpeg.volume(media).clipped
    except ToolUnavailable as exc:
        skip(f"clipping: {exc}")
        return None
    if clipped > max_clipped_samples:
        raise SilentFail(
            f"{clipped} samples in {media} are pinned at 0 dBFS, past the "
            f"{max_clipped_samples} allowed -- the waveform was flattened where "
            f"it clipped, which crackles on consonants and cannot be undone by "
            f"lowering the level afterwards"
        )
    return clipped


def _require_video(media: str | Path, check: str) -> bool:
    """Whether there is a picture to analyse. Skips (fails open) when there is not.

    A file with no video stream is not evidence of a broken render -- it may
    simply be audio. What matters is that the detectors are never *asked* about
    a stream that isn't there: `blackdetect` on a .wav reports nothing, and
    nothing would read as "no black frames found".
    """
    if not _ffmpeg.has_video(media):
        skip(f"{check}: {media} has no video stream -- nothing to look at")
        return False
    return True


def assert_no_black_frames(
    media: str | Path, *, max_seconds: float = 1.0
) -> float | None:
    """Stretches where the picture is entirely black.

    Generated video truncates to black rather than erroring: the clip is the
    right length, the container is valid, and the last third is nothing.

    Returns the longest black stretch in seconds (0.0 if none), or None if
    unmeasurable.
    """
    existing(media)
    try:
        if not _require_video(media, "black frames"):
            return None
        blacks = _ffmpeg.measure_video(media, min_black=max_seconds).blacks
    except ToolUnavailable as exc:
        skip(f"black frames: {exc}")
        return None
    if not blacks:
        return 0.0

    start, length = max(blacks, key=lambda black: black[1])
    raise SilentFail(
        f"{length:.1f}s of solid black starting at {timestamp(start)} exceeds "
        f"the {max_seconds:g}s limit -- a stretch this long is a render that "
        f"stopped producing picture, not a transition ({len(blacks)} found in "
        f"total): {media}"
    )


def assert_not_frozen(media: str | Path, *, max_seconds: float = 3.0) -> float | None:
    """Stretches where the picture stops changing.

    A frozen clip plays as a still photograph with sound over it. Nothing errors:
    the frames are all there, and every one of them is the same frame.

    Returns the longest frozen stretch in seconds (0.0 if none), or None if
    unmeasurable. Raise `max_seconds` for content with legitimate held shots.
    """
    existing(media)
    try:
        if not _require_video(media, "frozen"):
            return None
        freezes = _ffmpeg.measure_video(media, min_freeze=max_seconds).freezes
    except ToolUnavailable as exc:
        skip(f"frozen: {exc}")
        return None
    if not freezes:
        return 0.0

    start, length = max(freezes, key=lambda freeze: freeze[1])
    raise SilentFail(
        f"the picture stops moving for {length:.1f}s at {timestamp(start)}, past "
        f"the {max_seconds:g}s limit -- a generated clip that freezes is a failed "
        f"render playing as a still, and it will not look intentional: {media}"
    )
