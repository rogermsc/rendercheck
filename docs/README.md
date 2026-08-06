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

## `assert_true_peak(media, *, max_dbtp=-1.0)`

The highest inter-sample peak, in dBTP. Different from `assert_no_clipping`,
which counts samples already flattened at full scale: true peak is what the
waveform reaches *between* samples, so a file can measure under 0 dBFS
everywhere and still clip once a lossy codec reconstructs it. The distortion
appears on the platform's copy, not on yours.

Read from the same decode as loudness, so it is free once that has run. Off by
default on the command line — there is no universal ceiling — and switched on by
`--max-true-peak`, or by any preset that states one. `--preset web` does not:
it exists to name the built-in defaults, so it has to behave exactly like
passing no preset at all.

## `assert_loudness_range(media, *, max_lra=15.0)`

Off by default on the command line, like true peak, and switched on by
`--max-lra`. Legitimately dynamic material — a music bed, a drama mix, broadcast
content — runs wider than any figure calibrated on speech, and a gate nobody
asked for that starts failing yesterday's files is a regression rather than a
check.

Loudness range in LU — how far the level moves across the programme, which the
integrated figure cannot tell you. A file whose quiet half sits 25 LU under its
loud half still averages out to a respectable number.

**There is deliberately no floor.** Loudness range is gated: EBU R128 discards
blocks more than 10 LU below the ungated level before measuring, so the pauses
between sentences do not count towards it and only the spread *within* speech
does. Consistently-levelled narration therefore reads a legitimate 0.0 LU —
across this library's own demo fixtures the readings are 0.0 to 4.8, and six of
seven would fail a floor of 1.0. That would reject almost every TTS render there
is. Over-compression is a real defect; this is not the measurement that finds it.

The ceiling is set from measurement rather than from a spec: material swung from
near-silence to full scale every thirty seconds reaches 18.1 LU, so 15 sits
between that and anything real. Read from the same decode as loudness.

## `assert_audio_format(media, *, sample_rate=None, channels=None)`

Sample rate and channel count against the delivery spec. Checks only what you
pass, like `assert_format` does for picture, and skips rather than passes when
the container declares neither.

Reads the container only — no decoding — so it costs nothing to leave on.
Presets do **not** set it: a preset governs loudness, and quietly widening an
existing flag's meaning to also assert 48 kHz would change behaviour for
everyone already using one.

## `assert_not_blank(image, *, min_spread=16.0)`

Whether a still has anything on it. Image generators return a blank canvas on
failure far more often than they return an error — the same report against
DALL·E, Stable Diffusion, Qwen, Gemini and Krita, always with no error, no
warning, correct dimensions and nothing drawn.

Measured as the spread between the bottom and top of the luma distribution
(`YLOW` to `YHIGH`), not minimum to maximum. That choice is what makes it hold:
a blank frame carrying a single stray artifact spans the full 16–235 range on
min/max, so a naive version calls it full-contrast content, while the percentile
reading still says 235–235. It catches any flat canvas — white, grey and solid
colour as well as black, none of which `blackdetect` sees.

**Stills only, deliberately.** A *video* holding one flat frame is already
caught by `assert_not_frozen`, whatever colour it is; a still is the only case
with no motion to compare against. An animated `.gif` or `.webp` therefore
**skips** here rather than being judged on its first frame — a clip that opens
on a dark leader is not a blank canvas. Whether a file is a still is read from
the *container* (`png_pipe`, `image2`), never the codec: a `.jpg` and a Matroska
full of motion JPEG both report `mjpeg`.

## `assert_streams_aligned(media, *, max_gap=0.5, max_start_skew=0.25)`

Whether sound and picture cover the same stretch of time. Two defects, one
reading: a mux that ran out of one input leaves audio ending before the picture
does, and a concatenation that mistimed its first segment leaves the streams
starting at different points.

A **container** check, not a perceptual one. It reads what the file declares
about its own streams, in a single `ffprobe` call with no decoding, which makes
it the cheapest thing here. It cannot see lip sync; what it can see is the much
more common case where nothing lines up because the timings never did.

