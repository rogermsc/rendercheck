# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-03

First release. Six assertions for generated media, extracted from a production
pipeline that renders narrated video at scale.

Briefly published the same day as `silentfail`, renamed before it had users: the
name collided with an unrelated AI-tooling project and was not the word anyone
with this problem would search for. The `SilentFail` exception keeps the old
name — it is still exactly what the exception means.

### Added

- `assert_pace` — narration speed in words per minute, read from raw text or a
  `.vtt` / `.srt` sitting next to the media.
- `assert_loudness` — integrated loudness in LUFS against a target.
- `assert_duration` — actual length against expected, distinguishing a badly
  short render (a broken encode) from a mild mismatch (drift).
- `assert_no_dead_air` — longest silent stretch, with a timestamp.
- `assert_speaker` — the script's self-introduction against the presenter
  actually assigned, gated on a roster so story characters don't trip it.
- `looks_ok` — a rubric-driven vision check for defects only a person can see.
  Optional extra: `pip install "rendercheck[vision]"`.
- `assert_no_truncation` — audio that stops at full level was cut, not finished.
  Measured against the file's own average rather than a fixed threshold.
- `assert_no_clipping` — samples pinned at full scale by a gain stage.
- `assert_has_sound` — no audio stream, or a stream of pure zeroes.
- `assert_no_black_frames` and `assert_not_frozen` — generated video that
  truncates to black or holds a single frame. Both skip rather than pass when
  there is no video stream to analyse.
- `rendercheck check <file>`, with `--json` for calling from another pipeline.
  Takes several files, or a directory, and checks them on a thread pool. With
  more than one file `--json` emits one object per line, so a single-file
  consumer sees exactly what it saw before.
- A composite **GitHub Action** (`action.yml`) that installs ffmpeg and fails
  the build on a defect.
- An **npx wrapper** (`npm/`) for the Remotion/Node side, which shells to the
  Python CLI and passes its exit codes through. Not yet published to npm.
- A **promptfoo custom assertion** (`examples/promptfoo/`). promptfoo's
  assertions are string-shaped, so an eval can check the narration script and
  nothing about the audio it produced.
- `rendercheck demo` — synthesises five defective files with ffmpeg and runs the
  real checks against them. Every check here only fires on *broken* media, so
  trying the library used to require already owning a broken render.

### Fixed — eight silent failures found inside the checker itself

**`assert_no_dead_air` missed gaps in quiet audio.** `loudnorm` and
`silencedetect` share one decode, and `loudnorm` was first in the chain — but
`loudnorm` is a *filter*, not just a meter, so the gap detector was reading audio
it had already normalised. A quiet file got boosted on the way through, which
lifted its near-silent stretches above the threshold and hid them. Measured on a
−40 dBFS file with a five-second gap: **zero gaps found the old way, one the
new way**, with an identical loudness reading either way. Quiet material is
exactly the population most likely to have this defect, so the miss was aimed at
the worst possible audience. `silencedetect` now runs first.


Audited before the first release under this name. Every one of these made the
tool report success without having established it:

- The CLI exited `0` when **every** check skipped. A run that measured nothing
  was green in CI. It now exits `1`, with or without `--strict`.
- A **typo'd media path** exited `0`: `FileNotFoundError` was caught and
  downgraded to a skip, contradicting the documented promise. Now exit `2`.
- A **typo'd `--script` path** was read as narration text — one word — and
  produced a confident, wrong verdict about the *audio* (`1 WPM is below 110`).
  A path-shaped string ending in a transcript extension now raises.
- `--presenter` without `--known-names` defaulted the roster to the assigned
  presenter, which made the speaker check **structurally incapable of firing**.
  It printed `PASS` on a script naming someone else. It now refuses to run and
  names the missing flag.
- `looks_ok` reported `"no ANTHROPIC_API_KEY set"` for *any* exception when that
  variable was unset — wrong for Bedrock, Vertex, or a caller-supplied `client=`,
  and it hid SDK signature mismatches entirely. It now reports the real error.
- `ffprobe` failures leaked a Python list repr into the message.
- Without ffmpeg the test suite crashed on collection instead of skipping.

`--strict`, `--version`, and flags for the five thresholds that were previously
reachable only from Python (`--min-wpm`, `--loudness-tol`, `--duration-tol`,
`--min-ratio`, `--silence-threshold`) were added in the same pass.

### Notes

- The deterministic checks have no dependencies. ffmpeg is invoked as a
  subprocess, not imported as a library.
- Every check fails **open** on infrastructure trouble (no ffmpeg, no key, no
  network) and **closed** on a defect. A missing file still raises.
- `assert_loudness` and `assert_no_dead_air` share a single decode, memoised on
  the file's `(path, mtime, size)` so a re-render is never served a stale
  reading.

[Unreleased]: https://github.com/rogermsc/rendercheck/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rogermsc/rendercheck/releases/tag/v0.1.0
