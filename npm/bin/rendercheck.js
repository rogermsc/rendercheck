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

function onPath(command) {
  const probe = spawnSync(process.platform === "win32" ? "where" : "which", [command], {
    stdio: "ignore",
  });
  return probe.status === 0;
}

// uvx and pipx both run a published tool without installing it permanently,
// which is the closest thing Python has to npx.
const runners = [
  { command: "rendercheck", prefix: [] },
  { command: "uvx", prefix: ["rendercheck"] },
  { command: "pipx", prefix: ["run", "rendercheck"] },
];

const runner = runners.find((candidate) => onPath(candidate.command));

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
});

if (result.error) {
  console.error(`rendercheck: could not run ${runner.command}: ${result.error.message}`);
  process.exit(2);
}

// Exit codes are the contract: 0 clean, 1 a defect (or nothing measured), 2 a
// path that is not there. Passing them straight through keeps that true from
// Node as well.
process.exit(result.status === null ? 1 : result.status);
