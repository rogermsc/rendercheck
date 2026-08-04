# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-08-04

Four checks and three surfaces, all chosen from what people outside this repo
are asking for rather than from what this pipeline happened to hit.

### Added

- **`assert_captions_aligned`** — do the captions describe the audio they ship
  next to? The gap here is real: [ffsubsync] and friends *correct* drift, and the
  online validators lint the `.srt` on its own (overlapping cues, reading speed).
  Neither asks whether this caption file matches this audio, which is the only
  question a build needs answered. Both sides become a coarse "is anyone talking
  now" track — cues on one side, `silencedetect` on the other — and are slid
  against each other at 100 ms resolution.

  **Offset and drift are separate findings, and drift is reported first.** An
  offset is one shift from correct; drift means the two were timed against
  different clocks and no shift fixes it. A drifting file also has an average
  offset, so testing offset first would report the symptom and bury the cause.

  **It skips when no shift stands out.** Wall-to-wall speech and music beds have
  no silence structure, so every shift scores identically — and reading a
  confident zero offset off that flat curve is exactly the failure this library
  is named after. There is a test for it.

- **`assert_streams_aligned`** — sound and picture must cover the same stretch of
  time. Catches a mux that ran out of one input (audio ends early) and a
  concatenation that mistimed its first segment (streams start apart). One
  `ffprobe` call, no decoding: the cheapest check here. Skips when the container
  declares no per-stream duration rather than comparing against an invented one.

- **`assert_format`** — resolution and frame rate against what was asked for,
  checking only what you pass. With `fps` it also catches variable frame rate,
  a standard cause of audio drifting against picture downstream.

- **`assert_true_peak`** and **platform presets.** `--preset
  youtube|spotify|tiktok|podcast|apple|web|ebu|atsc|netflix` sets the loudness
  target, tolerance and true-peak ceiling together from published specs;
  `rendercheck presets` prints the table with sources. None of the numbers are
  ours — the contribution is that `--preset ebu` is a decision a reviewer can
  read where `--target-lufs -23` is a magic number nobody will touch. True peak
  is what the waveform reaches *between* samples, so it catches the master that
  measures clean locally and distorts after upload; it comes free from the
  decode loudness already runs.

- **An MCP server** (`rendercheck mcp`). Coding agents now write render
  pipelines and run them, and the media that comes back is the one artifact they
  cannot inspect; the existing media MCP servers cut and transcode, which hands
  the model *more* media rather than an answer. Written against the wire
  protocol directly — stdio MCP is newline-delimited JSON-RPC — so it adds **no
  dependency**. `check_media` returns the same `status`/`check`/`detail` records
  as `--json`; `list_checks` returns every check and preset.

- **A Docker image**, `ghcr.io/rogermsc/rendercheck`, with ffmpeg already in it.
  "Install Python, then ffmpeg, then this" is three steps too many for someone
  whose build is a Node container.

- **A config file** — `rendercheck.toml`, or `[tool.rendercheck]` in
  `pyproject.toml`. Precedence runs defaults → file → `--preset` → typed flags. A
  `pyproject.toml` with no section of ours is skipped rather than treated as an
  empty config, so a package directory inside a repo does not hide the real one.
  **An unknown key is reported on stderr, not ignored**; needs Python 3.11 for
  `tomllib`, and says so on 3.10 instead of silently doing nothing.

### Changed

- `rendercheck demo` gained two cases — captions three seconds late against their
  audio, and a mux whose sound runs out before the picture. Every fixture is
  built so it demonstrates exactly one defect.
- The README's *What it does not check* now separates container timing and
  caption timing from **lip sync**, which still needs a model and is still not
  here. The new checks do not narrow that disclaimer as much as they might look
  like they do.

[ffsubsync]: https://github.com/smacke/ffsubsync

### Fixed

- **npm wrapper 0.2.1 — `npx rendercheck` spawned itself forever.** npx puts its
  own shim for this package on PATH under the name `rendercheck`, which is
  exactly the name the wrapper probes for to find the Python CLI. It resolved to
  the shim, spawned it, and that shim pointed back at the wrapper. `npx
  rendercheck` hung on the first real use. It now enumerates every match with
  `which -a`, skips any candidate that resolves to itself or sits under
  `node_modules`/`_npx`, and carries an environment guard so a shim it cannot
  recognise costs one wasted hop and a clear error rather than an unbounded
  loop. Covered by a CI step that rebuilds the npx layout.

## [0.2.0] - 2026-08-04

First release under this name, and the first on PyPI as `rendercheck`.

`0.1.0` was published as **`silentfail`** and renamed the next day, before it had
users: the name collided with an unrelated AI-tooling project, and it was not the
word anyone with this problem would search for — they search *"veo 3 no audio"*
and *"tts cut off last sentence"*. The version number moves rather than the tag,
so the `silentfail` release stays where it is instead of being rewritten. The
`SilentFail` exception keeps its name; it is still exactly what it means.

Eleven assertions for generated media. Six came out of one production pipeline
that renders narrated video at scale; five came from reading other people's bug
reports.

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

[Unreleased]: https://github.com/rogermsc/rendercheck/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/rogermsc/rendercheck/releases/tag/v0.3.0
[0.2.0]: https://github.com/rogermsc/rendercheck/releases/tag/v0.2.0
