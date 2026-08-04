# rendercheck

**The worst bugs in generated media don't throw.**

If your build renders speech or video — Remotion, an ffmpeg pipeline, a TTS
step, an AI video API — your tests catch the exception that never happens. They
do not catch narration at 300 words per minute, a voice track 18 dB under the
footage, a clip that came back silent, or audio that stops mid-sentence while
the API reports success.

```bash
npx rendercheck demo                  # see it fire, no files of your own needed
npx rendercheck check out/            # then point it at your renders
```

Exit code is 1 if anything failed **or if nothing could be measured**, 2 for a
path that isn't there. It drops straight into CI.

```js
// in a render script
import { execFileSync } from "node:child_process";

execFileSync("npx", ["rendercheck", "check", "out/lesson-1.mp4", "--strict"], {
  stdio: "inherit",
});
```

## Requirements

This package is a thin wrapper around the Python CLI, which is where the checks
live. It finds `rendercheck`, `uvx`, or `pipx` on your PATH:

```bash
pipx install rendercheck        # or: uv tool install rendercheck
brew install ffmpeg             # the measurements are ffmpeg's
```

Full documentation, the check reference, and the reasoning behind each default:
**https://github.com/rogermsc/rendercheck**
