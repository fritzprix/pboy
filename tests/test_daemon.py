"""Daemon FastAPI skeleton and inject API tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from pboy.daemon.app import create_app
from pboy.models.hook import HookCreate


@dataclass
class DaemonHarness:
    client: AsyncClient
    app: FastAPI


@pytest_asyncio.fixture
async def daemon(tmp_path: Path) -> AsyncGenerator[DaemonHarness, None]:
    """Async client with Engine already started (manual lifespan for older httpx)."""
    app = create_app(db_path=tmp_path / "daemon.db", secret_key="daemon-test-key")
    await app.state.engine.start()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield DaemonHarness(client=client, app=app)
    finally:
        await app.state.engine.stop()


@pytest.mark.asyncio
async def test_healthz_ok(daemon: DaemonHarness) -> None:
    res = await daemon.client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "pboy-daemon"
    assert "daemon.db" in body["db_path"]


@pytest.mark.asyncio
async def test_inject_event_dispatches_hook(
    daemon: DaemonHarness, sample_jira_payload: dict
) -> None:
    engine = daemon.app.state.engine
    await engine.store.create_hook(
        HookCreate(
            name="inject-echo",
            topic="jira:issue.created",
            exec="echo inject-ok",
            timeout=30,
        )
    )

    res = await daemon.client.post(
        "/v1/events",
        json={"source": "jira", "payload": sample_jira_payload, "dispatch": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "jira"
    assert body["matched_hooks"] == ["inject-echo"]
    assert len(body["executions"]) == 1
    assert body["executions"][0]["status"] == "completed"
    assert "inject-ok" in (body["executions"][0]["stdout"] or "")

    stored = await engine.store.get_event(body["event_id"])
    assert stored is not None


@pytest.mark.asyncio
async def test_inject_event_dispatch_false(
    daemon: DaemonHarness, sample_jira_payload: dict
) -> None:
    engine = daemon.app.state.engine
    await engine.store.create_hook(
        HookCreate(name="no-run", topic="jira:issue.created", exec="echo should-not-run")
    )
    res = await daemon.client.post(
        "/v1/events",
        json={"source": "jira", "payload": sample_jira_payload, "dispatch": False},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["matched_hooks"] == ["no-run"]
    assert body["executions"] == []


@pytest.mark.asyncio
async def test_inject_event_invalid_source(daemon: DaemonHarness) -> None:
    res = await daemon.client.post(
        "/v1/events",
        json={"source": "unknown-platform", "payload": {}},
    )
    assert res.status_code == 422
