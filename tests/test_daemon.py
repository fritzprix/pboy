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


def test_run_daemon_logs_ui_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from loguru import logger

    from pboy.daemon import server as server_mod

    logs: list[str] = []
    sink_id = logger.add(lambda msg: logs.append(str(msg)), format="{message}")
    try:
        monkeypatch.setattr(server_mod.uvicorn, "run", lambda *a, **k: None)
        monkeypatch.setattr(server_mod, "probe_daemon_alive", lambda *_a, **_k: False)
        server_mod.run_daemon(host="127.0.0.1", port=8080, db=tmp_path / "d.db", ui=True)
        assert any("UI URL: http://127.0.0.1:8080/ui" in msg for msg in logs)
    finally:
        logger.remove(sink_id)


def test_run_daemon_refuses_when_legacy_8080_also_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Starting on default port while :8080 daemon runs must refuse (prevents double-polling)."""
    from pboy.cli.daemon_client import DEFAULT_DAEMON_URL, LEGACY_DAEMON_URL
    from pboy.core.config import DEFAULT_PORT
    from pboy.daemon import server as server_mod

    if DEFAULT_PORT == 8080:
        pytest.skip("legacy conflict check only applies when default port is not 8080")

    monkeypatch.setattr(server_mod.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(
        server_mod,
        "probe_daemon_alive",
        lambda url, timeout=0.15: url.rstrip("/") == LEGACY_DAEMON_URL.rstrip("/"),
    )

    with pytest.raises(SystemExit) as exc_info:
        server_mod.run_daemon(
            host="127.0.0.1", port=DEFAULT_PORT, db=tmp_path / "d.db", ui=False
        )

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert LEGACY_DAEMON_URL in err
    assert DEFAULT_DAEMON_URL in err
    assert "double-poll" in err
    # Verify all suggested commands are valid
    assert "pboy daemon" in err
    assert f"{LEGACY_DAEMON_URL}/ui" in err
    assert "pboy ui --port 8080" in err
    assert "--allow-dual-daemon" in err


def test_run_daemon_allows_dual_with_explicit_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--allow-dual-daemon must override the conflict check."""
    from pboy.cli.daemon_client import LEGACY_DAEMON_URL
    from pboy.core.config import DEFAULT_PORT
    from pboy.daemon import server as server_mod

    if DEFAULT_PORT == 8080:
        pytest.skip("legacy conflict check only applies when default port is not 8080")

    monkeypatch.setattr(server_mod.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(
        server_mod,
        "probe_daemon_alive",
        lambda url, timeout=0.15: url.rstrip("/") == LEGACY_DAEMON_URL.rstrip("/"),
    )

    # Should not raise
    server_mod.run_daemon(
        host="127.0.0.1", port=DEFAULT_PORT, db=tmp_path / "d.db", ui=False, allow_dual_daemon=True
    )

    err = capsys.readouterr().err
    # Should not print error
    assert "Error:" not in err


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
    assert sync_prop["examples"] == [["telegram"], ["slack"]]

    inject_props = schemas["EventInjectRequest"]["properties"]
    payload_prop = inject_props["payload"]
    assert "description" in payload_prop
    assert payload_prop["examples"][0]["message"]["text"] == "hello from curl"
