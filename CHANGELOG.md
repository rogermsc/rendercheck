# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-08-04

A review of 0.3.0 found fifteen ways it could report success without having
established it — in a library whose entire premise is not doing that. Every one
is fixed here with a test that fails without the fix. **Upgrade from 0.3.0.**

### Fixed — checks that passed, or failed, on the wrong evidence

- **`assert_captions_aligned` invented drift on correct files.** A window with
  no cues in it — a title sequence, or the beat of silence almost every file
  ends on — scores zero at every shift, the tie resolved to the largest, and a
  perfectly-aligned caption file was told to re-generate itself against a
  drift that did not exist. Measured: correct captions on a 60-second file with
  a quiet tail reported 5.0s of drift. Drift is now `None` unless both windows
  hold cues, neither saturates, and the whole-file fit did not saturate either.
- **An offset past the search range saturated silently and lied about it.** At
  6s out — a concat pre-roll, one second past the default range — the fit
  clamped to the edge and the result was reported as unfixable clock drift; at
  12s out the sign inverted, and captions twelve seconds *late* were described
  as one second early. A fit at the edge is now reported as "more than Ns away,
  the search ran out of room", which is the honest answer.
- **Ties broke toward the largest shift**, turning every ambiguous fit into a
  confident accusation that the captions were late. They break toward zero now.
- **Cues outside the media were clamped into the first or last bin**, so a
  caption file for a different, longer cut piled up at one end and produced a
  small, confident, wrong offset. Named as what it is instead.
- **`assert_streams_aligned` compared durations and ignored `start`.** A stream
  ends at start + duration, so a mux offset by 1.5s whose sound genuinely ran
  past the picture reported `PASS streams 0.0 s`. Worse, widening
  `--max-start-skew` to tolerate a known pre-roll silently discarded the ending
  check while still reporting that the endings matched.
- **`assert_format` returned silently when the container declared no
  dimensions**, which the CLI rendered as `PASS` for a size nothing had been
  compared against. It skips, like every other unmeasurable path in the file.
- **A freeze running to the end of the file was dropped entirely.**
  `freezedetect` prints `freeze_start` with no `freeze_duration` when the freeze
  is still running at EOF, and `zip` discarded it — so a video that stopped
  producing frames and held one to the end reported `PASS frozen`. That is the
  headline defect the check exists for, and it had been passing since 0.2.0.
  The audio path already had this guard; the video path now does too.
- **A timestamp quoted inside caption text became a phantom cue**, dragging the
  alignment toward a moment nobody spoke at. Cue rows are matched at the start
  of a line now, which is what they are.
- **The promptfoo assertion graded `pass` when every check skipped.** A CI
  container with the package installed but no ffmpeg passed every eval having
  measured nothing. It now fails, and says how many checks skipped.

### Fixed — crashes

- **The MCP server died mid-session on `"params": null`**, on a JSON-RPC batch,
  and on any bare scalar — all of which are things a real client sends. To the
  client that is indistinguishable from a broken tool.
- **Arguments the model guessed wrong killed the server.** argparse calls
  `sys.exit()` on a value it will not accept, and `SystemExit` is a
  `BaseException` that `except Exception` sails straight past: one
  `preset: "youtub"` and the process was gone. Rejected arguments are now a
  correctable answer, and `serve()` has a last-resort guard besides.
- **A config file could set the `file` positional** and crash the run with an
  `AttributeError`, because the whitelist was harvested from every argparse
  destination including the positionals it must never allow.
- **A `preset` typo in the config raised an uncaught `ValueError`** — a bare
  traceback, on every subcommand including `demo` and `mcp`. The same typo as a
  flag had always been handled cleanly.

### Fixed — quietly wrong behaviour

- **`check_media` reported `ok: true, clean` having measured nothing.** A
  directory with no media in it, or no `path` at all (`Path("")` is the current
  directory, which exists), returned a clean verdict. An agent whose render step
  wrote no files was told its render was fine — by the surface built for agents.
- **Config values bypassed argparse's `type=`, `choices` and `nargs`.**
  `known_names = "Karl"` — the natural TOML for a one-name roster — arrived as a
  string the speaker check iterated letter by letter, giving the roster
  `{k, a, r, l}`, which can never match: `PASS` forever, the exact outcome the
  CLI already refuses to ship on the flag path. `strict = "no"` turned strict
  mode *on*. Config values are now checked the way flags are, and a value of the
  wrong shape is reported rather than used.
- **`--preset web` was not the no-op it is documented as**, adding a −1 dBTP
  gate that no bare run applies. `web` exists to *name* the built-in defaults,
  so it now carries no ceiling. The test that should have caught this asserted
  `assert_loudness.__defaults__ is None`, which is a tautology for any
  keyword-only function; it reads `__kwdefaults__` now.
- **The image branch silently discarded `--expect-width/-height/-fps`**, so an
  explicit size requirement on a PNG was never checked and never mentioned.
- **Skips were collected with `warnings.catch_warnings`**, documented as not
  thread-safe, while files are checked on a thread pool — so one file's skip
  could be recorded against another, leaving the check that actually skipped
  counted as a **pass**. Collection now runs through a `ContextVar`, which is
  per-thread by definition. `collect_skips` is public, for callers who want the
  same thing.
- **`input_tp` parsing shared a `try` with the mandatory loudness reading**, so
  an unreadable optional extra would have taken loudness and dead air down with
  it.
- **The release workflow published truncated notes.** Its `awk` stopped at any
  `[x]:` line, and v0.3.0's own notes lost their `### Fixed` section to an
  ordinary markdown link reference sitting mid-section. It now stops only at the
  version-link block, and fails the release if the notes come out empty.
- **A `pyproject.toml` that merely mentioned `[tool.rendercheck]`** — in a
  comment, or in prose — halted the upward config search and hid the real config
  above it. Matched at the start of a line now.

### Changed

- Audio-only measurement passes `-vn`, so checking a long video no longer
  decodes every frame three times to read its sound.

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

[Unreleased]: https://github.com/rogermsc/rendercheck/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/rogermsc/rendercheck/releases/tag/v0.3.1
[0.3.0]: https://github.com/rogermsc/rendercheck/releases/tag/v0.3.0
[0.2.0]: https://github.com/rogermsc/rendercheck/releases/tag/v0.2.0

[ffsubsync]: https://github.com/smacke/ffsubsync
