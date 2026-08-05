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
from . import captions as _captions
from ._core import SilentFail, existing, skip, timestamp
from ._ffmpeg import ToolUnavailable
from .text import read_cues, read_script


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


def _digital_silence(media: str | Path) -> SilentFail:
    """The one sentence for an audio stream of nothing but zeroes.

    Two checks reach this condition -- loudness and has-sound, since `-inf` LUFS
    is the same reading either way -- and they used to describe it in two
    hand-written sentences that had drifted apart, only one of which said what
    the listener would experience. One defect gets one description.
    """
    return SilentFail(
        f"{media} carries an audio stream but it is digital silence -- every "
        f"sample in it is zero, so it will play as nothing at all, which is "
        f"what a failed synthesis or a muted mix looks like from the outside"
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
        raise _digital_silence(media)
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


def assert_true_peak(media: str | Path, *, max_dbtp: float = -1.0) -> float | None:
    """Highest true peak, in dBTP.

    Distinct from `assert_no_clipping`, which counts samples already flattened
    at full scale. True peak is what the waveform reaches *between* samples: a
    file can measure under 0 dBFS everywhere and still clip once a lossy codec
    reconstructs it, which is why every platform states a ceiling below zero
    rather than at it. This is the check that catches a master which sounds fine
    locally and distorts after upload.

    Read from the same decode as loudness, so it is free once that has run.
    Returns the measured true peak, or None if it could not be measured.
    """
    existing(media)
    try:
        _require_audio(media)
        peak = _ffmpeg.measure(media).true_peak
    except ToolUnavailable as exc:
        skip(f"true peak: {exc}")
        return None
    if peak == float("-inf"):
        # Either digital silence or an ffmpeg too old to print input_tp. Both
        # are "no reading", and neither is evidence the file is within spec.
        skip(f"true peak: no reading from {media}")
        return None
    if peak > max_dbtp:
        raise SilentFail(
            f"{media} peaks at {peak:+.1f} dBTP, above the {max_dbtp:+.1f} "
            f"ceiling -- it measures clean now, but a lossy encode reconstructs "
            f"the waveform between samples and will clip there. The distortion "
            f"appears after upload, on the platform's copy, not on yours"
        )
    return peak


def assert_loudness_range(media: str | Path, *, max_lra: float = 15.0) -> float | None:
    """Loudness range, in LU -- how far the level moves across the programme.

    `assert_loudness` asks where the middle sits. This asks how wide the
    material swings around it, which is a question the integrated figure cannot
    answer: a file whose quiet half is 25 LU under its loud half still averages
    out to a perfectly respectable number.

    **There is deliberately no floor**, and the reason is worth stating because
    it looks like an omission. Loudness range is gated: EBU R128 discards blocks
    more than 10 LU below the ungated level before measuring, so the pauses
    between sentences do not count towards it and only the spread *within
    speech* does. Consistently-levelled narration therefore reads a legitimate
    0.0 LU -- measured across every fixture in this library's own demo, the
    readings are 0.0 to 4.8. A floor would fail almost every TTS render there
    is, which is precisely the material this library exists for. Over-
    compression is a real defect; this is not the measurement that finds it.

    The ceiling is the half that holds, and 15 LU is set from measurement rather
    than from a spec: the widest legitimate reading across this library's demo
    fixtures is 4.8 LU, while material swung deliberately from near-silence to
    full scale every thirty seconds reaches 18.1. The default sits between them,
    close enough to catch a file with no workable volume setting and far enough
    from real narration not to argue with it.

    Read from the same decode as loudness, so it is free once that has run.
    Returns the measured range, or None if it could not be measured.
    """
    existing(media)
    try:
        _require_audio(media)
        measured = _ffmpeg.measure(media).loudness_range
    except ToolUnavailable as exc:
        skip(f"loudness range: {exc}")
        return None
    if measured is None:
        # An ffmpeg too old to print input_lra. Not evidence of anything.
        skip(f"loudness range: no reading from {media}")
        return None

    if measured > max_lra:
        raise SilentFail(
            f"{media} swings {measured:.1f} LU between its quiet and loud parts, "
            f"past the {max_lra:g} limit -- there is no single volume setting "
            f"that works for the whole file. Turned up for the quiet passages, "
            f"the loud ones startle; set for the loud ones, the quiet ones "
            f"vanish on a phone speaker or in a car"
        )
    return measured


def assert_audio_format(
    media: str | Path,
    *,
    sample_rate: int | None = None,
    channels: int | None = None,
) -> None:
    """Sample rate and channel count against what the delivery spec asks for.

    Checks only what you pass, like `assert_format` does for picture. The
    defects are the quiet kind: a mix bounced to mono when the spec says stereo
    plays with the music and voice collapsed on top of each other, and a
    44.1 kHz file delivered where 48 kHz was required gets resampled by whoever
    receives it, on their terms rather than yours.

    Reads the container only -- no decode -- so it costs nothing to leave on.
    """
    existing(media)
    if sample_rate is None and channels is None:
        return
    try:
        _require_audio(media)
        audio = _one(media, "audio")
    except ToolUnavailable as exc:
        skip(f"audio format: {exc}")
        return
    if audio is None:
        skip(f"audio format: {media} declares no audio stream")
        return

    for name, want, got, unit in (
        ("sample rate", sample_rate, audio.sample_rate, " Hz"),
        ("channel count", channels, audio.channels, ""),
    ):
        if want is None:
            continue
        if got is None:
            skip(f"audio format: {media} declares no {name}")
            continue
        if got != want:
            extra = ""
            if name == "channel count":
                extra = (
                    " -- a stereo mix folded to mono loses its separation, and a "
                    "mono source declared as stereo wastes half the file"
                    if got < want
                    else " -- whoever receives it will fold it down on their "
                    "terms rather than yours"
                )
            else:
                extra = (
                    " -- it will be resampled downstream, by whichever converter "
                    "happens to be in the chain"
                )
            raise SilentFail(
                f"{media} has a {name} of {got}{unit}, not the {want}{unit} "
                f"asked for{extra}"
            )


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
        raise _digital_silence(media)
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


def _one(media: str | Path, kind: str) -> _ffmpeg.Stream | None:
    """The first stream of `kind`, or None."""
    return next((s for s in _ffmpeg.streams(media) if s.kind == kind), None)


# Codecs that carry a picture rather than a moving image. Deliberately not an
# extension list: the question is what ffmpeg decoded, not what the file was
# named. `gif` and `webp` are absent because both can be animated -- for those
# the frame count settles it.
_STILL_CODECS = frozenset({"png", "mjpeg", "bmp", "tiff", "ppm", "jpeg2000"})


def _is_still(video: _ffmpeg.Stream) -> bool:
    """Whether this video stream is one frame rather than a sequence.

    Container metadata cannot answer this on its own -- a Matroska of real video
    declares neither a duration nor a frame count, which is exactly what a .png
    declares. Only the codec distinguishes them.
    """
    if video.codec in _STILL_CODECS:
        return video.frames is None or video.frames <= 1
    return False


def assert_streams_aligned(
    media: str | Path, *, max_gap: float = 0.5, max_start_skew: float = 0.25
) -> float | None:
    """Whether sound and picture cover the same stretch of time.

    Two defects, one reading. A mux that ran out of one input leaves a file whose
    audio ends before the picture does -- it plays, it is the right length, and
    the last stretch is silent. A concatenation that mistimed its first segment
    leaves the streams starting at different points, which is the whole file
    running out of sync from the first frame.

    This is a *container* check, not a perceptual one. It reads what the file
    declares about its own streams and costs no decoding at all. It cannot see
    lip sync; what it can see is the far more common case where nothing lines up
    because the timings never did.

    Returns the gap between the two stream endings in seconds, or None if
    unmeasurable.
    """
    existing(media)
    try:
        audio, video = _one(media, "audio"), _one(media, "video")
    except ToolUnavailable as exc:
        skip(f"stream alignment: {exc}")
        return None
    if audio is None or video is None:
        skip(f"stream alignment: {media} has only one of sound and picture")
        return None
    # Matroska and some streamed MP4s carry no per-stream duration. Comparing
    # against a number we invented would be a confident wrong answer, which is
    # worse than saying nothing.
    if audio.length is None or video.length is None:
        skip(f"stream alignment: {media} declares no per-stream duration")
        return None

    if audio.start is not None and video.start is not None:
        skew = audio.start - video.start
        if abs(skew) > max_start_skew:
            late = "sound" if skew > 0 else "picture"
            raise SilentFail(
                f"{media} starts its two streams {abs(skew):.2f}s apart "
                f"({late} is late, past the {max_start_skew:g}s limit) -- the "
                f"file is out of sync from the first frame, and every caption "
                f"and cue timed against it inherits the same offset"
            )

    # A stream ends at start + duration, not at duration. Comparing lengths
    # alone reports a match for a file where one stream is offset and genuinely
    # runs past the other -- and the wider you set max_start_skew to tolerate a
    # known pre-roll, the more of the ending check you silently lose.
    audio_end = (audio.start or 0.0) + audio.length
    video_end = (video.start or 0.0) + video.length
    gap = audio_end - video_end
    if abs(gap) > max_gap:
        short, ends = ("sound", audio_end) if gap < 0 else ("picture", video_end)
        raise SilentFail(
            f"{media} runs {video_end:.1f}s of picture against "
            f"{audio_end:.1f}s of sound -- {short} stops {abs(gap):.1f}s "
            f"early (limit {max_gap:g}s). A mux that ran out of one input "
            f"produces exactly this: a valid file, correct overall length, and "
            f"{ends:.1f}s in, nothing there"
        )
    return gap


def assert_format(
    media: str | Path,
    *,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    fps_tol: float = 0.01,
) -> None:
    """The picture is the shape and rate you asked the renderer for.

    Checks only what you pass. A generation step that quietly fell back to 720p,
    or a render that came out at 25 fps for a 30 fps timeline, produces a
    perfectly valid file that is wrong everywhere it is used downstream.

    Passing `fps` also catches **variable frame rate**: a file whose nominal and
    average rates disagree plays at a rate that changes as it goes, which is one
    of the standard reasons audio drifts against picture in an editor.
    """
    existing(media)
    try:
        video = _one(media, "video")
    except ToolUnavailable as exc:
        skip(f"format: {exc}")
        return
    if video is None:
        skip(f"format: {media} has no video stream")
        return

    if width or height:
        # Skip, not pass. A requested size that nothing was compared against is
        # "could not measure" -- and returning silently here is the one place in
        # this file where that would have read as "measured and fine".
        if not video.width or not video.height:
            skip(f"format: {media} declares no picture dimensions")
            return
        want = (width or video.width, height or video.height)
        if (video.width, video.height) != want:
            raise SilentFail(
                f"{media} is {video.width}x{video.height}, not "
                f"{want[0]}x{want[1]} -- a render that silently fell back to a "
                f"smaller size is upscaled by everything downstream, and the "
                f"softness is blamed on the generator rather than on this"
            )

    if fps is None:
        return
    if _is_still(video):
        # ffprobe describes a still as a video stream and invents a rate for it:
        # a .png reports 25 fps, a .jpg reports 25 fps and a 0.04s duration. Both
        # are ffmpeg's defaults for a file that has no rate at all, so comparing
        # against them raises on a number nobody produced. The CLI never routes a
        # still here; a library caller can, and did.
        skip(f"format: {media} is a still, so it has no frame rate to compare")
        return
    if video.fps is None:
        skip(f"format: {media} declares no frame rate")
        return
    if abs(video.fps - fps) > fps_tol:
        raise SilentFail(
            f"{media} runs at {video.fps:.3f} fps, not {fps:g} -- every cue and "
            f"caption timed in frames lands somewhere else, and the drift grows "
            f"across the file rather than staying put"
        )
    if video.average_fps is not None and abs(video.average_fps - video.fps) > fps_tol:
        raise SilentFail(
            f"{media} declares {video.fps:.3f} fps but averages "
            f"{video.average_fps:.3f} -- the file is variable-rate. Editors and "
            f"muxes that assume a constant rate will drift the audio against the "
            f"picture, further the longer the file runs"
        )


# Speech detection wants a different question than dead-air detection does: a
# lower bar for "quiet" and a much shorter minimum, because the gaps between
# sentences are the structure being matched against. Dead air asks about holes;
# this asks about rhythm.
_SPEECH_THRESHOLD_DB = -40.0
_SPEECH_GAP = 0.3


def assert_captions_aligned(
    media: str | Path,
    captions: str | Path,
    *,
    max_offset: float = 0.75,
    max_drift: float = 1.0,
) -> float | None:
    """Whether the captions describe the audio they ship next to.

    Captions are generated against one clock and the audio rendered against
    another -- a concatenation adds a pre-roll, a segment is re-cut, an editor
    trims a leading breath -- and nothing complains. The file plays, the captions
    display, and every line arrives at the wrong moment. It is invisible to every
    other check here, because both files are individually perfect.

    Works by matching the shape of the two: where the cues say someone is
    talking, against where the audio is not silent. It needs the audio to have
    some silence structure to match against; wall-to-wall speech or a music bed
    gives nothing to line up, and this skips rather than guessing.

    Returns the measured offset in seconds (positive means the captions run
    late), or None if it could not be measured.
    """
    existing(media)
    cues = read_cues(captions)
    if not cues:
        skip(f"captions: no cues found in {captions}")
        return None
    try:
        _require_audio(media)
        length = _ffmpeg.duration(media)
        silences = _ffmpeg.measure(
            media, threshold_db=_SPEECH_THRESHOLD_DB, min_seconds=_SPEECH_GAP
        ).silences
    except ToolUnavailable as exc:
        skip(f"captions: {exc}")
        return None

    # Search comfortably wider than the limit being enforced, so an offset just
    # over the line is measured rather than found at the edge and reported as
    # unknown.
    max_shift = max(5.0, max_offset * 4)

    # Cues outside the media are their own defect, and they have to be caught
    # before the fit rather than after it: the binning clamps anything out of
    # range to the first or last bin, so a caption file for a different cut
    # piles up at one end and produces a confident, small, wrong offset.
    #
    # Measured against the search range, not against the tolerance. Captions
    # that are merely late overhang the end by exactly how late they are, and
    # that is the case this check is *for* -- only an overhang bigger than any
    # offset the fit could explain means a different cut. Only the tail is
    # tested: no caption format can express a time before zero, so cues cannot
    # start early enough to fall off the front.
    overhang = max(e for _, e in cues) - length
    if overhang > max_shift:
        raise SilentFail(
            f"{captions} describes {overhang:.1f}s of {media} that does not "
            f"exist -- its cues run past the end of the file, further than any "
            f"offset could account for. These are captions for a different cut "
            f"of this material, not a mistimed copy of this one"
        )
    fit = _captions.align(cues, silences, length, max_shift=max_shift)
    if fit is None:
        skip(f"captions: nothing to align in {media}")
        return None
    if not fit.distinct:
        # No shift beat the others. Either the audio has no silence to match
        # against, or these captions do not belong to this file at all -- and
        # this check cannot tell those apart, so it reports neither.
        skip(
            f"captions: no alignment stood out for {media} "
            f"({fit.overlap:.0%} of captioned time lands on speech at best) -- "
            f"continuous speech and music beds give nothing to match against"
        )
        return None

    if fit.saturated:
        # The best fit sat at the edge of the search range, so the captions are
        # at least that far out and possibly much further -- or they belong to
        # another file. Quoting the edge value would state a number nobody
        # measured, and the drift arithmetic built on it would be worse.
        raise SilentFail(
            f"{captions} is more than {max_shift:g}s away from the speech in "
            f"{media} -- far enough that the search ran out of room, so the "
            f"real offset is unknown. Either these captions were timed against "
            f"a different cut, or they are not the captions for this file"
        )

    # Drift is reported ahead of offset because it is the more specific finding
    # and the more expensive one to act on: a constant offset is one shift away
    # from correct, while drift means no shift fixes it. A drifting file also
    # always has *some* average offset, so testing offset first would report the
    # symptom and bury the cause.
    if fit.drift is not None and abs(fit.drift) > max_drift:
        raise SilentFail(
            f"{captions} drifts {abs(fit.drift):.1f}s against {media} between "
            f"its start and its end, past the {max_drift:g}s limit -- the two "
            f"were timed against different clocks, so no single offset "
            f"correction fixes this. Re-generate the captions from the audio "
            f"that actually shipped"
        )
    if abs(fit.offset) > max_offset:
        late = "late" if fit.offset > 0 else "early"
        raise SilentFail(
            f"{captions} runs {abs(fit.offset):.1f}s {late} against {media}, "
            f"past the {max_offset:g}s limit -- every line arrives at the wrong "
            f"moment, and both files are individually valid so nothing else "
            f"catches it"
        )
    return fit.offset


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


def assert_not_blank(image: str | Path, *, min_spread: float = 16.0) -> float | None:
    """Whether a still has anything on it.

    Image generators return a blank canvas on failure far more often than they
    return an error. Solid black is the usual shape of it -- a NaN in the VAE, a
    sampler misconfigured, half precision on hardware that cannot hold it -- and
    every report of it says the same thing: no error, no warning, a valid PNG of
    the right dimensions containing nothing. A slide deck built from those is a
    deck of blank slides that passes every other check in this library.

    Measured as the spread between the bottom and top of the luma distribution,
    which is what makes it robust: a blank frame carrying one stray artifact
    still reads as blank, where a min-to-max reading would call it full-range.
    It catches any flat canvas, not only a black one -- white, grey and solid
    colour all read the same, and `blackdetect` sees none of them.

    Returns the measured spread, or None if it could not be measured.

    ponytail: stills only. A *video* that holds one flat frame is already caught
    by `assert_not_frozen`, whatever colour it is -- a still is the only case
    with no motion to compare against. Sample across frames here only if some
    file turns up that neither check sees.
    """
    existing(image)
    try:
        picture = _ffmpeg.signalstats(image)
    except ToolUnavailable as exc:
        skip(f"blank: {exc}")
        return None

    spread = picture.high - picture.low
    if spread < min_spread:
        shade = (
            "solid black"
            if picture.high < 32
            else "solid white"
            if picture.low > 200
            else f"one flat tone at luma {picture.low:.0f}"
        )
        raise SilentFail(
            f"{image} is a blank canvas -- its luma spans {spread:.0f} levels "
            f"(under the {min_spread:g} floor), which is {shade} with nothing "
            f"drawn on it. A generator that failed and returned an empty frame "
            f"produces exactly this: correct dimensions, valid file, no error, "
            f"and nothing on the slide"
        )
    return spread


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
