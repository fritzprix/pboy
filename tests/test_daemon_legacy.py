"""Tests for legacy daemon detection and safe stopping (issue #74 upgrade path)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pboy.cli.main import app
from pboy.daemon.legacy import (
    LegacyDaemonInfo,
    probe_legacy_ports,
    stop_legacy_daemon,
    verify_pboy_process,
)


@pytest.fixture
def mock_legacy_daemon():
    """Mock a legacy daemon running on port 8080."""
    info = LegacyDaemonInfo(url="http://127.0.0.1:8080", pid=12345, version="0.4.0")

    def fake_httpx_get(url: str, *args, **kwargs):
        if "8080" in url and "/healthz" in url:

            class FakeResponse:
                status_code = 200

                def json(self):
                    return {"service": "pboy-daemon", "version": "0.4.0"}

            return FakeResponse()
        raise Exception("Connection refused")

    return info, fake_httpx_get


def test_probe_legacy_ports_finds_legacy_daemon(monkeypatch: pytest.MonkeyPatch):
    """probe_legacy_ports detects a daemon on old default ports."""

    def fake_httpx_get(url: str, *args, **kwargs):
        if "8080" in url and "/healthz" in url:

            class FakeResponse:
                status_code = 200

                def json(self):
                    return {"service": "pboy-daemon", "version": "0.4.0"}

            return FakeResponse()
        raise Exception("Connection refused")

    import httpx

    monkeypatch.setattr(httpx, "get", fake_httpx_get)

    info = probe_legacy_ports()
    assert info is not None
    assert info.url == "http://127.0.0.1:8080"
    assert info.version == "0.4.0"


def test_probe_legacy_ports_returns_none_when_no_daemon(monkeypatch: pytest.MonkeyPatch):
    """probe_legacy_ports returns None when no legacy daemon found."""

    def fake_httpx_get(url: str, *args, **kwargs):
        raise Exception("Connection refused")

    import httpx

    monkeypatch.setattr(httpx, "get", fake_httpx_get)

    info = probe_legacy_ports()
    assert info is None


def test_verify_pboy_process_with_valid_process(monkeypatch: pytest.MonkeyPatch):
    """verify_pboy_process returns True for a valid pboy process."""

    class FakeProcess:
        def __init__(self, pid: int):
            pass

        def cmdline(self):
            return ["python3", "-m", "pboy", "daemon"]

    try:
        import psutil

        monkeypatch.setattr(psutil, "Process", FakeProcess)
        assert verify_pboy_process(12345) is True
    except ImportError:
        pytest.skip("psutil not available")


def test_verify_pboy_process_with_invalid_process(monkeypatch: pytest.MonkeyPatch):
    """verify_pboy_process returns False for a non-pboy process."""

    class FakeProcess:
        def __init__(self, pid: int):
            pass

        def cmdline(self):
            return ["nginx", "-g", "daemon off;"]

    try:
        import psutil

        monkeypatch.setattr(psutil, "Process", FakeProcess)
        assert verify_pboy_process(12345) is False
    except ImportError:
        pytest.skip("psutil not available")


def test_stop_legacy_daemon_refuses_without_pid():
    """stop_legacy_daemon refuses to stop when PID not found."""
    info = LegacyDaemonInfo(url="http://127.0.0.1:8080", pid=None, version="0.4.0")

    with pytest.raises(RuntimeError, match="PID not found"):
        stop_legacy_daemon(info)


def test_stop_legacy_daemon_refuses_unverified_process(monkeypatch: pytest.MonkeyPatch):
    """stop_legacy_daemon refuses to stop if process verification fails."""
    info = LegacyDaemonInfo(url="http://127.0.0.1:8080", pid=12345, version="0.4.0")

    monkeypatch.setattr(
        "pboy.daemon.legacy.verify_pboy_process", lambda pid: False
    )

    with pytest.raises(RuntimeError, match="failed verification"):
        stop_legacy_daemon(info)


def test_resolve_daemon_url_detects_legacy_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """resolve_daemon_url raises SystemExit when legacy daemon detected (non-interactive)."""
    import pboy.core.config as config_mod
    from pboy.cli.daemon_client import resolve_daemon_url

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.delenv("PB_DATABASE_URL", raising=False)
    monkeypatch.setenv("PB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "_settings", None)

    # Mock non-TTY
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.delenv("PB_REPLACE_LEGACY_DAEMON", raising=False)

    def fake_probe():
        return LegacyDaemonInfo(url="http://127.0.0.1:8080", pid=12345, version="0.4.0")

    monkeypatch.setattr("pboy.daemon.legacy.probe_legacy_ports", fake_probe)

    with pytest.raises(SystemExit) as exc_info:
        resolve_daemon_url()

    assert exc_info.value.code == 1


def test_resolve_daemon_url_with_replace_legacy_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """resolve_daemon_url stops legacy daemon when PB_REPLACE_LEGACY_DAEMON=1."""
    import pboy.core.config as config_mod
    from pboy.cli.daemon_client import resolve_daemon_url

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.delenv("PB_DATABASE_URL", raising=False)
    monkeypatch.setenv("PB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PB_REPLACE_LEGACY_DAEMON", "1")
    monkeypatch.setattr(config_mod, "_settings", None)

    stopped = {"called": False}

    def fake_probe():
        return LegacyDaemonInfo(url="http://127.0.0.1:8080", pid=12345, version="0.4.0")

    def fake_stop(info, *, force=False, timeout=15.0):
        stopped["called"] = True
        return True

    monkeypatch.setattr("pboy.daemon.legacy.probe_legacy_ports", fake_probe)
    monkeypatch.setattr("pboy.daemon.legacy.stop_legacy_daemon", fake_stop)

    result = resolve_daemon_url()
    assert result is None
    assert stopped["called"]


def test_daemon_start_detects_legacy_conflict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """run_daemon raises SystemExit when legacy daemon detected (non-interactive)."""
    import pboy.core.config as config_mod
    from pboy.daemon import server as server_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("PB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "_settings", None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.delenv("PB_REPLACE_LEGACY_DAEMON", raising=False)

    def fake_probe():
        return LegacyDaemonInfo(url="http://127.0.0.1:8080", pid=12345, version="0.4.0")

    monkeypatch.setattr("pboy.daemon.legacy.probe_legacy_ports", fake_probe)
    monkeypatch.setattr(server_mod.uvicorn, "run", lambda *a, **k: None)

    with pytest.raises(SystemExit) as exc_info:
        server_mod.run_daemon(host="127.0.0.1", port=17680, db=tmp_path / "d.db", ui=False)

    assert exc_info.value.code == 1


def test_daemon_stop_legacy_flag(monkeypatch: pytest.MonkeyPatch):
    """pboy daemon stop --legacy stops a legacy daemon."""
    runner = CliRunner()

    stopped = {"called": False}

    def fake_probe():
        return LegacyDaemonInfo(url="http://127.0.0.1:8080", pid=12345, version="0.4.0")

    def fake_stop(info, *, force=False, timeout=10.0):
        stopped["called"] = True
        return True

    monkeypatch.setattr("pboy.daemon.legacy.probe_legacy_ports", fake_probe)
    monkeypatch.setattr("pboy.daemon.legacy.stop_legacy_daemon", fake_stop)

    result = runner.invoke(app, ["daemon", "stop", "--legacy"])
    assert result.exit_code == 0
    assert stopped["called"]
    assert "stopped successfully" in result.output.lower()


def test_daemon_stop_legacy_not_found(monkeypatch: pytest.MonkeyPatch):
    """pboy daemon stop --legacy exits cleanly when no legacy daemon found."""
    runner = CliRunner()

    monkeypatch.setattr("pboy.daemon.legacy.probe_legacy_ports", lambda: None)

    result = runner.invoke(app, ["daemon", "stop", "--legacy"])
    assert result.exit_code == 0
    assert "No legacy daemon found" in result.output


def test_daemon_stop_legacy_force(monkeypatch: pytest.MonkeyPatch):
    """pboy daemon stop --legacy --force uses force stop."""
    runner = CliRunner()

    force_used = {"value": False}

    def fake_probe():
        return LegacyDaemonInfo(url="http://127.0.0.1:8080", pid=12345, version="0.4.0")

    def fake_stop(info, *, force=False, timeout=10.0):
        force_used["value"] = force
        return True

    monkeypatch.setattr("pboy.daemon.legacy.probe_legacy_ports", fake_probe)
    monkeypatch.setattr("pboy.daemon.legacy.stop_legacy_daemon", fake_stop)

    result = runner.invoke(app, ["daemon", "stop", "--legacy", "--force"])
    assert result.exit_code == 0
    assert force_used["value"] is True
