"""Tests for legacy daemon detection (issue #74 upgrade path)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pboy.daemon.legacy import LegacyDaemonInfo, get_stop_instructions, probe_legacy_ports


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


def test_probe_legacy_ports_ignores_non_pboy_service(monkeypatch: pytest.MonkeyPatch):
    """probe_legacy_ports ignores services that aren't pboy-daemon."""

    def fake_httpx_get(url: str, *args, **kwargs):
        if "8080" in url and "/healthz" in url:

            class FakeResponse:
                status_code = 200

                def json(self):
                    return {"service": "other-service", "version": "1.0"}

            return FakeResponse()
        raise Exception("Connection refused")

    import httpx

    monkeypatch.setattr(httpx, "get", fake_httpx_get)

    info = probe_legacy_ports()
    assert info is None


def test_get_stop_instructions_windows(monkeypatch: pytest.MonkeyPatch):
    """get_stop_instructions returns Windows-specific guidance."""
    monkeypatch.setattr(sys, "platform", "win32")
    instructions = get_stop_instructions()
    assert "netstat -ano" in instructions
    assert "tasklist" in instructions
    assert "taskkill" in instructions


def test_get_stop_instructions_unix(monkeypatch: pytest.MonkeyPatch):
    """get_stop_instructions returns Unix-specific guidance."""
    monkeypatch.setattr(sys, "platform", "linux")
    instructions = get_stop_instructions()
    assert "lsof" in instructions
    assert "kill" in instructions
    assert "taskkill" not in instructions


def test_resolve_daemon_url_detects_legacy_and_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """resolve_daemon_url raises SystemExit when legacy daemon detected."""
    import pboy.core.config as config_mod
    from pboy.cli.daemon_client import resolve_daemon_url

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.delenv("PB_DATABASE_URL", raising=False)
    monkeypatch.setenv("PB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "_settings", None)

    def fake_probe():
        return LegacyDaemonInfo(url="http://127.0.0.1:8080", version="0.4.0")

    monkeypatch.setattr("pboy.daemon.legacy.probe_legacy_ports", fake_probe)

    with pytest.raises(SystemExit) as exc_info:
        resolve_daemon_url()

    assert exc_info.value.code == 1


def test_ensure_daemon_detects_legacy_and_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """ensure_daemon_url raises SystemExit when legacy daemon detected."""
    import pboy.core.config as config_mod
    from pboy.daemon.ensure import ensure_daemon_url

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("PB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "_settings", None)

    def fake_probe():
        return LegacyDaemonInfo(url="http://127.0.0.1:8080", version="0.4.0")

    monkeypatch.setattr("pboy.daemon.legacy.probe_legacy_ports", fake_probe)

    with pytest.raises(SystemExit) as exc_info:
        ensure_daemon_url()

    assert exc_info.value.code == 1


def test_daemon_start_detects_legacy_and_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
):
    """run_daemon raises SystemExit when legacy daemon detected."""
    import pboy.core.config as config_mod
    from pboy.daemon import server as server_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("PB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "_settings", None)

    def fake_probe():
        return LegacyDaemonInfo(url="http://127.0.0.1:8080", version="0.4.0")

    monkeypatch.setattr("pboy.daemon.legacy.probe_legacy_ports", fake_probe)
    monkeypatch.setattr(server_mod.uvicorn, "run", lambda *a, **k: None)

    with pytest.raises(SystemExit) as exc_info:
        server_mod.run_daemon(host="127.0.0.1", port=17680, db=tmp_path / "d.db", ui=False)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "legacy pageboy daemon" in captured.err.lower()
    assert "http://127.0.0.1:8080" in captured.err
