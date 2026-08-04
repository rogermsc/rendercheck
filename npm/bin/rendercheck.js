#!/usr/bin/env node
"use strict";

// Node entry point for people who render media from JavaScript -- Remotion,
// ffmpeg wrappers, anything that produces a file in a build step and wants CI
// to fail when that file is quietly broken.
//
// ponytail: this shells out to the Python CLI rather than reimplementing the
// checks. The measurements are ffmpeg's either way, so a port would duplicate
// the parsing and the thresholds and then drift from them. If install friction
// turns out to be the thing stopping people, the upgrade path is per-platform
// binaries attached to the GitHub release and fetched on postinstall, the way
// esbuild does it.

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");

const WINDOWS = process.platform === "win32";
const GUARD = "RENDERCHECK_NODE_WRAPPER";

// Backstop. If this process was started *by* this wrapper, then the thing we
// resolved as "the Python CLI" was in fact this script again, and delegating
// once more would recurse without end. Refuse instead of spinning.
if (process.env[GUARD] === "1") {
  console.error(
    "rendercheck: the Node wrapper resolved to itself, so the Python CLI is\n" +
      "not actually installed. Install it with one of:\n\n" +
      "  pipx install rendercheck\n" +
      "  uv tool install rendercheck\n" +
      "  pip install rendercheck\n",
  );
  process.exit(2);
}

let selfPath = null;
try {
  selfPath = fs.realpathSync(process.argv[1]);
} catch {
  /* argv[1] should always resolve, but never fail on the way to a failure */
}

function candidates(command) {
  // `which -a` so a shim earlier on PATH does not hide the real binary behind
  // it. Windows `where` already lists every match.
  const probe = spawnSync(WINDOWS ? "where" : "which", WINDOWS ? [command] : ["-a", command], {
    encoding: "utf8",
  });
  if (probe.status !== 0 || !probe.stdout) return [];
  return probe.stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

// The trap this exists for: `npx rendercheck` puts npx's own shim for this
// package on PATH under the name `rendercheck`. Probing for that name finds the
// shim, which points back here -- so the wrapper would spawn itself forever.
function isThisWrapper(candidate) {
  try {
    if (selfPath && fs.realpathSync(candidate) === selfPath) return true;
  } catch {
    /* unresolvable: fall through to the path test */
  }
  return /[\\/](node_modules|_npx)[\\/]/.test(candidate);
}

function firstRealBinary(command) {
  return candidates(command).find((candidate) => !isThisWrapper(candidate)) || null;
}

// uvx and pipx both run a published tool without installing it permanently,
// which is the closest thing Python has to npx.
const direct = firstRealBinary("rendercheck");
const runner = direct
  ? { command: direct, prefix: [] }
  : [
      { command: "uvx", prefix: ["rendercheck"] },
      { command: "pipx", prefix: ["run", "rendercheck"] },
    ].find((candidate) => firstRealBinary(candidate.command));

if (!runner) {
  console.error(
    [
      "rendercheck: needs the Python package on your machine.",
      "",
      "  pipx install rendercheck      # or: uv tool install rendercheck",
      "  pip install rendercheck       # if you would rather not use pipx",
      "",
      "It also needs ffmpeg on your PATH (brew install ffmpeg).",
    ].join("\n"),
  );
  process.exit(2);
}

const result = spawnSync(runner.command, [...runner.prefix, ...process.argv.slice(2)], {
  stdio: "inherit",
  env: { ...process.env, [GUARD]: "1" },
});

if (result.error) {
  console.error(`rendercheck: could not run ${runner.command}: ${result.error.message}`);
  process.exit(2);
}

// Exit codes are the contract: 0 clean, 1 a defect (or nothing measured), 2 a
// path that is not there. Passing them straight through keeps that true from
// Node as well.
process.exit(result.status === null ? 1 : result.status);
