"""Tests for daemon-aware CLI client, local-only guard, and live DaemonClient HTTP."""

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
from pboy.cli.daemon_client import DEFAULT_DAEMON_URL, DaemonClient, resolve_daemon_url
from pboy.core.config import DEFAULT_PORT, Settings
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
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setattr(config_mod, "_settings", None)
    yield
    monkeypatch.setattr(config_mod, "_settings", None)


def test_resolve_daemon_url_cli_flag_and_auto_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    import pboy.cli.daemon_client as dc_mod

    monkeypatch.delenv("PB_DATABASE_URL", raising=False)
    # When no daemon is running at all, returns None:
    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", lambda _url, timeout=0.15: False)
    assert resolve_daemon_url() is None
    assert resolve_daemon_url("  http://127.0.0.1:9999/  ") == "http://127.0.0.1:9999"

    # When default daemon is alive and no custom PB_DATABASE_URL is set, auto-connects:
    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", lambda _url, timeout=0.15: True)
    assert resolve_daemon_url() == DEFAULT_DAEMON_URL

    # Explicit PB_DATABASE_URL disables implicit auto-probe unless --daemon-url is given:
    monkeypatch.setenv("PB_DATABASE_URL", "/tmp/custom.db")
    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", lambda _url, timeout=0.15: False)
    assert resolve_daemon_url() is None
    assert resolve_daemon_url(DEFAULT_DAEMON_URL) == DEFAULT_DAEMON_URL


