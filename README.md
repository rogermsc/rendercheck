# rendercheck

<!-- mcp-name: io.github.rogermsc/rendercheck -->
<!--
  The line above is not decoration. The MCP registry proves who owns a PyPI
  package by fetching its README and looking for that marker, so it has to ship
  inside the published long_description. Removing it un-verifies the registry
  listing on the next release. See server.json.
-->

[![PyPI](https://img.shields.io/pypi/v/rendercheck)](https://pypi.org/project/rendercheck/)
[![CI](https://github.com/rogermsc/rendercheck/actions/workflows/ci.yml/badge.svg)](https://github.com/rogermsc/rendercheck/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.14-blue)](https://github.com/rogermsc/rendercheck)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**The worst bugs in generated media don't throw.**

> "the audio often cuts off the final sentence […] though the API returns
> success without error signals"
>
> — a developer on the [OpenAI forum](https://community.openai.com/t/1379584),
> April 2026, describing production output

![rendercheck demo](docs/demo.gif)

If you generate speech or video with a model — TTS, voice agents, podcasts,
avatars, AI video — your tests catch the exception that never happens. They do
not catch the narration that reads at 300 words per minute, the voice track
sitting 18 dB below the footage it's cut against, the clip that rendered at 42%
length and got cached as a success, the captions that describe the audio three
seconds before it happens, or the file whose audio track is missing entirely.

The 2026 state of the art for catching these is *a person listening to the
output*. That works, and it costs more than everything else in your pipeline
combined.

`rendercheck` makes them throw.

```python
from rendercheck import assert_pace, assert_loudness, looks_ok

assert_pace("episode-12.mp3", "episode-12.vtt")
assert_loudness("episode-12.mp3")
looks_ok("slide-14.png", ["the title fits on one line"])
```

Plain assert functions. No framework, no runner, no service. They raise
`AssertionError`, so they already work in pytest, in CI, or in a five-line
script. Fourteen of the fifteen checks have **no dependencies and make no
network calls** — if you have `ffmpeg`, you're ready.

---

## Quickstart

You need `ffmpeg` on your PATH (`brew install ffmpeg`, `apt-get install ffmpeg`,
or `winget install ffmpeg`). Then:

```bash
pip install rendercheck
rendercheck demo
```

Or drop a file into the **[playground](https://rogermsc.github.io/rendercheck/playground/)**
— same checks, running on ffmpeg compiled to WebAssembly, nothing uploaded.

`demo` synthesises eight defective files and runs the real checks against them,
so you can see it fire without owning a broken render. Verbatim, first two of
eight:

```
Narration too fast
  A voice picked to match a presenter's face read English at machine-gun speed. Valid audio, correct timing, perfectly in sync.

  $ rendercheck check machine-gun.wav --script narration.vtt

  FAIL  pace        narration pace 300 WPM exceeds 245 (300 words in 60.0s) -- this reads as machine-gun delivery and listeners cannot follow it: machine-gun.wav
  PASS  loudness    -16.1 LUFS
  PASS  dead air    0.0 s silence
  PASS  truncation  8.9 dB of fall-off at the end
  PASS  clipping    0 samples at 0 dBFS

Levels that don't match
  Synthesised narration landed 18 dB under the footage it was cut against. Nobody noticed until viewers rode the volume knob.

  $ rendercheck check too-quiet.wav

  SKIP  pace        no --script given
  FAIL  loudness    -34.2 LUFS is 18.2 dB quieter than the -16 target -- it will sound inaudible next to correctly-levelled audio cut alongside it: too-quiet.wav
  PASS  dead air    0.0 s silence
  PASS  truncation  8.8 dB of fall-off at the end
  PASS  clipping    0 samples at 0 dBFS
```

…and one of the two added in 0.3.0:

```
Captions against the wrong clock
  A concatenation added three seconds of pre-roll after the captions were written. Both files are perfectly valid on their own.

  $ rendercheck check late-captions.wav

  SKIP  pace        no --script given
  PASS  loudness    -16.0 LUFS
  PASS  dead air    0.0 s silence
  PASS  truncation  74.3 dB of fall-off at the end
  PASS  clipping    0 samples at 0 dBFS
  FAIL  captions    late-captions.vtt runs 3.0s late against late-captions.wav, past the 0.75s limit -- every line arrives at the wrong moment, and both files are individually valid so nothing else catches it
```

Then point it at your own output:

```bash
rendercheck check episode-12.mp3 --script episode-12.vtt --preset podcast
```

Exit code is 1 if anything failed — **or if nothing could be measured**, because
a run that looked at nothing is not a clean one. A path you typo'd exits 2.
`--json` gives you the same report for pipelines in any language, and `--strict`
rejects partial runs too.

## Where is this file going?

"How loud should this be?" has no single answer — it depends entirely on where
the file ends up, and every platform publishes a different number. `--preset`
turns that table into something a build can enforce:

```
$ rendercheck presets

  preset    target     tol     peak  source
  youtube     -14L   1.0dB   -1.0TP  YouTube normalises playback to -14 LUFS
  spotify     -14L   1.0dB   -1.0TP  Spotify, including podcasts, at -14 LUFS
  tiktok      -14L   1.5dB   -1.0TP  TikTok and Instagram, measured rather than published
  podcast     -16L   1.0dB   -1.0TP  AES71 / Apple Podcasts: -16 LUFS stereo, -19 mono
  apple       -16L   1.0dB   -1.0TP  Apple Music Sound Check, -16 LUFS
  web         -16L   2.0dB       --  spoken-word web video -- rendercheck's own defaults
  ebu         -23L   1.0dB   -1.0TP  EBU R128, European broadcast
  atsc        -24L   2.0dB   -2.0TP  ATSC A/85, North American broadcast
  netflix     -27L   2.0dB   -2.0TP  Netflix delivery, dialog-gated
```

None of those numbers are ours. The contribution is that `--preset ebu` is a
decision a reviewer can read, where `--target-lufs -23` is a magic number the
next person will not dare touch. A preset that states a ceiling also switches on
the **true-peak** check, which catches a master measuring clean locally and
distorting after upload. `web` exists only to *name* the built-in defaults, so
it states none and behaves exactly like passing no preset at all.

Project-wide settings go in `rendercheck.toml` (or `[tool.rendercheck]` in
`pyproject.toml`) so a CI step is not eight flags on one line:

```toml
preset = "podcast"
max_silence = 5.0
```

Flags you type still beat the file, and the file beats the built-in defaults.

In pytest they're just asserts — no plugin, no fixtures:

```python
@pytest.mark.parametrize("episode", EPISODES)
def test_episode_is_shippable(episode):
    assert_pace(episode.audio, episode.vtt)
    assert_loudness(episode.audio)
    assert_no_dead_air(episode.audio)
```

## "Isn't this forty lines of pyloudnorm?"

For one of the eleven checks, roughly yes. None of these measurements are novel,
and it would be dishonest to imply otherwise:

| The measurement | Already available from |
|---|---|
| Integrated loudness, true peak | [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm), ffmpeg's `loudnorm` |
| Silence detection | [pydub](https://github.com/jiaaro/pydub)`.silence`, ffmpeg's `silencedetect` |
| Duration, stream layout, frame rate | `ffprobe` |
| Black frames, freezes | ffmpeg's `blackdetect`, `freezedetect` |
| Caption↔audio offset | [ffsubsync](https://github.com/smacke/ffsubsync) — which *corrects* it |
| Container and codec conformance | [MediaConch](https://mediaarea.net/MediaConch) — policy-driven, pass/fail, from the CLI |
| Speaker identity | [resemblyzer](https://github.com/resemble-ai/resemblyzer) |
| Video quality metrics | [VMAF](https://github.com/Netflix/vmaf), [ffmpeg-quality-metrics](https://github.com/slhck/ffmpeg-quality-metrics) |

Most of those hand a **number to a researcher**. The two that are already gates
gate a different thing: MediaConch checks that a file conforms to a container
policy, which is a preservation question, not a perceptual one — a file can pass
every MediaConch rule and still be narrated at 300 WPM. ffsubsync will happily
realign captions that were never wrong, because it has no opinion about whether
they needed it.

What is actually missing, and what this is:

- **A threshold that came from a defect**, not from a paper. 245 WPM because a
  real voice narrated at 280 and shipped. −16 LUFS because narration landed at
  −34 against footage at −13.
- **A message that says what a person would notice.** "−34.0 LUFS" is a reading.
  "18.0 dB quieter than the −16 target — it will sound inaudible next to
  correctly-levelled audio cut alongside it" is a bug report.
- **Fail-open on infrastructure, fail-closed on a defect**, so it can sit in CI
  without becoming the thing that breaks the build for its own reasons.
- **Exit codes and one command over a directory**, rather than a notebook.

Against the LLM-eval tools the difference is structural rather than a matter of
coverage. [promptfoo](https://github.com/promptfoo/promptfoo),
[DeepEval](https://github.com/confident-ai/deepeval) and
[RAGAS](https://github.com/vibrantlabsai/ragas) are excellent and none of them
can do this: their test case is a **string**. There is no assertion to add,
because there is nowhere to put the file. Use them for the script; use this for
what the script turned into.

And if you already run broadcast QC — Interra BATON, Telestream Vidchecker,
QCTools — you have had most of this for twenty years. It just isn't in your git
hooks.

## In your pipeline

**GitHub Actions** — installs ffmpeg and fails the build on a defect:

```yaml
- uses: rogermsc/rendercheck@v0
  with:
    files: out/
    preset: podcast
    strict: "true"
```

**Node, Remotion, anything that renders in a build step:**

```bash
npx rendercheck check out/
```

**Docker**, if you would rather not have a Python toolchain at all — ffmpeg is
already in the image:

```bash
docker run --rm -v "$PWD:/work" ghcr.io/rogermsc/rendercheck check /work/out.mp4
```

**Coding agents**, via MCP. An agent that just wrote a render pipeline and ran
it has no way to tell whether the file that came back is any good; the other
media MCP servers cut and transcode, which hands it *more* media rather than an
answer:

```bash
claude mcp add rendercheck -- rendercheck mcp
```

It is listed in the [MCP registry](https://registry.modelcontextprotocol.io)
as `io.github.rogermsc/rendercheck`, so clients that read the registry can
install it without being told where it lives. No key is needed — every check
except `looks ok` is deterministic.

`check_media` returns one verdict per check with the measured value, so the
model can act on "−34 LUFS, 18 dB under target" rather than on a file it cannot
hear.

**promptfoo** — its assertions are all string-shaped, so an eval can confirm the
narration script and tell you nothing about the audio. `examples/promptfoo/`
closes that half in thirty lines:

```yaml
assert:
  - type: python
    value: file://rendercheck_assert.py:get_assert
```

**Anything else** — `--json` on stdout, one object per file, plus exit codes.

---

## Six checks, six incidents

Each default is a threshold set by a defect that actually shipped, not a number
chosen for symmetry. All output below is verbatim. Every threshold is an
argument — see the [reference](docs/README.md) for tuning.

**Narration too fast.** A voice picked to match a presenter's *face* narrated
English at ~280 WPM. The audio was valid, correctly timed, perfectly in sync. It
just sounded like a machine gun.

```python
assert_pace("episode-12.mp3", "episode-12.vtt", max_wpm=245)
```
```
narration pace 300 WPM exceeds 245 (300 words in 60.0s) -- this reads as
machine-gun delivery and listeners cannot follow it: episode-12.mp3
```

**Levels that don't match.** Synthesised narration landed at −34 LUFS and was
concatenated with footage at −13. Same file, a 20 dB step in the middle. Nobody
noticed until viewers spent 45 minutes riding the volume knob.

```python
assert_loudness("episode-12.mp3", target_lufs=-16, tol=2.0)
```
```
-34.0 LUFS is 18.0 dB quieter than the -16 target -- it will sound inaudible
next to correctly-levelled audio cut alongside it: episode-12.mp3
```

**Truncated renders cached as successes.** Encode failures produced clips a
fraction of their intended length, which the pipeline cached as *succeeded*.
Retries only re-ran the ones that had **errored** — and these hadn't.

```python
assert_duration("segment-07.mp4", expected_seconds=24.0)
```
```
segment-07.mp4 is 10.0s -- 42% of the expected 24.0s. A render this short is a
silent encode failure, not a short take; re-render rather than retry
```

**Holes in the middle.** Compositing failed transiently *and silently*, leaving
dead stretches mid-file. Right length, right average loudness. The hole only
existed in the middle.

```python
assert_no_dead_air("episode-12.mp3", max_silence=3.0)
```
```
6.2s of silence starting at 0:41 exceeds the 3s limit -- a gap this long
mid-file is a dropped segment, not a pause (2 found in total): episode-12.mp3
```

**The wrong person speaking.** A script said "I'm Jordan" while the system had
assigned Alex. A whole module rendered with the wrong face and the wrong voice.
**Every other gate passed.**

```python
assert_speaker(script, expected="Alex", known_names=["Alex", "Jordan", "Sam"])
```
```
the script introduces the presenter as "Jordan" but Alex is assigned -- the
rendered avatar would introduce itself with someone else's name. Fix whichever
is wrong: the assigned presenter, or the name in the script
```

The `known_names` roster is required, and it's the whole trick: without it, a
character in a scenario saying *"I'm Rosa, a nurse"* trips the check on every
script that tells a story. Only a name belonging to someone who could actually
have been cast counts as a claim about the speaker.

**Things you can only see.** Overflowing titles, colliding logos, half-empty
canvases, figures cropped mid-caption. All rendered without error.

```python
looks_ok("slide-14.png", ["the title fits on one line", "no text is clipped"])
```
```
[major] slide-14.png: the title wraps to three lines and overlaps the logo in
the top-right corner -- failed rubric item: 'the title fits on one line'
```

This is the only check that needs a key: `pip install "rendercheck[vision]"`.

## Nine more, for defects other people keep reporting

The six above came out of one pipeline. These came from reading other people's
bug reports — the same complaint, filed against every provider in turn:

| Check | The defect |
|---|---|
| `assert_no_truncation` | Speech that stops mid-sentence while the API returns success. The single most-reported defect in generated audio; measured against the file's own average, so it holds for quiet and loud content alike. |
| `assert_has_sound` | A clip that comes back silent — an upscale step drops the audio track, a mux points at the wrong stream, a synthesis writes zeroes. |
| `assert_no_clipping` | A gain stage pushed the waveform past full scale. Crackles on consonants, and turning it down afterwards does not undo it. |
| `assert_true_peak` | Measures clean locally, distorts after upload. Loudness and peak are different problems: a lossy codec reconstructs the waveform *between* samples, and clips where it goes over. |
| `assert_no_black_frames` | Generated video truncating to black instead of erroring: right length, valid container, nothing in the last third. |
| `assert_not_frozen` | The picture stops moving. Every frame present, every frame the same frame. |
| `assert_captions_aligned` | Captions written against one clock, audio rendered against another. Every line arrives at the wrong moment, and both files are individually perfect. |
| `assert_streams_aligned` | Sound and picture that do not cover the same stretch of time — a mux that ran out of one input, or a concatenation that mistimed its first segment. |
| `assert_format` | A render that quietly fell back to 720p, came out at the wrong frame rate, or is variable-rate where the pipeline downstream assumes constant. |

**The caption check is the one with no equivalent anywhere.** [ffsubsync] and
friends *correct* drift; the online validators lint the `.srt` on its own —
overlapping cues, reading speed, empty rows. Neither asks whether this caption
file matches this audio, which is the only question a build needs answered. It
works by matching the shape of the talking against the shape of the cues, and it
reports a constant offset and a *drift* separately: an offset is one shift from
correct, and drift is not fixable by any single shift.

Every one of these **skips rather than passes** when it cannot measure — no
video stream, no silence structure to align against, no per-stream duration in
the container. `blackdetect` on a `.wav` reports nothing, and nothing would
otherwise read as "looked, all clean". That is the same trap as the regression
below, and it is the reason a run where everything skipped exits non-zero.

[ffsubsync]: https://github.com/smacke/ffsubsync

---

## Found on real files

Not a synthetic benchmark. Pointed at the output of a production pipeline that
renders narrated video at scale:

| | result |
|---|---|
| A course known to be good | clean — 162 WPM, −14.1 LUFS, no dead air |
| Content re-rendered *after* a loudness fix landed | **passes** at −14.2 LUFS |
| Four episodes rendered *before* that fix | **fails** at −19.4 to −21.3 LUFS |

It drew the line exactly where the fix landed, on files it was never told
anything about, agreeing with a conclusion humans had reached months earlier —
and produced no false positives across the clean set.

## Two promises

**It fails open on infrastructure.** No ffmpeg, no key, no network, no
measurement → it warns and passes. A gate that blocks your pipeline because of
its *own* breakage gets deleted within a week, and then it protects nothing. A
defect fails closed; the checker fails open. (A missing file is your typo, not
infrastructure — that still raises.)

**Silence is never mistaken for success.** A check that couldn't run says so, as
a `rendercheck.Skipped` warning and in the CLI output. An empty run never reads
as a clean one.

We had to earn the second one, twice.

The first cut of this library returned **PASS** for a file with no audio track
at all — `silencedetect` reports nothing when there is nothing to analyse, and
that read as "no silence found". It is now the loudest failure in the suite,
with a regression test named after it, and the line it taught is the rule
everything else follows: *if we measured and it is wrong, fail closed; if we
could not measure, fail open.*

Then, before releasing under this name, we audited the tool against its own
premise and found **seven more**. Every one of them reported success without
having established it:

- An all-skipped run exited `0`. No ffmpeg on the runner meant a green build.
- A **typo'd file path** exited `0`, contradicting the promise two paragraphs up.
- A typo'd `--script` path was read as narration — one word — and produced a
  confident, wrong verdict *about the audio*: `1 WPM is below 110`.
- `--presenter` without `--known-names` defaulted the roster to the assigned
  presenter, which made the speaker check **structurally incapable of firing**.
  It printed `PASS` on a script naming somebody else.
- `looks_ok` blamed a missing API key for every exception, so an SDK mismatch
  passed forever and Bedrock users were sent chasing the wrong thing.
- ffprobe failures leaked a raw Python list into the message.
- Without ffmpeg the test suite crashed on collection instead of skipping.

All seven are fixed, each with a test that fails without the fix, and the exit
codes are now a contract: `0` measured and clean, `1` a defect **or nothing
measured**, `2` a path that isn't there. Details in the
[changelog](CHANGELOG.md).

A tool that catches silent failures is worth exactly as much as its own honesty
about them.

## What it does not check

Being explicit, because a QA tool that implies more coverage than it has is
worse than none:

- **Lip sync.** `assert_streams_aligned` reads what the container *declares*
  about its two streams, and `assert_captions_aligned` matches cue timings
  against speech. Neither looks at a face. A file where sound and picture are
  declared identical and the mouth is still a beat behind passes both, and
  catching that needs a model ([SyncNet](https://github.com/joonson/syncnet_python)
  and friends) rather than arithmetic.
- **Perceptual video quality.** No PSNR, SSIM, or VMAF — those need a reference
  encode to compare against, which generated media does not have.
- **Whether the narration is *correct*** — only how fast it's read, and whether
  the captions line up in time. Nothing here transcribes the audio, so a voice
  reading the wrong script at a reasonable pace passes everything. That check
  wants Whisper, which is a dependency out of proportion to the rest of this.
  Groundedness and factual accuracy are a different problem again, well covered
  by the LLM-eval tools.
- **Speech intelligibility.** Loudness is not clarity; a correctly-levelled
  track can still be mumbled.
- **Music, mixing, or anything non-speech.** The defaults assume spoken word.
- **The rubric you didn't write.** `looks_ok` only checks what you ask it to.

## More

- [Symptoms](docs/symptoms.md) — "the TTS cut off the last sentence", "the clip
  came back silent", and which check catches each
- [Reference and tuning](docs/README.md) — every threshold, and when to turn a
  check off
- [Contributing](CONTRIBUTING.md) — the bar for a new check
- [Changelog](CHANGELOG.md)

MIT.
