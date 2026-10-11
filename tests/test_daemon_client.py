"""Tests for daemon-aware CLI client and dual-mode CLI commands."""

from __future__ import annotations

import importlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from pboy.cli.daemon_client import resolve_daemon_url
from pboy.daemon.app import create_app
from pboy.models.cloudevent import CloudEvent

cli_main = importlib.import_module("pboy.cli.main")


@dataclass
class DaemonHarness:
    client: AsyncClient
    app: FastAPI


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_resolve_daemon_url_requires_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PB_DAEMON_URL", raising=False)
    assert resolve_daemon_url() is None
    monkeypatch.setenv("PB_DAEMON_URL", "  http://127.0.0.1:9999  ")
    assert resolve_daemon_url() == "http://127.0.0.1:9999"
    monkeypatch.setenv("PB_DAEMON_URL", "   ")
    assert resolve_daemon_url() is None


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
async def test_daemon_api_state_and_events(daemon: DaemonHarness) -> None:
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
    assert got.status_code == 200
    assert got.json()["value"] == {"status": "running", "progress": 40}

    listed = await daemon.client.get("/v1/events")
    assert listed.status_code == 200
    assert any(item["id"] == event.id for item in listed.json()["events"])

    fetched = await daemon.client.get(f"/v1/events/{event.id}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["priority"] == "High"

    missing = await daemon.client.get("/v1/events/missing")
    assert missing.status_code == 404


def test_cli_state_uses_daemon_when_env_set(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            assert base_url == "http://daemon.test"

        def put_state(self, event_id: str, key: str, value: dict[str, Any]) -> dict[str, Any]:
            calls.append((event_id, key, value))
            return {"event_id": event_id, "key": key, "value": value}

    monkeypatch.setenv("PB_DAEMON_URL", "http://daemon.test")
    monkeypatch.setattr(cli_main, "DaemonClient", FakeClient)

    res = cli_runner.invoke(
        cli_main.app,
        ["state", "put", "--event-id", "evt_1", "--status", "running", "--progress", "25"],
    )
    assert res.exit_code == 0, res.output
    assert calls == [
        ("evt_1", "execution", {"status": "running", "progress": 25, "broadcast": False})
    ]
    assert "State updated" in res.output


def test_cli_state_uses_sqlite_without_daemon_env(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PB_DAEMON_URL", raising=False)
    import pboy.core.config as config_mod
    from pboy.core.config import Settings

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
