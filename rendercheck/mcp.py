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
        "expect_width": {
            "type": "number",
            "description": "Picture width the render was asked for, in pixels.",
        },
        "expect_height": {
            "type": "number",
            "description": "Picture height the render was asked for, in pixels.",
        },
        "expect_fps": {
            "type": "number",
            "description": (
                "Frame rate the render was asked for. Also catches variable "
                "frame rate, a standard cause of audio drifting against picture."
            ),
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
    from .cli import EXIT_OK, FAIL, SKIP, _expand, _parse, _plan, _run, _verdict

    raw = str(arguments.get("path", "")).strip()
    if not raw:
        # `Path("")` is the current directory, which exists -- so without this
        # an argument-less call silently checks whatever the server was started
        # in and reports on it as though it had been asked to.
        raise ValueError("check_media needs a path; none was given")
    path = Path(raw)
    if not path.exists():
        raise FileNotFoundError(f"no such file: {path}")

    # Validated here rather than left to argparse: `choices` failures call
    # `parser.error()`, which raises SystemExit -- a BaseException that no
    # `except Exception` catches, so a model guessing a preset name would take
    # the whole server down instead of getting a correctable message back.
    if arguments.get("preset"):
        presets.get(str(arguments["preset"]))

    argv = ["check", str(path)]
    for flag, key in (
        ("--script", "script"),
        ("--captions", "captions"),
        ("--preset", "preset"),
    ):
        if arguments.get(key):
            # `--flag=value` rather than two words: a value beginning with `-`
            # is otherwise read as another flag, and argparse exits on it.
            argv.append(f"{flag}={arguments[key]}")
    for flag, key in (
        ("--expect-seconds", "expected_seconds"),
        ("--expect-width", "expect_width"),
        ("--expect-height", "expect_height"),
        ("--expect-fps", "expect_fps"),
    ):
        if arguments.get(key) is not None:
            number = arguments[key]
            if not isinstance(number, int | float) or isinstance(number, bool):
                raise ValueError(f"{key} must be a number, got {number!r}")
            argv.append(f"{flag}={number}")
    strict = bool(arguments.get("strict"))
    if strict:
        argv.append("--strict")

    # A config file in the server's working directory is not this agent's
    # intent, and it would change verdicts invisibly across sessions.
    args = _parse(argv, use_config=False)
    targets = _expand([path])
    if not targets:
        # A directory holding no media measured nothing. Reporting that as
        # `ok: true, clean` tells an agent whose render step wrote no files that
        # its render is fine -- which is the failure this library is named after,
        # committed by the surface built for agents.
        raise ValueError(
            f"no media files found in {path} -- nothing was measured, so there "
            f"is no verdict to give"
        )

    files: list[dict[str, Any]] = []
    worst = EXIT_OK
    for target in targets:
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
    from . import media, text, vision
    from .cli import _UNITS

    # Enumerated from the assertions themselves, not from the CLI's unit table:
    # that table exists to label a measured number, so checks that report no
    # number (speaker, format, looks ok) were silently absent from a listing
    # documented as returning every check.
    checks = {
        "pace": media.assert_pace,
        "loudness": media.assert_loudness,
        "true peak": media.assert_true_peak,
        "duration": media.assert_duration,
        "dead air": media.assert_no_dead_air,
        "truncation": media.assert_no_truncation,
        "clipping": media.assert_no_clipping,
        "has sound": media.assert_has_sound,
        "black frames": media.assert_no_black_frames,
        "frozen": media.assert_not_frozen,
        "captions": media.assert_captions_aligned,
        "streams": media.assert_streams_aligned,
        "format": media.assert_format,
        "speaker": text.assert_speaker,
        "looks ok": vision.looks_ok,
    }
    return {
        "checks": [
            {
                "check": name,
                "reports": _UNITS.get(name, "pass or fail only"),
                "about": (check.__doc__ or "").strip().splitlines()[0],
            }
            for name, check in sorted(checks.items())
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


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def handle(message: Any) -> dict[str, Any] | None:
    """One request in, one response out. None for notifications.

    Takes `Any`, not a dict: what arrives is whatever the other end sent. A
    batch (a list, legal in the older protocol revisions this server accepts) or
    a bare scalar has to come back as an error, not as an AttributeError that
    ends the session.
    """
    if not isinstance(message, dict):
        return _error(None, -32600, "expected a single JSON-RPC object")

    method = message.get("method")
    request_id = message.get("id")
    # `"params": null` is valid JSON-RPC and means the same as omitting it.
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    # Notifications carry no id and must never be answered -- a response to one
    # is a protocol error, and some clients drop the connection over it.
    if request_id is None:
        return None

    def reply(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    if method == "initialize":
        asked = str(params.get("protocolVersion", KNOWN[0]))
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
        arguments = params.get("arguments")
        try:
            return reply(
                _call(
                    str(params.get("name", "")),
                    arguments if isinstance(arguments, dict) else {},
                )
            )
        except SystemExit as exc:
            # argparse calls sys.exit() on a value it will not accept, and
            # SystemExit is a BaseException -- `except Exception` sails straight
            # past it and the process dies mid-session. The arguments came from
            # a model, so a rejected one has to be a correctable answer.
            rejected = f"rendercheck rejected those arguments (exit {exc.code})"
            return reply(_result({"error": rejected}, failed=True))
        except Exception as exc:
            # A tool that failed is a result, not a transport error: the model
            # should see what went wrong and fix its arguments, and a JSON-RPC
            # error would be reported to the user as a broken server instead.
            return reply(
                _result({"error": f"{type(exc).__name__}: {exc}"}, failed=True)
            )

    return _error(request_id, -32601, f"method not found: {method}")


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
        try:
            response = handle(message)
        except BaseException as exc:
            # Last line of defence. A server that dies mid-session looks to the
            # client like the tool is broken, with no clue which request did it;
            # an error response says so and the session carries on. BaseException
            # rather than Exception because SystemExit is the realistic one here.
            if isinstance(exc, KeyboardInterrupt):
                raise
            response = _error(
                message.get("id") if isinstance(message, dict) else None,
                -32603,
                f"internal error: {type(exc).__name__}: {exc}",
            )
        if response is not None:
            sink.write(json.dumps(response) + "\n")
            sink.flush()
    return 0
