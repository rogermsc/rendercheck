# Contributing

## Setup

```bash
git clone https://github.com/rogermsc/silentfail
cd silentfail
pip install -e ".[dev,vision]"
```

You also need **ffmpeg** on your PATH. The test fixtures are generated with it
rather than committed as binaries, so the suite exercises the real measurement
path instead of a mock of it.

## The gate

Everything CI runs, you can run:

```bash
ruff check . && ruff format --check .
mypy
pytest -q --cov --cov-fail-under=80
```

Both test files also run as plain scripts (`python tests/test_checks.py`) if you
would rather not involve pytest.

## What a good change looks like

**Every check must earn its place with a real defect.** The six that exist are
each traceable to something that actually shipped. A check that catches a
hypothetical is a check that mostly produces false positives, and a QA tool
people learn to ignore is worse than no QA tool.

**The failure message is the product.** It should name the measured value, the
threshold it broke, and what a person would actually perceive. Someone should be
able to paste it into an issue unedited. `"loudness check failed"` is not
acceptable; `"-34.0 LUFS is 18.0 dB quieter than the -16 target — it will sound
inaudible next to correctly-levelled audio cut alongside it"` is the bar.

**Respect the fail-open line.** If we *measured* something and it is wrong, fail
closed. If we *could not measure* — no ffmpeg, no key, no network, an unreadable
file — warn via `skip()` and pass. Getting this backwards is how a QA gate ends
up deleted. A missing file is the caller's typo, not infrastructure, and still
raises.

**Thresholds are arguments, not constants.** Every default is a keyword argument
with a documented origin. Do not hard-code one.

## Platforms

CI runs Linux on Python 3.10–3.14. macOS is the primary development platform and
is exercised locally; Windows is untested — reports welcome.
