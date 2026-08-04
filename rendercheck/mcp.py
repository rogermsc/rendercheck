"""An MCP server, so an agent can check what it just rendered.

The gap this fills: coding agents now write render pipelines and run them, and
the media that comes back is the one artifact they cannot inspect. The existing
media MCP servers cut, transcode and thumbnail -- they hand the model *more*
media. None of them answer "is this file broken", which is the only question an
agent has after a render finishes.

Written directly against the wire protocol rather than the SDK, because the
deterministic side of this library has no dependencies and that is worth more
than the two hundred lines the SDK would save. MCP over stdio is newline-
delimited JSON-RPC; that is the whole transport.

    claude mcp add rendercheck -- rendercheck mcp
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import __version__, presets

# Echoed back when the client asks for a version we know. Clients that ask for
# something else get their own string back: this server uses only the parts of
# the protocol that have not changed across revisions, so refusing the
# connection over a date would be a worse answer than serving it.
KNOWN = ("2025-06-18", "2025-03-26", "2024-11-05")

_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Media file to check. A directory checks what is inside it.",
        },
        "script": {
            "type": "string",
            "description": (
                "Narration text, or a path to a transcript/.vtt/.srt. "
                "Enables the pace check."
            ),
        },
        "captions": {
            "type": "string",
            "description": (
                "A .vtt or .srt to check against the audio. Found automatically "
                "if it sits beside the media under the same name."
            ),
        },
        "expected_seconds": {
            "type": "number",
            "description": "How long the render was supposed to be.",
        },
        "preset": {
            "type": "string",
            "enum": sorted(presets.PRESETS),
            "description": "Loudness target for wherever this file is going.",
        },
        "strict": {
            "type": "boolean",
            "description": "Count a check that could not run as a failure.",
        },
    },
    "required": ["path"],
}

TOOLS = [
    {
        "name": "check_media",
        "description": (
            "Check a generated audio or video file for the defects that do not "
            "raise an error: narration pace, loudness, true peak, dead air, "
            "truncated endings, clipping, black or frozen video, sound and "
            "picture that do not line up, and captions that do not match the "
            "audio. Returns one verdict per check with the measured value. Run "
            "this after any step that produces media -- a render that failed "
            "silently is indistinguishable from one that worked until something "
            "measures it."
        ),
        "inputSchema": _CHECK_SCHEMA,
    },
    {
        "name": "list_checks",
        "description": (
            "What check_media can look for, what each one costs, and the "
            "loudness presets available. Call this to decide which arguments "
            "are worth supplying."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _check(arguments: dict[str, Any]) -> dict[str, Any]:
    """Run the CLI's plan over one path and return it as data.

    Deliberately reaches for `_plan`/`_run` rather than `main`: everything in the
    CLI's reporting path prints, and on this transport stdout carries the
    protocol. One stray line of human-readable output corrupts the session.
    """
    from .cli import EXIT_OK, FAIL, SKIP, _expand, _parse, _run, _verdict

    path = Path(str(arguments.get("path", "")))
    if not path.exists():
        raise FileNotFoundError(f"no such file: {path}")

    argv = ["check", str(path)]
    for flag, key in (
        ("--script", "script"),
        ("--captions", "captions"),
        ("--preset", "preset"),
    ):
        if arguments.get(key):
            argv += [flag, str(arguments[key])]
    if arguments.get("expected_seconds"):
        argv += ["--expect-seconds", str(arguments["expected_seconds"])]
    strict = bool(arguments.get("strict"))
    if strict:
        argv.append("--strict")

    from .cli import _plan  # imported here to keep the module import graph flat

    args = _parse(argv)
    files: list[dict[str, Any]] = []
    worst = EXIT_OK
    for target in _expand([path]):
        results = [_run(planned) for planned in _plan(target, args)]
        worst = max(worst, _verdict(results, strict))
        files.append(
            {
                "file": str(target),
                # Same keys as `--json`. Anything parsing one parses the other.
                "results": [
                    {"status": r.status, "check": r.name, "detail": r.detail}
                    for r in results
                ],
                "failed": sum(1 for r in results if r.status == FAIL),
                "skipped": sum(1 for r in results if r.status == SKIP),
            }
        )

    return {
        "files": files,
        "exit_code": worst,
        "ok": worst == EXIT_OK,
        # Spelled out because "exit_code: 1" does not say which of the two
        # things it means, and the difference changes what the agent does next.
        "verdict": (
            "clean"
            if worst == EXIT_OK
            else "defects found, or nothing could be measured"
        ),
    }


def _describe() -> dict[str, Any]:
    from .cli import _UNITS

    return {
        "checks": [
            {"check": name, "reports": unit} for name, unit in sorted(_UNITS.items())
        ],
        "presets": {
            name: {
                "target_lufs": preset.target_lufs,
                "tolerance_db": preset.tol,
                "max_true_peak_dbtp": preset.max_true_peak,
                "note": preset.note,
            }
            for name, preset in presets.PRESETS.items()
        },
        "notes": [
            "Checks needing an argument you did not supply are reported as SKIP, "
            "never as a pass.",
            "A run where every check skipped is a failure: nothing was measured.",
            "ffmpeg must be on PATH. Without it every check skips and the run "
            "fails rather than reporting success.",
        ],
    }


def _result(payload: dict[str, Any], failed: bool = False) -> dict[str, Any]:
    """An MCP tool result: readable text, plus the same thing as data."""
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "structuredContent": payload,
        "isError": failed,
    }


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "check_media":
        return _result(_check(arguments))
    if name == "list_checks":
        return _result(_describe())
    raise ValueError(f"unknown tool: {name}")


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """One request in, one response out. None for notifications."""
    method = message.get("method")
    request_id = message.get("id")

    # Notifications carry no id and must never be answered -- a response to one
    # is a protocol error, and some clients drop the connection over it.
    if request_id is None:
        return None

    def reply(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    if method == "initialize":
        asked = str(message.get("params", {}).get("protocolVersion", KNOWN[0]))
        return reply(
            {
                "protocolVersion": asked if asked in KNOWN else KNOWN[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "rendercheck", "version": __version__},
            }
        )
    if method == "tools/list":
        return reply({"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params", {})
        try:
            return reply(
                _call(str(params.get("name", "")), params.get("arguments") or {})
            )
        except Exception as exc:
            # A tool that failed is a result, not a transport error: the model
            # should see what went wrong and fix its arguments, and a JSON-RPC
            # error would be reported to the user as a broken server instead.
            return reply(
                _result({"error": f"{type(exc).__name__}: {exc}"}, failed=True)
            )

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(stdin: Any = None, stdout: Any = None) -> int:
    """Read requests from stdin, write responses to stdout, until stdin closes.

    Nothing but protocol goes to stdout. Diagnostics go to stderr, which the
    spec reserves for exactly that.
    """
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout

    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            # Unparseable input has no id to answer against, so there is nothing
            # to reply to. Say so on stderr and keep the session alive.
            print(
                f"rendercheck mcp: ignoring unparseable line: {line[:120]}",
                file=sys.stderr,
            )
            continue
        response = handle(message)
        if response is not None:
            sink.write(json.dumps(response) + "\n")
            sink.flush()
    return 0
