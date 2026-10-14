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


def test_assert_bind_auth_policy_allows_loopback_without_token() -> None:
    from pboy.daemon.server import assert_bind_auth_policy, is_loopback_bind

    assert is_loopback_bind("127.0.0.1")
    assert is_loopback_bind("localhost")
    assert is_loopback_bind("[::1]")
    assert is_loopback_bind("127.0.1.1")
    assert not is_loopback_bind("0.0.0.0")
    assert not is_loopback_bind("::")
    assert_bind_auth_policy("127.0.0.1", "")
    assert_bind_auth_policy("::1", "")
    assert_bind_auth_policy("[::1]", "")
    assert_bind_auth_policy("127.0.1.1", "")


def test_is_loopback_peer_fail_closed() -> None:
    from pboy.daemon.auth import _is_loopback

    assert _is_loopback("") is False
    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("127.0.1.1") is True
    assert _is_loopback("203.0.113.10") is False


def test_assert_bind_auth_policy_requires_token_for_wildcard() -> None:
    from pboy.daemon.server import assert_bind_auth_policy

    with pytest.raises(ValueError, match="PB_AUTH_TOKEN"):
        assert_bind_auth_policy("0.0.0.0", "")
    with pytest.raises(ValueError, match="PB_AUTH_TOKEN"):
        assert_bind_auth_policy("::", "  ")
    assert_bind_auth_policy("0.0.0.0", "secret")
    assert_bind_auth_policy("192.168.1.10", "secret")


def test_run_daemon_exits_when_wildcard_bind_lacks_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pboy.core.config as config_mod
    from pboy.daemon import server as server_mod

    monkeypatch.delenv("PB_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(config_mod, "_settings", None)

    def _fail_uvicorn(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("uvicorn.run must not be called")

    monkeypatch.setattr(server_mod.uvicorn, "run", _fail_uvicorn)
    with pytest.raises(SystemExit) as excinfo:
        server_mod.run_daemon(host="0.0.0.0", port=8080)
    assert excinfo.value.code == 1
