"""MCP server surface and backend (daemon-backed) tests."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
import uvicorn
from mcp.server.mcpserver.exceptions import ToolError
from typer.testing import CliRunner

import pboy.core.config as config_mod
from pboy.cli.daemon_client import DaemonClient
from pboy.daemon.app import create_app
from pboy.mcp.backend import McpBackend, McpBackendError
from pboy.mcp.server import TOOL_NAMES, create_mcp_server
from pboy.models.hook import HookCreate


@pytest.fixture
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setattr(config_mod, "_settings", None)
    yield
    monkeypatch.setattr(config_mod, "_settings", None)


@pytest.fixture
def live_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_settings: None
) -> Generator[str, None, None]:
    monkeypatch.setattr(config_mod, "_settings", None)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    application = create_app(db_path=tmp_path / "mcp_live.db", secret_key="mcp-live-key")
    config = uvicorn.Config(application, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("uvicorn failed to start")

    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.asyncio
async def test_tool_allowlist_is_curated() -> None:
    server = create_mcp_server(backend=McpBackend(daemon_url="http://127.0.0.1:9"))
    names = {t.name for t in await server.list_tools()}
    assert names == TOOL_NAMES
    assert "inject_event" not in names
    assert "put_secret" not in names
    assert "complete_event" not in names


def test_backend_hooks_events_reply_via_daemon(live_daemon: str) -> None:
    seed = DaemonClient(live_daemon)
    seed.create_hook(
        HookCreate(name="echo", topic="telegram:message", exec="echo hi", timeout=30)
    )
    injected = seed.inject_event(
        "telegram",
        {
            "message": {
                "message_id": 1,
                "chat": {"id": 111},
                "from": {"id": 222, "username": "alice"},
                "text": "ping",
            }
        },
        dispatch=False,
    )
    eid = injected["event_id"]

    backend = McpBackend(daemon_url=live_daemon)
    hooks = backend.list_hooks()
    assert any(h["name"] == "echo" for h in hooks["hooks"])

    events = backend.list_events(limit=10)
    assert any(e["id"] == eid for e in events["events"])

    detail = backend.get_event(eid)
    assert detail["id"] == eid
    assert detail["source"] == "telegram"

    result = backend.reply_event(eid, summary="pong")
    assert result["event_id"] == eid
    assert result["summary"] == "pong"

    registered = backend.register_hook(
        name="echo2",
        topic="slack:message",
        command="echo 2",
        timeout=15,
    )
    assert registered["name"] == "echo2"
    assert backend.delete_hook("echo2") == {"deleted": True, "name": "echo2"}

    with pytest.raises(McpBackendError, match="not found"):
        backend.get_event("evt_missing")

    with pytest.raises(McpBackendError, match="not found"):
        backend.delete_hook("no-such-hook")


@pytest.mark.asyncio
async def test_mcp_call_tool_status_and_list(live_daemon: str) -> None:
    seed = DaemonClient(live_daemon)
    seed.create_hook(
        HookCreate(name="echo", topic="telegram:message", exec="echo hi", timeout=30)
    )
    server = create_mcp_server(backend=McpBackend(daemon_url=live_daemon))

    status = await server.call_tool("status", {})
    assert status.is_error is False

    listed = await server.call_tool("list_hooks", {})
    assert listed.is_error is False
    assert listed.content is not None
    text = listed.content[0].text
    assert "echo" in text

    with pytest.raises(ToolError, match="not found"):
        await server.call_tool("get_event", {"event_id": "evt_missing"})


def test_cli_mcp_help() -> None:
    from pboy.cli.main import app

    runner = CliRunner()
    res = runner.invoke(app, ["mcp", "--help"])
    assert res.exit_code == 0
    assert "MCP" in res.output
    assert "list_events" in res.output
