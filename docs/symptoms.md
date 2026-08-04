# "My generated file is broken" — which check catches it

Sorted by what you would type into a search box, because that is how people
arrive at this problem. Every command below works on any file; nothing here
needs the rest of your pipeline.

Install: `pip install rendercheck` (plus `ffmpeg` on your PATH). Run
`rendercheck demo` first if you want to watch the checks fire before pointing
them at your own output.

---

## "The TTS cut off the last sentence"

The most-reported defect in generated speech, filed against every provider in
turn. The API returns success; the audio simply stops mid-thought.

```bash
rendercheck check narration.mp3
```

```
FAIL  truncation  narration.mp3 is still at its full average level 0.4 dB into
the final 0.25s (expected at least 6 dB of fall-off) -- audio that stops flat
was cut rather than finished, which is what a dropped last sentence looks like
from outside
```

Speech that finishes tails off, into a decay or into the small silence a
sentence leaves behind. Speech that was cut stops at full level. The check
compares the ending against the file's *own* average, so it holds for quiet and
loud content alike.

If your content legitimately ends hot — a music bed, a hard cut into the next
scene — lower the bar: `--min-tail-drop 3`.

## "Veo / Sora / Runway returned a video with no audio"

Upscaling and re-encode steps drop audio tracks. The clip plays; there is
nothing to hear; nothing errored.

```bash
rendercheck check clip.mp4
```

```
FAIL  has sound  clip.mp4 has no audio stream at all -- this is not silence, it
is a missing track, and it usually means a mux step dropped the audio or was
never given any. Nothing downstream will play sound
```

Worth knowing: a missing audio *track* and a track of digital silence are
different failures with different causes, and this separates them. A silent
track means synthesis or mixing failed; a missing track means the mux did.

## "The narration is way too fast"

Usually a voice whose primary language isn't the one it is reading, or a speed
parameter that was ignored.

```bash
rendercheck check episode.mp3 --script episode.vtt
```

```
FAIL  pace  narration pace 300 WPM exceeds 245 (300 words in 60.0s) -- this
reads as machine-gun delivery and listeners cannot follow it: episode.mp3
```

Takes a `.vtt`, `.srt`, a plain transcript, or the raw text. Cue numbers,
timestamps and tags are stripped before counting. `--max-wpm` and `--min-wpm`
move the bounds; ~150–180 WPM is comfortable narration, 245 is where it stops
being followable.

## "The voice is much quieter than the music"

Two sources mastered to different levels and concatenated. Correct on its own,
wrong next to anything else.

```bash
rendercheck check voice.wav --target-lufs -16
```

```
FAIL  loudness  -34.0 LUFS is 18.0 dB quieter than the -16 target -- it will
sound inaudible next to correctly-levelled audio cut alongside it: voice.wav
```

−16 LUFS suits speech cut against other speech. −14 is the streaming-music
convention; −19 to −23 is broadcast. Set it to whatever the rest of your audio
is mastered to and the check becomes an alignment test rather than an opinion.

## "The audio has a gap in the middle"

A compositing or concatenation step failed for one segment and carried on. Right
total length, right average loudness, a hole you only find by listening.

```bash
rendercheck check lesson.mp4 --max-silence 3
```

```
FAIL  dead air  6.2s of silence starting at 0:41 exceeds the 3s limit -- a gap
this long mid-file is a dropped segment, not a pause (2 found in total)
```

For **recorded** audio rather than synthesised, raise `--silence-threshold`:
real rooms have a noise floor well above −50 dB, so a recorded track may never
register as silent at the default.

## "The generated video goes black partway through"

Generation truncates to black rather than erroring. The container is valid and
the duration is right.

```bash
rendercheck check clip.mp4
```

```
FAIL  black frames  4.2s of solid black starting at 0:06 exceeds the 1s limit --
a stretch this long is a render that stopped producing picture, not a transition
```

## "The video is frozen on one frame"

A failed interpolation plays as a still photograph with sound over it.

```bash
rendercheck check clip.mp4 --max-freeze 3
```

Raise `--max-freeze` for content with deliberate held shots.

## "The audio crackles"

Something in the chain applied gain past full scale. The waveform is flattened
where it clipped, and lowering the volume afterwards does not bring it back.

```bash
rendercheck check narration.wav
```

```
FAIL  clipping  4821 samples in narration.wav are pinned at 0 dBFS, past the 100
allowed -- the waveform was flattened where it clipped, which crackles on
consonants and cannot be undone by lowering the level afterwards
```

## "The render is much shorter than it should be"

An encode failure that produced a valid short file, which a pipeline then cached
as a success — so retries, which only re-run the ones that *errored*, never
touched it.

```bash
rendercheck check segment.mp4 --expect-seconds 24
```

```
FAIL  duration  segment.mp4 is 10.0s -- 42% of the expected 24.0s. A render this
short is a silent encode failure, not a short take; re-render rather than retry
```

A badly short render and a mild drift are different problems, so they get
different messages: below `--min-ratio` (default 50%) it is a broken encode,
above it a timing mismatch.

## "The wrong presenter is on screen"

The script introduces someone the pipeline did not cast.

```bash
rendercheck check lesson.mp4 --script lesson.vtt \
  --presenter Alex --known-names Alex Jordan Sam
```

`--known-names` is required, and it is the whole trick: without a roster of
people who could actually have been cast, a character in a scenario saying
*"I'm Rosa, a nurse"* trips the check on every script that tells a story.

## "The slide looks wrong and I can't write a rule for it"

Overflowing titles, colliding logos, figures cropped mid-caption.

```bash
rendercheck check slide-14.png --rubric "the title fits on one line" \
  "no text is clipped at any edge"
```

The only check that needs an API key — `pip install "rendercheck[vision]"`.
Rubric items should be things a person could verify at a glance; vague ones
("looks professional") produce vague findings.

---

Everything above, with every threshold and when to turn a check off:
[reference](README.md).
