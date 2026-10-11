"""Tests for daemon-aware CLI client, auth, and live DaemonClient HTTP."""

from __future__ import annotations

import importlib
import socket
import threading
import time
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

import pboy.core.config as config_mod
from pboy.cli.daemon_client import DaemonClient, resolve_auth_token, resolve_daemon_url
from pboy.core.config import Settings, get_settings
from pboy.daemon.app import create_app
from pboy.daemon.server import _public_daemon_url
from pboy.models.cloudevent import CloudEvent
from pboy.models.hook import Hook, HookCreate

cli_main = importlib.import_module("pboy.cli.main")


@dataclass
class DaemonHarness:
    client: AsyncClient
    app: FastAPI


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setattr(config_mod, "_settings", None)
    yield
    monkeypatch.setattr(config_mod, "_settings", None)


def test_resolve_daemon_url_requires_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PB_DAEMON_URL", raising=False)
    assert resolve_daemon_url() is None
    monkeypatch.setenv("PB_DAEMON_URL", "  http://127.0.0.1:9999  ")
    assert resolve_daemon_url() == "http://127.0.0.1:9999"


def test_public_daemon_url_maps_wildcard_bind() -> None:
    assert _public_daemon_url("0.0.0.0", 9090) == "http://127.0.0.1:9090"
    assert _public_daemon_url("127.0.0.1", 8080) == "http://127.0.0.1:8080"


def test_cli_package_does_not_shadow_main_module() -> None:
    mod = importlib.import_module("pboy.cli.main")
    import pboy.cli as cli_pkg

    # Package attribute must resolve to the submodule, not the entry function.
    assert cli_pkg.main is mod
    assert hasattr(mod, "app")
    assert callable(mod.main)


@pytest_asyncio.fixture
async def daemon(tmp_path: Path) -> AsyncGenerator[DaemonHarness, None]:
    application = create_app(db_path=tmp_path / "daemon_cli.db", secret_key="daemon-cli-key")
    await application.state.engine.start()
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield DaemonHarness(client=client, app=application)
    finally:
        await application.state.engine.stop()


@pytest.mark.asyncio
async def test_daemon_api_state_events_hooks(daemon: DaemonHarness) -> None:
    create = await daemon.client.post(
        "/v1/hooks",
        json={
            "name": "h1",
            "topic": "jira:issue.created",
            "exec": "echo 1",
            "concurrency": 1,
            "timeout": 30,
        },
    )
    assert create.status_code == 200
    listed_hooks = await daemon.client.get("/v1/hooks")
    assert listed_hooks.status_code == 200
    assert any(h["name"] == "h1" for h in listed_hooks.json()["hooks"])

    event = CloudEvent(
        source="jira",
        type="com.atlassian.jira.issue.created",
        data={"priority": "High"},
    )
    await daemon.app.state.engine.store.create_event(event)

    put = await daemon.client.put(
        f"/v1/events/{event.id}/state",
        json={"key": "execution", "value": {"status": "running", "progress": 40}},
    )
    assert put.status_code == 200

    got = await daemon.client.get(f"/v1/events/{event.id}/state", params={"key": "execution"})
    assert got.json()["value"] == {"status": "running", "progress": 40}

    deleted = await daemon.client.delete("/v1/hooks/h1")
    assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_mutating_requires_token_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_settings: None
) -> None:
    monkeypatch.setenv("PB_AUTH_TOKEN", "secret-token")
    monkeypatch.setattr(config_mod, "_settings", None)
    application = create_app(db_path=tmp_path / "auth.db", secret_key="k")
    await application.state.engine.start()
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post(
                "/v1/hooks",
                json={"name": "x", "topic": "jira:issue.created", "exec": "echo 1"},
            )
            assert denied.status_code == 401
            ok = await client.post(
                "/v1/hooks",
                headers={"X-PB-Token": "secret-token"},
                json={"name": "x", "topic": "jira:issue.created", "exec": "echo 1"},
            )
            assert ok.status_code == 200
    finally:
        await application.state.engine.stop()


@pytest.fixture
def live_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_settings: None
) -> Generator[str, None, None]:
    """Real HTTP daemon on an ephemeral port for DaemonClient coverage."""
    monkeypatch.setenv("PB_AUTH_TOKEN", "live-token")
    monkeypatch.setattr(config_mod, "_settings", None)
    settings = get_settings()
    assert settings.auth_token == "live-token"

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    application = create_app(db_path=tmp_path / "live.db", secret_key="live-key")
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


def test_real_daemon_client_against_live_http(live_daemon: str) -> None:
    client = DaemonClient(live_daemon, auth_token="live-token")
    hook = client.create_hook(
        HookCreate(name="live-hook", topic="jira:issue.created", exec="echo ok", timeout=30)
    )
    assert hook.name == "live-hook"
    assert any(h.name == "live-hook" for h in client.list_hooks())

    # Create event via store inject path on daemon
    inject = client._request(
        "POST",
        "/v1/events",
        json={
            "source": "jira",
            "payload": {
                "webhookEvent": "jira:issue_created",
                "issue": {
                    "id": "1",
                    "key": "L-1",
                    "fields": {
                        "summary": "s",
                        "priority": {"name": "High"},
                        "project": {"key": "L"},
                        "creator": {"accountId": "u"},
                        "labels": [],
                    },
                },
            },
            "dispatch": False,
        },
    )
    assert inject.status_code == 200
    event_id = inject.json()["event_id"]
    client.put_state(event_id, "execution", {"status": "running", "progress": 5})
    assert client.get_state(event_id) == {"status": "running", "progress": 5}
    assert any(e.id == event_id for e in client.list_events())
    assert client.get_event(event_id) is not None
    assert client.delete_hook("live-hook") is True


def test_cli_hook_uses_daemon_when_env_set(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[HookCreate] = []

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            assert base_url == "http://daemon.test"

        def create_hook(self, data: HookCreate) -> Hook:
            created.append(data)
            return Hook(**data.model_dump())

    monkeypatch.setenv("PB_DAEMON_URL", "http://daemon.test")
    monkeypatch.setattr(cli_main, "DaemonClient", FakeClient)

    res = cli_runner.invoke(
        cli_main.app,
        [
            "hook",
            "register",
            "--name",
            "via-daemon",
            "--topic",
            "jira:issue.created",
            "--exec",
            "echo 1",
        ],
    )
    assert res.exit_code == 0, res.output
    assert created and created[0].name == "via-daemon"


def test_cli_state_uses_sqlite_without_daemon_env(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reset_settings: None,
) -> None:
    monkeypatch.delenv("PB_DAEMON_URL", raising=False)
    cfg = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data", secret_key="cli-local")
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    res = cli_runner.invoke(
        cli_main.app,
        ["state", "put", "--event-id", "evt_local", "--status", "running", "--progress", "10"],
    )
    assert res.exit_code == 0, res.output
    get_res = cli_runner.invoke(cli_main.app, ["state", "get", "evt_local"])
    assert get_res.exit_code == 0
    assert "running" in get_res.output


def test_resolve_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PB_AUTH_TOKEN", raising=False)
    assert resolve_auth_token() is None
    monkeypatch.setenv("PB_AUTH_TOKEN", " abc ")
    assert resolve_auth_token() == "abc"