def test_resolve_daemon_url_reuses_legacy_8080(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Upgrade scenario: default port not running, legacy :8080 is up → reuse it."""
    import pboy.cli.daemon_client as dc_mod
    from pboy.cli.daemon_client import LEGACY_DAEMON_URL

    monkeypatch.delenv("PB_DATABASE_URL", raising=False)
    monkeypatch.setattr(dc_mod, "_legacy_daemon_hint_emitted", False)

    def fake_probe(url: str, timeout: float = 0.15) -> bool:
        return url.rstrip("/") == LEGACY_DAEMON_URL.rstrip("/")

    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", fake_probe)

    # Should return the legacy URL, not None
    result = resolve_daemon_url()
    assert result == LEGACY_DAEMON_URL

    # Should print a notice
    err = capsys.readouterr().err
    assert "Notice:" in err
    assert LEGACY_DAEMON_URL in err
    assert DEFAULT_DAEMON_URL in err

    # Once per process — second resolve must not reprint the notice
    result2 = resolve_daemon_url()
    assert result2 == LEGACY_DAEMON_URL
    assert capsys.readouterr().err == ""


def test_public_daemon_url_for_loopback_bind() -> None:
    assert _public_daemon_url("127.0.0.1", DEFAULT_PORT) == DEFAULT_DAEMON_URL
    assert _public_daemon_url("::1", DEFAULT_PORT) == f"http://[::1]:{DEFAULT_PORT}"


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
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
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
async def test_remote_peer_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_settings: None
) -> None:
    monkeypatch.setattr(config_mod, "_settings", None)
    application = create_app(db_path=tmp_path / "remote.db", secret_key="k")
    await application.state.engine.start()
    try:
        loopback = ASGITransport(app=application)
        async with AsyncClient(transport=loopback, base_url="http://127.0.0.1") as client:
            ok_local = await client.post(
                "/v1/hooks",
                json={"name": "local", "topic": "jira:issue.created", "exec": "echo 1"},
            )
            assert ok_local.status_code == 200

        remote = ASGITransport(app=application, client=("203.0.113.10", 50000))
        async with AsyncClient(transport=remote, base_url="http://127.0.0.1") as client:
            denied = await client.post(
                "/v1/hooks",
                json={"name": "x", "topic": "jira:issue.created", "exec": "echo 1"},
            )
            assert denied.status_code == 403
    finally:
        await application.state.engine.stop()


@pytest.mark.asyncio
async def test_local_mode_rejects_non_local_host_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_settings: None
) -> None:
    """Local-only guard must reject DNS-rebinding Host values."""
    monkeypatch.setattr(config_mod, "_settings", None)
    application = create_app(db_path=tmp_path / "rebind.db", secret_key="k")
    await application.state.engine.start()
    try:
        loopback = ASGITransport(app=application)
        async with AsyncClient(transport=loopback, base_url="http://evil.example") as client:
            denied = await client.post(
                "/v1/hooks",
                json={"name": "x", "topic": "jira:issue.created", "exec": "echo 1"},
            )
            assert denied.status_code == 403

        # ``127.`` prefix alone must not count as loopback (classic rebinding name).
        async with AsyncClient(
            transport=loopback, base_url="http://127.evil.com"
        ) as client:
            denied_prefix = await client.post(
                "/v1/hooks",
                json={"name": "y", "topic": "jira:issue.created", "exec": "echo 1"},
            )
            assert denied_prefix.status_code == 403

        async with AsyncClient(transport=loopback, base_url="http://127.0.0.1") as client:
            ok = await client.post(
                "/v1/hooks",
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
    monkeypatch.setattr(config_mod, "_settings", None)

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
    client = DaemonClient(live_daemon)
    hz = client.healthz()
    assert hz["status"] == "ok"
    assert hz["service"] == "pboy-daemon"

    hook = client.create_hook(
        HookCreate(name="live-hook", topic="jira:issue.created", exec="echo ok", timeout=30)
    )
    assert hook.name == "live-hook"
    assert any(h.name == "live-hook" for h in client.list_hooks())

    # Create event via DaemonClient.inject_event on daemon
    injected = client.inject_event(
        "jira",
        {
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
        dispatch=False,
    )
    event_id = injected["event_id"]
    client.put_state(event_id, "execution", {"status": "running", "progress": 5})
    assert client.get_state(event_id) == {"status": "running", "progress": 5}
    assert any(e.id == event_id for e in client.list_events())
    assert client.get_event(event_id) is not None
    assert client.delete_hook("live-hook") is True


def test_cli_hook_uses_daemon_when_flag_passed(
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

    monkeypatch.setattr(cli_main, "DaemonClient", FakeClient)

    res = cli_runner.invoke(
        cli_main.app,
        [
            "hook",
            "register",
            "--daemon-url",
            "http://daemon.test",
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


def test_cli_state_uses_sqlite_when_daemon_not_running(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reset_settings: None,
) -> None:
    import pboy.cli.daemon_client as dc_mod

    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", lambda _url, timeout=0.15: False)
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


def test_cli_hook_register_warns_on_local_sqlite_fallback(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reset_settings: None,
) -> None:
    import pboy.cli.daemon_client as dc_mod

    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", lambda _url, timeout=0.15: False)
    monkeypatch.delenv("PB_DATABASE_URL", raising=False)
    cfg = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data", secret_key="cli-warn")
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    res = cli_runner.invoke(
        cli_main.app,
        [
            "hook",
            "register",
            "--name",
            "offline-hook",
            "--topic",
            "telegram:message",
            "--exec",
            "echo 1",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Warning: no daemon at" in res.output
    assert DEFAULT_DAEMON_URL in res.output
    assert "--daemon-url" in res.output


def test_cli_channel_enable_warns_on_local_sqlite_fallback(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reset_settings: None,
) -> None:
    import pboy.cli.daemon_client as dc_mod

    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", lambda _url, timeout=0.15: False)
    monkeypatch.delenv("PB_DATABASE_URL", raising=False)
    cfg = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data", secret_key="cli-ch-warn")
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    res = cli_runner.invoke(cli_main.app, ["channel", "enable", "telegram"])
    assert res.exit_code == 0, res.output
    assert "Warning: no daemon at" in res.output
    assert DEFAULT_DAEMON_URL in res.output


def test_cli_hook_register_skips_warn_when_pb_database_url_set(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reset_settings: None,
) -> None:
    import pboy.cli.daemon_client as dc_mod

    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", lambda _url, timeout=0.15: False)
    db_path = tmp_path / "explicit.db"
    monkeypatch.setenv("PB_DATABASE_URL", str(db_path))
    cfg = Settings(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "data",
        secret_key="cli-db-url",
        database_url=str(db_path),
    )
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    res = cli_runner.invoke(
        cli_main.app,
        [
            "hook",
            "register",
            "--name",
            "db-url-hook",
            "--topic",
            "telegram:message",
            "--exec",
            "echo 1",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Warning: no daemon at" not in res.output


def test_upgrade_scenario_hook_register_hits_legacy_daemon(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reset_settings: None,
) -> None:
    """Upgrade from 0.4.0: daemon on :8080, CLI must connect to it, not local SQLite."""
    import pboy.cli.daemon_client as dc_mod
    from pboy.cli.daemon_client import LEGACY_DAEMON_URL

    monkeypatch.delenv("PB_DATABASE_URL", raising=False)
    monkeypatch.setattr(dc_mod, "_legacy_daemon_hint_emitted", False)
    cfg = Settings(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "data",
        secret_key="upgrade-test",
    )
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    created_hooks: list[HookCreate] = []

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            assert base_url == LEGACY_DAEMON_URL, f"Expected {LEGACY_DAEMON_URL}, got {base_url}"

        def create_hook(self, data: HookCreate) -> Hook:
            created_hooks.append(data)
            return Hook(**data.model_dump())

    def fake_probe(url: str, timeout: float = 0.15) -> bool:
        return url.rstrip("/") == LEGACY_DAEMON_URL.rstrip("/")

    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", fake_probe)
    monkeypatch.setattr(cli_main, "DaemonClient", FakeClient)

    res = cli_runner.invoke(
        cli_main.app,
        [
            "hook",
            "register",
            "--name",
            "upgrade-hook",
            "--topic",
            "telegram:message",
            "--exec",
            "echo 1",
        ],
    )
    assert res.exit_code == 0, res.output
    assert created_hooks and created_hooks[0].name == "upgrade-hook"
    assert "Notice:" in res.output
    assert LEGACY_DAEMON_URL in res.output


def test_upgrade_scenario_explicit_daemon_url_takes_precedence(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reset_settings: None,
) -> None:
    """Explicit --daemon-url must override legacy auto-connect."""
    import pboy.cli.daemon_client as dc_mod
    from pboy.cli.daemon_client import LEGACY_DAEMON_URL

    monkeypatch.delenv("PB_DATABASE_URL", raising=False)
    cfg = Settings(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "data",
        secret_key="explicit-url",
    )
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    created_hooks: list[HookCreate] = []

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            assert base_url == "http://127.0.0.1:9999", f"Expected custom URL, got {base_url}"

        def create_hook(self, data: HookCreate) -> Hook:
            created_hooks.append(data)
            return Hook(**data.model_dump())

    def fake_probe(url: str, timeout: float = 0.15) -> bool:
        return url.rstrip("/") == LEGACY_DAEMON_URL.rstrip("/")

    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", fake_probe)
    monkeypatch.setattr(cli_main, "DaemonClient", FakeClient)

    res = cli_runner.invoke(
        cli_main.app,
        [
            "hook",
            "register",
            "--daemon-url",
            "http://127.0.0.1:9999",
            "--name",
            "custom-hook",
            "--topic",
            "telegram:message",
            "--exec",
            "echo 1",
        ],
    )
    assert res.exit_code == 0, res.output
    assert created_hooks and created_hooks[0].name == "custom-hook"


def test_upgrade_scenario_pb_database_url_takes_precedence(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reset_settings: None,
) -> None:
    """PB_DATABASE_URL must override legacy auto-connect, hitting local SQLite."""
    import pboy.cli.daemon_client as dc_mod
    from pboy.cli.daemon_client import LEGACY_DAEMON_URL

    db_path = tmp_path / "explicit.db"
    monkeypatch.setenv("PB_DATABASE_URL", str(db_path))
    cfg = Settings(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "data",
        secret_key="db-url-precedence",
        database_url=str(db_path),
    )
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    def fake_probe(url: str, timeout: float = 0.15) -> bool:
        return url.rstrip("/") == LEGACY_DAEMON_URL.rstrip("/")

    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", fake_probe)

    res = cli_runner.invoke(
        cli_main.app,
        [
            "hook",
            "register",
            "--name",
            "db-url-hook",
            "--topic",
            "telegram:message",
            "--exec",
            "echo 1",
        ],
    )
    assert res.exit_code == 0, res.output
    # Should NOT connect to daemon
    assert "Notice:" not in res.output
    # Should NOT warn about split-brain (PB_DATABASE_URL is intentional)
    assert "Warning: no daemon at" not in res.output

