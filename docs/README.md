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

## `assert_no_truncation(media, *, tail_seconds=0.25, min_drop_db=6.0)`

Whether the audio was allowed to finish. Speech that ends properly tails off,
into a decay or into the small silence a sentence leaves behind; speech that was
cut stops at full level.

Measured against the file's **own average**, not a fixed dBFS line, so it works
on quiet and loud content alike. The separation is wide in practice — an abrupt
cut lands within a decibel of the average, a fade around 16 dB below it, a
normal trailing silence 60 dB or more below. Lower `min_drop_db` for content
that legitimately ends hot: a music bed, or a hard cut into the next scene.

## `assert_no_clipping(media, *, max_clipped_samples=100)`

Samples pinned at full scale, which is what a gain stage somewhere in a TTS
chain leaves behind. Audible as crackle on consonants, and turning the file down
afterwards does not recover what was flattened.

## `assert_has_sound(media)`

The file plays sound at all — no audio stream, or a stream of pure zeroes.
`assert_loudness` catches both of these too; this is the same question without
an opinion about the level, named for the way people arrive at it ("the clip
came back silent").

## `assert_no_black_frames(media, *, max_seconds=1.0)`

Stretches where the picture is entirely black. Generated video truncates to
black rather than erroring: right length, valid container, nothing in the last
third.

## `assert_not_frozen(media, *, max_seconds=3.0)`

Stretches where the picture stops changing — a clip that plays as a still with
sound over it. Every frame is present; every frame is the same frame. Raise
`max_seconds` for content with deliberate held shots.

Both video checks **skip** rather than pass when the file has no video stream.
The detectors report findings, and a filter given nothing to analyse reports no
findings — which would otherwise read as "looked, all clean".

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

The only check that needs an API key: `pip install "rendercheck[vision]"`.

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
  `warnings.warn(..., rendercheck.Skipped)` and pass. The checker fails open, so
  it never blocks your pipeline on its own breakage.
- **The file you named does not exist** → `FileNotFoundError`. That is your
  typo, not infrastructure, and silently passing it would defeat the point.

Turn skips into failures if you would rather be strict:

```python
warnings.simplefilter("error", rendercheck.Skipped)
```

```bash
rendercheck check lesson.mp4 --strict
```

## The command line

```bash
rendercheck demo                      # generate defects and check them
rendercheck check lesson.mp4          # one file
rendercheck check out/                # every media file in a directory
rendercheck check a.mp4 b.wav c.mp4   # several, checked in parallel
```

Directories expand one level, to files with a known media extension. Globs are
your shell's job. Multiple files are checked on a thread pool — decoding is
subprocess-bound, and nothing is shared between files.

Inputs: `--script` (narration text, or a path to a `.vtt`/`.srt`/transcript),
`--presenter` with `--known-names`, `--expect-seconds`, `--rubric` for images.

Thresholds, all matching the Python defaults: `--max-wpm`, `--min-wpm`,
`--target-lufs`, `--loudness-tol`, `--max-silence`, `--silence-threshold`,
`--min-tail-drop`, `--max-clipped`, `--max-black`, `--max-freeze`,
`--duration-tol`, `--min-ratio`.

Behaviour: `--strict`, `--json`, `--version`.

## `--json`

One JSON object per file, on stdout. With several files it is one object per
line (JSONL), so a single-file consumer sees exactly what it always did.

```json
{
  "file": "lesson.mp4",
  "results": [
    {"status": "FAIL", "check": "loudness", "detail": "-34.0 LUFS is 18.0 dB quieter…"},
    {"status": "PASS", "check": "dead air", "detail": "0.0 s silence"},
    {"status": "SKIP", "check": "pace", "detail": "no --script given"}
  ],
  "failed": 1,
  "skipped": 1
}
```

`status` is one of `PASS`, `FAIL`, `SKIP`. `check` is the check's name — note
it is `check`, not `name`, and the key names are a contract other pipelines
parse. `detail` is the failure message for a `FAIL`, the reason for a `SKIP`,
and the measurement for a `PASS`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Something was measured and all of it passed |
| `1` | A defect was measured — or **nothing was**, which is not a pass |
| `2` | A path you supplied does not exist: `file`, or a `--script` that ends in a transcript extension |

The middle row is the one that matters in CI. A run where every check skipped —
no ffmpeg on the runner, no `--script`, an image with no `--rubric` — measured
nothing, and reporting that as green would be the exact failure this library is
named after. `--strict` goes further and rejects *partial* runs, where some
checks ran and others could not.

## Performance

`assert_loudness` and `assert_no_dead_air` share one decode, memoised on the
file's `(path, mtime, size)`. Calling both costs one pass; re-rendering the file
invalidates the entry, so measure → fix → re-measure in a single process reports
the new file rather than a stale reading.

Decoding dominates: roughly 5 seconds for a 3-minute lesson on a laptop. If you
are checking a large batch, run files in parallel — nothing here shares state
across files.
