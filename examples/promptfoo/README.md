# rendercheck as a promptfoo assertion

[promptfoo](https://github.com/promptfoo/promptfoo) evaluates text. Its assertion
list — `contains`, `similar`, `llm-rubric`, ROUGE/BLEU, latency, cost — operates
on strings, because its test case *is* a string. That is the right design for
prompts and it leaves generated media unchecked: a TTS eval can confirm the
script was correct and tell you nothing about whether the audio was listenable.

This bridges the two. Your provider renders a file and prints its path;
`get_assert` measures the file behind that path.

## Use it

```bash
pip install rendercheck        # plus ffmpeg on your PATH
npx promptfoo@latest eval
```

```yaml
assert:
  - type: python
    value: file://rendercheck_assert.py:get_assert
```

The failure `reason` promptfoo shows you is rendercheck's exception message
verbatim — the measured value, the threshold it broke, and what a listener would
actually notice:

```
-34.0 LUFS is 18.0 dB quieter than the -16 target -- it will sound inaudible
next to correctly-levelled audio cut alongside it: episode-12.mp3
```

## What it checks

`assert_has_sound`, `assert_loudness`, `assert_true_peak`, `assert_no_dead_air`,
`assert_no_truncation` and `assert_no_clipping` on every case.

Three vars turn on the rest:

```yaml
tests:
  - vars:
      script: "The full narration text, or a path to a .vtt"   # assert_pace
      captions: episode-12.vtt                                 # assert_captions_aligned
      preset: podcast                                          # -16 LUFS, -1 dBTP
```

`captions` is optional even when you want the check: a `.vtt` or `.srt` sitting
beside the media under the same name is found automatically. `preset` is any
name from `rendercheck presets` — `youtube`, `spotify`, `tiktok`, `podcast`,
`apple`, `web`, `ebu`, `atsc`, `netflix` — and a name that does not exist is
graded as a failure saying so, rather than quietly falling back to a default.

Edit `rendercheck_assert.py` to change the set or the thresholds — it is fifty
lines, and it is meant to be edited rather than configured.