A stream ends at `start + duration`, and the gap is measured between those
endings rather than between the two durations. It matters when the streams are
offset: comparing lengths alone reports a match for a file whose sound genuinely
runs past its picture, and widening `max_start_skew` to tolerate a known
pre-roll would then throw the ending check away without saying so.

Skips when the file has only one of the two, or when the container declares no
per-stream duration — Matroska usually does not. Comparing against a number
invented to fill the gap would be a confident wrong answer.

## `assert_format(media, *, width=None, height=None, fps=None, fps_tol=0.01)`

Checks only what you pass. A generation step that quietly fell back to 720p, or
a render that came out at 25 fps for a 30 fps timeline, produces a valid file
that is wrong everywhere it is used.

Passing `fps` also catches **variable frame rate**: a file whose nominal
(`r_frame_rate`) and average (`avg_frame_rate`) rates disagree plays at a rate
that changes as it goes, which is a standard cause of audio drifting against
picture in an editor that assumes constant rate.

## `assert_captions_aligned(media, captions, *, max_offset=0.75, max_drift=1.0)`

Whether the captions describe the audio they ship next to. Captions are written
against one clock and the audio rendered against another — a concatenation adds
a pre-roll, a segment is re-cut, an editor trims a leading breath — and both
files remain individually perfect. Every other check here passes.

**How it works.** Both sides become a coarse "is anyone talking now" track: the
captions from their cue timings, the audio from where it is not silent
(`silencedetect` at −40 dB). The two are slid against each other at 100 ms
resolution to find the shift that fits best. The same approach [ffsubsync] uses
to *correct* drift, reduced to what a pass/fail needs.

**Offset and drift are separate findings**, and drift is reported first because
it is the more specific one. An offset is a single shift from correct; drift
means the two were timed against different clocks and no shift fixes it. A
drifting file also has an average offset, so testing offset first would report
the symptom and bury the cause.

**It skips when no shift stands out.** Wall-to-wall speech, or a music bed, has
no silence structure to match against, so every shift scores identically — and a
naive implementation would read a confident zero offset off that flat curve and
pass. `SKIP` here means "could not tell", never "fine".

**It refuses to quote a number it did not measure.** Three cases, all of which
produced confident wrong answers before 0.3.1:

- A best fit sitting at the *edge* of the search range means the real offset is
  somewhere past it. Reported as "more than Ns away", not as the edge value.
- Drift is only reported when both the head and the tail window contain cues and
  neither fit saturated. A window with no cues scores zero at every shift, so
  the "best" one is whatever the tie-break picked — and the silence almost every
  file ends on produced exactly that, inventing drift on correct captions.
- Cues running past the end of the media by more than the search range are
  captions for a **different cut**, not a mistimed copy of this one, and are
  named as such. Left alone they clamp into the last bin and yield a small,
  plausible, wrong offset.

Returns the measured offset in seconds; positive means the captions run late.

[ffsubsync]: https://github.com/smacke/ffsubsync

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

Or collect them, which is what the runner does — and what any caller wants who
needs to know that a green result actually measured something:

```python
from rendercheck import collect_skips

with collect_skips() as skipped:
    assert_loudness("lesson.mp4")
    assert_no_dead_air("lesson.mp4")

if skipped:
    print("could not measure:", *skipped, sep="\n  ")
```

Per-thread, unlike `warnings.catch_warnings`, which is process-global and
documented as unsafe under threads.

```bash
rendercheck check lesson.mp4 --strict
```

## The command line

```bash
rendercheck demo                      # generate defects and check them
rendercheck presets                   # loudness targets, by platform
rendercheck check lesson.mp4          # one file
rendercheck check out/                # every media file in a directory
rendercheck check a.mp4 b.wav c.mp4   # several, checked in parallel
rendercheck mcp                       # serve the checks over MCP, on stdio
```

