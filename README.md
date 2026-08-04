# rendercheck

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
length and got cached as a success, or the file whose audio track is missing
entirely.

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

Plain assert functions. No framework, no runner, no config file, no service.
They raise `AssertionError`, so they already work in pytest, in CI, or in a
five-line script. Ten of the eleven checks have **no dependencies and make no
network calls** — if you have `ffmpeg`, you're ready.

---

## Quickstart

You need `ffmpeg` on your PATH (`brew install ffmpeg`, `apt-get install ffmpeg`,
or `winget install ffmpeg`). Then:

```bash
pip install git+https://github.com/rogermsc/rendercheck   # PyPI release pending
rendercheck demo
```

Or drop a file into the **[playground](https://rogermsc.github.io/rendercheck/playground/)**
— same checks, running on ffmpeg compiled to WebAssembly, nothing uploaded.

`demo` synthesises five defective files and runs the real checks against them,
so you can see it fire without owning a broken render. Verbatim, first two of
five:

```
Narration too fast
  A voice picked to match a presenter's face read English at machine-gun speed.

  $ rendercheck check machine-gun.wav --script narration.vtt

  FAIL  pace      narration pace 300 WPM exceeds 245 (300 words in 60.0s) -- this reads as machine-gun delivery and listeners cannot follow it: machine-gun.wav
  PASS  loudness  -16.1 LUFS
  PASS  dead air  0.0 s silence

Levels that don't match
  Synthesised narration landed 18 dB under the footage it was cut against.

  $ rendercheck check too-quiet.wav

  SKIP  pace      no --script given
  FAIL  loudness  -34.0 LUFS is 18.0 dB quieter than the -16 target -- it will sound inaudible next to correctly-levelled audio cut alongside it: too-quiet.wav
  PASS  dead air  0.0 s silence
```

Then point it at your own output:

```bash
rendercheck check episode-12.mp3 --script episode-12.vtt
```

Exit code is 1 if anything failed — **or if nothing could be measured**, because
a run that looked at nothing is not a clean one. A path you typo'd exits 2.
`--json` gives you the same report for pipelines in any language, and `--strict`
rejects partial runs too.

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
| Integrated loudness | [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm), ffmpeg's `loudnorm` |
| Silence detection | [pydub](https://github.com/jiaaro/pydub)`.silence`, ffmpeg's `silencedetect` |
| Duration, streams | `ffprobe` |
| Black frames, freezes | ffmpeg's `blackdetect`, `freezedetect` |
| Speaker identity | [resemblyzer](https://github.com/resemble-ai/resemblyzer) |
| Video quality metrics | [VMAF](https://github.com/Netflix/vmaf), [ffmpeg-quality-metrics](https://github.com/slhck/ffmpeg-quality-metrics) |

Every one of those hands a **number to a researcher**. None of them is a gate.
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
    strict: "true"
```

**Node, Remotion, anything that renders in a build step:**

```bash
npx rendercheck check out/
```

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

## Five more, for defects other people keep reporting

The six above came out of one pipeline. These came from reading other people's
bug reports — the same complaint, filed against every provider in turn:

| Check | The defect |
|---|---|
| `assert_no_truncation` | Speech that stops mid-sentence while the API returns success. The single most-reported defect in generated audio; measured against the file's own average, so it holds for quiet and loud content alike. |
| `assert_has_sound` | A clip that comes back silent — an upscale step drops the audio track, a mux points at the wrong stream, a synthesis writes zeroes. |
| `assert_no_clipping` | A gain stage pushed the waveform past full scale. Crackles on consonants, and turning it down afterwards does not undo it. |
| `assert_no_black_frames` | Generated video truncating to black instead of erroring: right length, valid container, nothing in the last third. |
| `assert_not_frozen` | The picture stops moving. Every frame present, every frame the same frame. |

The video pair **skips rather than passes** when a file has no video stream.
`blackdetect` on a `.wav` reports nothing, and nothing would otherwise read as
"looked, all clean" — the same trap that produced the regression below.

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

- **Lip sync and A/V drift.** The video checks look for black and frozen
  stretches; nothing here relates the picture to the sound.
- **Perceptual video quality.** No PSNR, SSIM, or VMAF — those need a reference
  encode to compare against, which generated media does not have.
- **Whether the narration is *correct*** — only how fast it's read.
  Groundedness and factual accuracy are a different problem, well covered by the
  LLM-eval tools.
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
