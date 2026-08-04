# rendercheck

[![PyPI](https://img.shields.io/pypi/v/rendercheck)](https://pypi.org/project/rendercheck/)
[![CI](https://github.com/rogermsc/rendercheck/actions/workflows/ci.yml/badge.svg)](https://github.com/rogermsc/rendercheck/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.14-blue)](https://github.com/rogermsc/rendercheck)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**The worst bugs in generated media don't throw.**

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
five-line script. The five deterministic checks have **no dependencies and make
no network calls** — if you have `ffmpeg`, you're ready.

---

## Quickstart

You need `ffmpeg` on your PATH (`brew install ffmpeg`, `apt-get install ffmpeg`,
or `winget install ffmpeg`). Then:

```bash
pip install rendercheck
rendercheck demo
```

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

We had to earn the second one. The first cut of this library returned **PASS**
for a file with no audio track at all — `silencedetect` reports nothing when
there is nothing to analyse, and that read as "no silence found". A silent
failure inside rendercheck. It is now the loudest failure in the suite, with a
regression test named after it, and the line it taught us is the rule everything
else follows: *if we measured and it is wrong, fail closed; if we could not
measure, fail open.*

## What it does not check

Being explicit, because a QA tool that implies more coverage than it has is
worse than none:

- **Lip sync, A/V drift, and video quality.** Nothing here decodes video frames.
- **Whether the narration is *correct*** — only how fast it's read.
  Groundedness and factual accuracy are a different problem, well covered by the
  LLM-eval tools.
- **Speech intelligibility.** Loudness is not clarity; a correctly-levelled
  track can still be mumbled.
- **Music, mixing, or anything non-speech.** The defaults assume spoken word.
- **The rubric you didn't write.** `looks_ok` only checks what you ask it to.

## More

- [Reference and tuning](docs/README.md) — every threshold, and when to turn a
  check off
- [Contributing](CONTRIBUTING.md) — the bar for a new check
- [Changelog](CHANGELOG.md)

MIT.
