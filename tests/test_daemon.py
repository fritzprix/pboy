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
from tests.helpers import py_print


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
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
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
            exec=py_print("inject-ok"),
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


def test_assert_loopback_bind_allows_loopback() -> None:
    from pboy.daemon.guard import is_loopback_bind
    from pboy.daemon.server import assert_loopback_bind

    assert is_loopback_bind("127.0.0.1")
    assert is_loopback_bind("localhost")
    assert is_loopback_bind("[::1]")
    assert is_loopback_bind("127.0.1.1")
    assert not is_loopback_bind("0.0.0.0")
    assert not is_loopback_bind("::")
    assert not is_loopback_bind("127.evil.com")
    assert_loopback_bind("127.0.0.1")
    assert_loopback_bind("::1")
    assert_loopback_bind("[::1]")
    assert_loopback_bind("127.0.1.1")


def test_is_loopback_peer_fail_closed() -> None:
    from pboy.daemon.guard import is_loopback_peer

    assert is_loopback_peer("") is False
    assert is_loopback_peer("127.0.0.1") is True
    assert is_loopback_peer("127.0.1.1") is True
    assert is_loopback_peer("testclient") is True
    assert is_loopback_peer("203.0.113.10") is False
    assert is_loopback_peer("127.evil.com") is False


def test_assert_loopback_bind_rejects_wildcard_and_lan() -> None:
    from pboy.daemon.server import assert_loopback_bind

    with pytest.raises(ValueError, match="local-only"):
        assert_loopback_bind("0.0.0.0")
    with pytest.raises(ValueError, match="local-only"):
        assert_loopback_bind("::")
    with pytest.raises(ValueError, match="local-only"):
        assert_loopback_bind("192.168.1.10")
    with pytest.raises(ValueError, match="local-only"):
        assert_loopback_bind("127.evil.com")


def test_is_local_hostname_rejects_127_prefix_dns() -> None:
    """``127.evil.com`` must not pass the Host allowlist (DNS rebinding)."""
    from pboy.daemon.guard import is_local_hostname

    assert is_local_hostname("127.0.0.1") is True
    assert is_local_hostname("127.0.1.1") is True
    assert is_local_hostname("localhost") is True
    assert is_local_hostname("::1") is True
    assert is_local_hostname("127.evil.com") is False
    assert is_local_hostname("evil.example") is False
    assert is_local_hostname("test") is False
    assert is_local_hostname("testserver") is False


def test_check_request_local_only_rules() -> None:
    from pboy.daemon.guard import check_request

    assert (
        check_request(
            peer="127.0.0.1",
            host_header="127.0.0.1:8080",
            method="GET",
            origin=None,
            sec_fetch_site="",
        )
        is None
    )
    assert (
        check_request(
            peer="203.0.113.10",
            host_header="127.0.0.1",
            method="GET",
            origin=None,
            sec_fetch_site="",
        )
        is not None
    )
    assert (
        check_request(
            peer="127.0.0.1",
            host_header="evil.example",
            method="GET",
            origin=None,
            sec_fetch_site="",
        )
        is not None
    )
    assert (
        check_request(
            peer="127.0.0.1",
            host_header="127.evil.com:8080",
            method="GET",
            origin=None,
            sec_fetch_site="",
        )
        is not None
    )
    assert (
        check_request(
            peer="127.0.0.1",
            host_header="127.0.0.1",
            method="POST",
            origin=None,
            sec_fetch_site="cross-site",
        )
        is not None
    )
    assert (
        check_request(
            peer="127.0.0.1",
            host_header="127.0.0.1",
            method="POST",
            origin="http://127.0.0.1",
            sec_fetch_site="same-origin",
        )
        is None
    )
    assert (
        check_request(
            peer="127.0.0.1",
            host_header="127.0.0.1",
            method="POST",
            origin="https://evil.example",
            sec_fetch_site="same-origin",
        )
        is not None
    )
    # WebSocket: missing Origin blocked; matching Origin allowed.
    assert (
        check_request(
            peer="127.0.0.1",
            host_header="127.0.0.1",
            method="WEBSOCKET",
            origin=None,
            sec_fetch_site="",
        )
        is not None
    )
    assert (
        check_request(
            peer="127.0.0.1",
            host_header="127.0.0.1",
            method="WEBSOCKET",
            origin="http://127.0.0.1",
            sec_fetch_site="",
        )
        is None
    )
    assert (
        check_request(
            peer="127.0.0.1",
            host_header="127.0.0.1",
            method="WEBSOCKET",
            origin="https://evil.example",
            sec_fetch_site="",
        )
        is not None
    )


def test_run_daemon_exits_on_non_loopback_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pboy.core.config as config_mod
    from pboy.daemon import server as server_mod

    monkeypatch.setattr(config_mod, "_settings", None)

    def _fail_uvicorn(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("uvicorn.run must not be called")

    monkeypatch.setattr(server_mod.uvicorn, "run", _fail_uvicorn)
    with pytest.raises(SystemExit) as excinfo:
        server_mod.run_daemon(host="0.0.0.0", port=8080)
    assert excinfo.value.code == 1


@pytest.mark.asyncio
async def test_openapi_schema_documents_sync_and_inject_payload(
    daemon: DaemonHarness,
) -> None:
    res = await daemon.client.get("/openapi.json")
    assert res.status_code == 200
    schemas = res.json()["components"]["schemas"]

    complete_props = schemas["EventCompleteRequest"]["properties"]
    sync_prop = complete_props["sync"]
    assert sync_prop["type"] == "array"
    assert sync_prop["items"] == {"type": "string"}
    assert "description" in sync_prop
    assert sync_prop["examples"] == [["telegram"]]

    inject_props = schemas["EventInjectRequest"]["properties"]
    payload_prop = inject_props["payload"]
    assert "description" in payload_prop
    assert payload_prop["examples"][0]["message"]["text"] == "hello from curl"
