# Security

## Reporting

Report vulnerabilities through
[GitHub's private advisory form](https://github.com/rogermsc/silentfail/security/advisories/new).
Please don't open a public issue for anything exploitable.

## What this library does with your data

Worth knowing before you point it at anything sensitive:

- **The deterministic checks are entirely local.** `assert_pace`,
  `assert_loudness`, `assert_duration`, `assert_no_dead_air`, and
  `assert_speaker` invoke `ffmpeg`/`ffprobe` as subprocesses on your machine and
  make no network calls whatsoever.
- **`looks_ok` uploads the image you give it** to the Anthropic API, along with
  your rubric text, in order to review it. That is the entire point of the
  check, but it means the image leaves your machine. Don't call it on anything
  you would not send to a third-party API.
- **Nothing is persisted.** No telemetry, no analytics, no cache written to
  disk. The in-process measurement cache is memory-only and dies with the
  process.
- **Failure messages include file paths**, which end up in your CI logs. If your
  paths are sensitive, treat the log output accordingly.

## Subprocess handling

Paths are passed to `ffmpeg`/`ffprobe` as argument-vector elements, never
through a shell, so a filename containing shell metacharacters is not
interpreted. The binaries are resolved from `PATH` via `shutil.which`.
