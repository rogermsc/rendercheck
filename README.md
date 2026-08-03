# silentfail

**The worst bugs in generated media don't throw.**

Your test suite catches the exception that never happens. It does not catch the
narration that reads at 300 words per minute, the voice track sitting 18 dB
below the avatar it's cut against, the segment that rendered at 42% length and
got cached as a success, or the wrong presenter introducing themselves for a
whole module.

Those are all real incidents. Every one of them shipped. The detector, in every
case, was a human watching the finished output — the most expensive one
available.

`silentfail` makes them throw.

```python
from silentfail import assert_pace, assert_loudness, looks_ok

assert_pace("lesson-1.2.mp4", "lesson-1.2.vtt")
assert_loudness("lesson-1.2.mp4")
looks_ok("slide-14.png", ["the title fits on one line"])
```

They're plain assert functions. No framework, no runner, no config file, no
service. They raise `AssertionError`, so they already work in pytest, in CI, or
in a five-line script.

---

## Six checks, six incidents

Each default below is a threshold set by a defect that actually shipped, not a
number chosen for symmetry. All output is verbatim.

**Narration too fast.** A voice picked to match a presenter's *face* narrated
English at ~280 WPM. The audio was valid, correctly timed, perfectly in sync.
It just sounded like a machine gun.

```python
assert_pace("lesson-1.2.mp4", "lesson-1.2.vtt", max_wpm=245)
```
```
narration pace 300 WPM exceeds 245 (300 words in 60.0s) -- this reads as
machine-gun delivery and listeners cannot follow it: lesson-1.2.mp4
```

**Levels that don't match.** Synthesised narration landed at −34 LUFS and was
concatenated with avatar footage at −13. Same file, same lesson, a 20 dB step
in the middle. Viewers rode the volume knob for 45 minutes.

```python
assert_loudness("lesson-1.2.mp4", target_lufs=-16, tol=2.0)
```
```
-34.0 LUFS is 18.0 dB quieter than the -16 target -- it will sound inaudible
next to correctly-levelled audio cut alongside it: lesson-1.2.mp4
```

**Truncated renders cached as successes.** Encode failures produced segments a
fraction of their intended length, which the pipeline then cached as
*succeeded*. Retries only re-ran segments that had **errored** — and these
hadn't.

```python
assert_duration("segment-07.mp4", expected_seconds=24.0)
```
```
segment-07.mp4 is 10.0s -- 42% of the expected 24.0s. A render this short is a
silent encode failure, not a short take; re-render rather than retry
```

**Holes in the middle.** Compositing failed transiently *and silently*, leaving
dead stretches mid-lesson. The file was the right length and the right average
loudness. The hole only existed in the middle.

```python
assert_no_dead_air("lesson-1.2.mp4", max_silence=3.0)
```
```
6.2s of silence starting at 0:41 exceeds the 3s limit -- a gap this long
mid-file is a dropped segment, not a pause (2 found in total): lesson-1.2.mp4
```

**The wrong person on screen.** A script said "I'm Benjawan" while the registry
assigned Karl. An entire module rendered with the wrong face and the wrong
gender. **Every other gate passed.**

```python
assert_speaker(script, expected="Karl", known_names=["Karl", "Benjawan", "Nika"])
```
```
the script introduces the presenter as "Benjawan" but Karl is assigned -- the
rendered avatar would introduce itself with someone else's name. Fix whichever
is wrong: the assigned presenter, or the name in the script
```

The `known_names` roster is required, and it's the whole trick: without it, a
character in a scenario saying *"I'm Amara, a nurse"* trips the check on every
script that tells a story. Only a name belonging to someone who could actually
have been cast counts as a claim about the presenter.

**Things you can only see.** Overflowing titles, colliding logos, half-empty
canvases, figures cropped mid-caption. All rendered without error.

```python
looks_ok("slide-14.png", [
    "the title fits on one line",
    "no text is clipped at any edge",
])
```
```
[major] slide-14.png: the title wraps to three lines and overlaps the logo in
the top-right corner -- failed rubric item: 'the title fits on one line'
```

---

## Quickstart

The first five checks need **no API key, no network, and no model**. If you have
`ffmpeg` — and if you're generating media, you do — you're ready:

```bash
pip install silentfail
silentfail check lesson-1.2.mp4 --script lesson-1.2.vtt
```

```
  FAIL  pace      narration pace 300 WPM exceeds 245 (300 words in 60.0s) -- …
  PASS  loudness  -16.0 LUFS
  FAIL  dead air  6.2s of silence starting at 0:41 exceeds the 3s limit -- …

  1 passed, 2 failed, 0 skipped
```

Exit code is 1 if anything failed, so it drops straight into CI.

In pytest, they're just asserts — no plugin, no fixtures:

```python
import pytest
from silentfail import assert_pace, assert_loudness

@pytest.mark.parametrize("lesson", LESSONS)
def test_lesson_is_shippable(lesson):
    assert_pace(lesson.mp4, lesson.vtt)
    assert_loudness(lesson.mp4)
```

`looks_ok` is the only check that needs a key:

```bash
pip install "silentfail[vision]"
export ANTHROPIC_API_KEY=...
```

---

## Two promises

**It fails open on infrastructure.** No ffmpeg, no key, no network, no
measurement → it warns and passes. A gate that blocks your pipeline because of
its *own* breakage gets deleted within a week, and then it's protecting nothing.
A defect fails closed; the checker fails open. (A missing file is your typo, not
infrastructure — that still raises.)

**Silence is never mistaken for success.** A check that couldn't run says so, in
the CLI output and as a `silentfail.Skipped` warning. An empty run never reads as
a clean one.

---

## What it does not check

Being explicit, because a QA tool that implies more coverage than it has is worse
than none:

- **Lip sync, A/V drift, and video quality.** Nothing here decodes video frames.
- **Whether the narration is *correct*** — only how fast it's read. Groundedness
  and factual accuracy are a different problem, well covered by the LLM-eval
  tools.
- **Speech intelligibility.** Loudness is not clarity; a correctly-levelled
  track can still be mumbled.
- **Music, mixing, or anything non-speech.** The defaults assume spoken word.
- **The rubric you didn't write.** `looks_ok` only checks what you ask it to.

Every threshold is an argument, not a constant. If `245` is wrong for your
content, pass a different number — the defaults are a starting point from one
domain, not a standard.

## License

MIT
