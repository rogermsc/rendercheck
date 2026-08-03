# Reference

One page, because you will read it once and then only ever come back for a
threshold. For *why each check exists*, see the incidents in the
[README](../README.md).

Every threshold is a keyword argument. The defaults come from spoken-word
video; if your content is different, change them. A number you tuned on your own
output beats one we picked.

---

## `assert_pace(media, script, *, max_wpm=245, min_wpm=110)`

Words in `script` divided by the media's duration.

`script` takes raw text, or a path to a transcript, `.vtt`, or `.srt` — cue
numbers, timestamps and `<v Speaker>` tags are stripped, so you can point it at
the subtitles that already ship next to your audio.

| | |
|---|---|
| **Tune up** | Dense technical narration for an expert audience, or a language whose words are shorter. |
| **Tune down** | Teaching material, non-native audiences, anything with on-screen text to read. Around 150–180 WPM is comfortable for instruction. |
| **Turn off** | Dialogue, interviews, or anything with two speakers — WPM across a conversation is not a meaningful number. |

`min_wpm` catches the opposite defect, and it is usually not a slow reader: an
unexpectedly low rate normally means the audio contains silence the script does
not account for.

## `assert_loudness(media, *, target_lufs=-16.0, tol=2.0)`

Integrated loudness (EBU R128), via ffmpeg's `loudnorm`.

−16 LUFS is the common target for spoken-word web video. Use −14 for music-led
content on streaming platforms, −19 to −23 for broadcast delivery. What matters
far more than the absolute number is that **everything you cut together shares
one target** — a 20 dB step mid-file is what viewers actually notice.

Two distinct failures, deliberately worded differently:

- *digital silence* — there is an audio stream and every sample is zero.
- *no audio stream at all* — the track is missing entirely. Different cause,
  different fix, so they never share a message.

## `assert_duration(media, expected_seconds, *, tol=0.5, min_ratio=0.5)`

Two checks in one, because the two failures deserve different responses:

- Below `min_ratio` of expected → a **broken encode**. Re-render; retrying will
  not help, because nothing errored.
- Within `min_ratio` but outside `tol` → **drift**. Usually harmless alone, but
  it compounds across a concatenated timeline.

Raise `tol` if your pipeline pads segments with variable silence.

## `assert_no_dead_air(media, *, max_silence=3.0, threshold_db=-50.0)`

Longest stretch quieter than `threshold_db`, reported with a timestamp so you
can go straight to it.

`threshold_db` is the one to adjust for recorded audio: real rooms have a noise
floor well above −50 dB, so a recorded track may never register as "silent". For
synthesised speech, −50 is usually right. Raise `max_silence` for material with
deliberate long pauses; lower it to around 1.5s to catch clipped joins.

## `assert_speaker(script, expected, known_names)`

Scans the script for `I'm X` / `I am X` / `My name is X` and compares against
the presenter you assigned.

`known_names` is required and it is the whole trick. Without a roster of people
who could actually have been cast, a character in a scenario saying *"I'm Rosa,
a nurse"* trips the check on every script that tells a story. There is no safe
default, so there isn't one.

Costs nothing, needs no media, and catches a defect that survives every other
gate — worth running the moment a script exists, long before you render.

## `looks_ok(image, rubric, *, model="claude-opus-5", client=None)`

The only check that needs an API key: `pip install "silentfail[vision]"`.

`rubric` is a list of plain-English claims the image must satisfy. Write them as
falsifiable statements about what is visible, not as goals:

```python
# good — a reviewer can point at the pixels that make each of these false
good = ["the title fits on one line", "no text is clipped at any edge"]

# bad — not checkable, will produce noise
bad = ["the slide looks professional", "good use of whitespace"]

looks_ok(slide, good)
```

Critical and major findings raise; minor ones warn. Pass `client=` to supply
your own configured Anthropic client, or a stub in tests.

---

## Fail open, fail closed

The rule the whole library runs on:

- **We measured, and it is wrong** → raise. A defect fails closed.
- **We could not measure** — no ffmpeg, no key, no network, an unreadable file →
  `warnings.warn(..., silentfail.Skipped)` and pass. The checker fails open, so
  it never blocks your pipeline on its own breakage.
- **The file you named does not exist** → `FileNotFoundError`. That is your
  typo, not infrastructure, and silently passing it would defeat the point.

Turn skips into failures if you would rather be strict:

```python
warnings.simplefilter("error", silentfail.Skipped)
```

## Performance

`assert_loudness` and `assert_no_dead_air` share one decode, memoised on the
file's `(path, mtime, size)`. Calling both costs one pass; re-rendering the file
invalidates the entry, so measure → fix → re-measure in a single process reports
the new file rather than a stale reading.

Decoding dominates: roughly 5 seconds for a 3-minute lesson on a laptop. If you
are checking a large batch, run files in parallel — nothing here shares state
across files.
