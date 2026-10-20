"""Tests for singleton daemon ensure (spawn once, never auto-kill)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import uvicorn
from typer.testing import CliRunner

import pboy.core.config as config_mod
from pboy.cli.daemon_client import DEFAULT_DAEMON_URL
from pboy.cli.main import app
from pboy.core.config import DEFAULT_PORT
from pboy.daemon.app import create_app
from pboy.daemon.ensure import (
    EnsureDaemonError,
    EnsureDaemonResult,
    daemon_base_url,
    ensure_daemon,
    ensure_daemon_url,
    parse_daemon_url,
    probe_daemon,
)


@pytest.fixture
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setattr(config_mod, "_settings", None)
    yield
    monkeypatch.setattr(config_mod, "_settings", None)


@pytest.fixture
def live_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_settings: None
) -> Generator[str, None, None]:
    monkeypatch.setenv("PB_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("PB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config_mod, "_settings", None)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    application = create_app(db_path=tmp_path / "ensure.db", secret_key="ensure-key")
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


def test_parse_daemon_url() -> None:
    assert parse_daemon_url("http://127.0.0.1:9000") == ("127.0.0.1", 9000)
    assert parse_daemon_url("http://127.0.0.1:9000/") == ("127.0.0.1", 9000)
    assert parse_daemon_url("127.0.0.1:9000") == ("127.0.0.1", 9000)


def test_ensure_returns_existing_without_spawn(
    live_daemon: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, port = parse_daemon_url(live_daemon)
    spawn = MagicMock()
    monkeypatch.setattr("pboy.daemon.ensure._spawn_daemon", spawn)
    result = ensure_daemon(host=host, port=port, wait_seconds=2.0)
    assert result == EnsureDaemonResult(url=live_daemon, started=False)
    spawn.assert_not_called()


def test_ensure_spawns_when_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_settings: None
) -> None:
    monkeypatch.setenv("PB_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("PB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config_mod, "_settings", None)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    url = daemon_base_url("127.0.0.1", port)
    assert not probe_daemon(url)

    # Simulate a daemon appearing after spawn (without a real child process).
    calls: dict[str, Any] = {"n": 0}

    def fake_spawn(**_kwargs: object) -> None:
        calls["n"] += 1

        def _start() -> None:
            application = create_app(
                db_path=tmp_path / "spawned.db", secret_key="spawn-key"
            )
            config = uvicorn.Config(
                application, host="127.0.0.1", port=port, log_level="error"
            )
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            # Keep a reference so GC does not stop the server mid-test.
            calls["server"] = server

        _start()

    monkeypatch.setattr("pboy.daemon.ensure._spawn_daemon", fake_spawn)
    result = ensure_daemon(host="127.0.0.1", port=port, wait_seconds=10.0)
    assert result.started is True
    assert result.url == url
    assert calls["n"] == 1
    assert probe_daemon(url)

    # Second ensure must not spawn again.
    result2 = ensure_daemon(host="127.0.0.1", port=port, wait_seconds=2.0)
    assert result2.started is False
    assert calls["n"] == 1

    server = calls["server"]
    server.should_exit = True


def test_ensure_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_settings: None
) -> None:
    monkeypatch.setenv("PB_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("PB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config_mod, "_settings", None)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    monkeypatch.setattr("pboy.daemon.ensure._spawn_daemon", lambda **_k: None)
    with pytest.raises(EnsureDaemonError, match="Timed out"):
        ensure_daemon(host="127.0.0.1", port=port, wait_seconds=0.4, poll_interval=0.05)


def test_ui_exits_when_daemon_already_running(
    live_daemon: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, port = parse_daemon_url(live_daemon)
    run = MagicMock()
    monkeypatch.setattr("pboy.daemon.server.run_daemon", run)
    runner = CliRunner()
    res = runner.invoke(app, ["ui", "--host", host, "--port", str(port)])
    assert res.exit_code == 0
    assert "already running" in res.output
    assert f"{live_daemon}/ui" in res.output
    run.assert_not_called()


def test_mcp_ensures_then_runs_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    ensured = EnsureDaemonResult(url="http://127.0.0.1:18080", started=True)
    monkeypatch.setattr(
        "pboy.daemon.ensure.ensure_daemon_url",
        lambda *a, **k: ensured,
    )
    ran: list[str | None] = []

    def fake_run(*, daemon_url: str | None = None) -> None:
        ran.append(daemon_url)

    monkeypatch.setattr("pboy.mcp.server.run_mcp_stdio", fake_run)
    runner = CliRunner()
    res = runner.invoke(app, ["mcp"])
    assert res.exit_code == 0
    assert ran == ["http://127.0.0.1:18080"]
    assert "Started Pageboy daemon" in (res.stderr or res.output)


def test_ensure_daemon_url_reuses_legacy_8080_instead_of_spawn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import pboy.cli.daemon_client as dc_mod
    from pboy.cli.daemon_client import LEGACY_DAEMON_URL, resolve_daemon_url

    if DEFAULT_PORT == 8080:
        pytest.skip("legacy attach only applies when default port is not 8080")

    monkeypatch.setattr(dc_mod, "_legacy_daemon_hint_emitted", False)
    spawn = MagicMock()
    monkeypatch.setattr("pboy.daemon.ensure._spawn_daemon", spawn)

    def fake_probe(url: str, *, timeout: float = 0.3) -> bool:
        return url.rstrip("/") == LEGACY_DAEMON_URL.rstrip("/")

    monkeypatch.setattr("pboy.daemon.ensure.probe_daemon", fake_probe)
    monkeypatch.setattr(dc_mod, "_probe_daemon_alive", fake_probe)
    result = ensure_daemon_url(None)
    assert result == EnsureDaemonResult(url=LEGACY_DAEMON_URL, started=False)
    spawn.assert_not_called()
    err = capsys.readouterr().err
    assert "Reusing legacy" in err
    assert LEGACY_DAEMON_URL in err
    assert DEFAULT_DAEMON_URL in err
    assert "Hint:" not in err

    # Subsequent resolve must not print the migration Hint again.
    assert resolve_daemon_url() is None
    assert "Hint:" not in capsys.readouterr().err