Directories expand one level, to files with a known media extension. Globs are
your shell's job. Multiple files are checked on a thread pool — decoding is
subprocess-bound, and nothing is shared between files.

Inputs: `--script` (narration text, or a path to a `.vtt`/`.srt`/transcript),
`--captions` (found beside the media by default), `--presenter` with
`--known-names`, `--expect-seconds`, `--expect-width`, `--expect-height`,
`--expect-fps`, `--rubric` for images.

Thresholds, all matching the Python defaults: `--max-wpm`, `--min-wpm`,
`--target-lufs`, `--loudness-tol`, `--max-true-peak`, `--max-silence`,
`--silence-threshold`, `--min-tail-drop`, `--max-clipped`, `--max-black`,
`--max-freeze`, `--duration-tol`, `--min-ratio`, `--max-caption-offset`,
`--max-caption-drift`, `--max-stream-gap`, `--max-start-skew`.

Behaviour: `--preset`, `--strict`, `--json`, `--version`.

## Presets and the config file

`--preset` sets `--target-lufs`, `--loudness-tol` and `--max-true-peak` together
from a published platform spec, and `rendercheck presets` prints the table with
its sources. The point is legibility: `--preset ebu` is a decision a reviewer
can read, where `--target-lufs -23` is a magic number nobody will dare touch.

Project defaults go in `rendercheck.toml`, or `[tool.rendercheck]` in
`pyproject.toml`. The search walks upward from the working directory, and a
`pyproject.toml` without a section of ours is skipped rather than treated as an
empty config.

```toml
preset = "podcast"
max_silence = 5.0
max_caption_offset = 0.5
```

Keys are the long-flag names with `--` dropped; hyphens and underscores both
work. **A key that does not exist is reported on stderr, not ignored** — a
typo'd threshold that silently does nothing is exactly the class of bug this
library exists to catch. So is a key of the wrong *shape*: config values are
checked the way argparse checks a flag, because `known_names = "Karl"` is
natural TOML for a one-name roster and, unchecked, becomes the roster
`{k, a, r, l}` — which can never match anyone, so the speaker check prints
`PASS` forever. Positionals cannot be set from the file at all.

Precedence, loosest to tightest: built-in defaults, the config file, `--preset`,
then flags you actually typed. Reading the config needs Python 3.11 (`tomllib`);
on 3.10 it says so and carries on with flags.

## MCP

`rendercheck mcp` serves the checks to a coding agent over stdio. Written
against the wire protocol directly, so it adds no dependency.

```bash
claude mcp add rendercheck -- rendercheck mcp
```

Two tools. `check_media` returns the same `status`/`check`/`detail` records as
`--json`, plus the exit code and a plain-language verdict. It takes:

| argument | turns on |
|---|---|
| `path` (required) | everything applicable to that file; a directory checks what is inside it |
| `script` | `pace`, and `speaker` alongside `presenter` |
| `captions` | `captions` — found automatically if a `.vtt`/`.srt` sits beside the media |
| `expected_seconds` | `duration` |
| `expect_width`, `expect_height`, `expect_fps` | `format` |
| `expect_sample_rate`, `expect_channels` | `audio format` |
| `presenter` + `known_names` | `speaker`. Both, always: a roster holding only the assigned presenter can never report a mismatch |
| `rubric` | `looks ok`, which needs `ANTHROPIC_API_KEY` |
| `preset` | the loudness target, and `true peak` where the platform states a ceiling |
| `strict` | counts a check that could not run as a failure |

`list_checks()` returns every check and every preset, so the model can decide
which arguments are worth supplying. Every check it names is reachable through
the arguments above — a check advertised with no way to invoke it is a check the
model will keep being told about and never manage to run.

The server reads a `rendercheck.toml` above the file being checked, the same way
the CLI does. It exposes no threshold arguments of its own, so that file is the
route to project-specific limits.

A file that *fails its checks* comes back as a normal result with `ok: false` —
that tool ran perfectly, the answer is just no. `isError` is reserved for a tool
that could not run at all, such as a path that does not exist.

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
