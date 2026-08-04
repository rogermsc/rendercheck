"""The MCP server, driven the way a client drives it.

The failure this suite exists for: stdout on this transport carries the
protocol, and every reporting path in the CLI prints. One stray human-readable
line and the session is corrupt -- so the tests parse *every* line rather than
looking for the one they wanted.
"""

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rendercheck import __version__
from rendercheck.mcp import KNOWN, TOOLS, handle, serve
from tests.test_checks import NOAUDIO, TONE


def session(*messages: dict[str, Any]) -> list[dict[str, Any]]:
    """Run a whole conversation through the server and parse what came back."""
    out = io.StringIO()
    serve(io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n"), out)
    # json.loads on every line, not just the interesting ones: a print() that
    # leaked into stdout would break here, which is the point.
    return [json.loads(line) for line in out.getvalue().splitlines() if line]


def call(name, **arguments):
    replies = session(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    return replies[0]["result"]


def test_initialize_answers_with_the_version_the_client_asked_for():
    reply = session(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        }
    )[0]
    assert reply["result"]["protocolVersion"] == "2025-03-26", reply
    assert reply["result"]["serverInfo"]["version"] == __version__, reply


def test_an_unknown_protocol_version_still_gets_a_usable_server():
    # Refusing a connection over a date would be a worse answer than serving
    # one: nothing here depends on the parts of the spec that changed.
    reply = session(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2099-01-01"},
        }
    )[0]
    assert reply["result"]["protocolVersion"] == KNOWN[0], reply


def test_a_notification_is_never_answered():
    # A response to a notification is a protocol error, and some clients drop
    # the connection over it.
    assert session({"jsonrpc": "2.0", "method": "notifications/initialized"}) == []
    assert handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None


def test_tools_are_listed_with_schemas():
    tools = session({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0]["result"]
    assert {t["name"] for t in tools["tools"]} == {"check_media", "list_checks"}
    for tool in tools["tools"]:
        assert tool["inputSchema"]["type"] == "object", tool


def test_check_media_returns_the_same_keys_as_json_output():
    # A consumer parsing `--json` must parse this unchanged.
    result = call("check_media", path=str(TONE))
    report = result["structuredContent"]["files"][0]
    assert set(report) == {"file", "results", "failed", "skipped"}, report
    assert set(report["results"][0]) == {"status", "check", "detail"}, report


def test_a_defect_is_reported_as_a_verdict_not_as_a_transport_error():
    result = call("check_media", path=str(NOAUDIO))
    body = result["structuredContent"]
    assert body["ok"] is False, body
    assert body["exit_code"] == 1, body
    # isError is for a tool that could not run. A file that failed its checks
    # ran perfectly -- the answer is just "no".
    assert result["isError"] is False, result


def test_a_missing_file_is_an_error_the_model_can_act_on():
    result = call("check_media", path="/definitely/not/here.wav")
    assert result["isError"] is True, result
    assert "FileNotFoundError" in result["structuredContent"]["error"], result


def test_an_unknown_tool_does_not_kill_the_session():
    replies = session(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    assert replies[0]["result"]["isError"] is True, replies[0]
    assert len(replies) == 2, replies  # the session carried on


def test_an_unknown_method_is_a_json_rpc_error():
    reply = session({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})[0]
    assert reply["error"]["code"] == -32601, reply


def test_unparseable_input_does_not_end_the_session():
    out = io.StringIO()
    source = io.StringIO(
        "{not json\n"
        + json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        + "\n"
    )
    serve(source, out)
    replies = [json.loads(line) for line in out.getvalue().splitlines() if line]
    assert len(replies) == 1 and replies[0]["id"] == 7, replies


def test_list_checks_describes_the_presets_it_accepts():
    # The schema the model is shown and the table it is told about have to be
    # the same set, or it will offer an argument the tool then rejects.
    body = call("list_checks")["structuredContent"]
    schema: Any = TOOLS[0]["inputSchema"]
    accepted = schema["properties"]["preset"]["enum"]
    assert set(body["presets"]) == set(accepted), body["presets"]


def test_the_server_runs_as_a_subprocess_with_clean_stdout():
    # The in-process tests share this interpreter's stdout. This one does not:
    # it is the only way to catch something printing straight to fd 1.
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "check_media", "arguments": {"path": str(TONE)}},
        }
    )
    proc = subprocess.run(
        [sys.executable, "-m", "rendercheck.cli", "mcp"],
        input=request + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
    for line in proc.stdout.splitlines():
        json.loads(line)


if __name__ == "__main__":
    failures = 0
    for name, test in sorted(dict(globals()).items()):
        if name.startswith("test_") and callable(test):
            try:
                test()
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("mcp ok" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)
