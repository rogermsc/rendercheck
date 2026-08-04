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
- `rendercheck check <file>`, with `--json` for calling from another pipeline.

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
