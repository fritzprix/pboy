"""Tests for the neutral daemon health probe."""

from __future__ import annotations

import pytest

from pboy.core.probe import probe_daemon_alive


def test_probe_daemon_alive_false_when_unreachable() -> None:
    assert probe_daemon_alive("http://127.0.0.1:1", timeout=0.05) is False


def test_probe_daemon_alive_true_for_pboy_healthz(monkeypatch: pytest.MonkeyPatch) -> None:
    import pboy.core.probe as probe_mod

    class _Res:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"service": "pboy-daemon"}

    monkeypatch.setattr(probe_mod.httpx, "get", lambda *_a, **_k: _Res())
    assert probe_daemon_alive("http://127.0.0.1:17680") is True


def test_probe_daemon_alive_false_for_wrong_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return False when /healthz returns 200 but service name is wrong."""
    import pboy.core.probe as probe_mod

    class _Res:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"service": "nginx"}

    monkeypatch.setattr(probe_mod.httpx, "get", lambda *_a, **_k: _Res())
    assert probe_daemon_alive("http://127.0.0.1:8080") is False


def test_probe_daemon_alive_false_for_missing_service_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return False when /healthz returns 200 but service field is missing."""
    import pboy.core.probe as probe_mod

    class _Res:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"status": "ok"}

    monkeypatch.setattr(probe_mod.httpx, "get", lambda *_a, **_k: _Res())
    assert probe_daemon_alive("http://127.0.0.1:8080") is False


def test_probe_daemon_alive_false_for_non_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return False when /healthz returns 200 but response is not JSON."""
    import pboy.core.probe as probe_mod

    class _Res:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            raise ValueError("Not JSON")

    monkeypatch.setattr(probe_mod.httpx, "get", lambda *_a, **_k: _Res())
    assert probe_daemon_alive("http://127.0.0.1:8080") is False


def test_probe_daemon_alive_false_for_non_200_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return False when /healthz returns non-200 status."""
    import pboy.core.probe as probe_mod

    class _Res:
        status_code = 404

        @staticmethod
        def json() -> dict[str, str]:
            return {"service": "pboy-daemon"}

    monkeypatch.setattr(probe_mod.httpx, "get", lambda *_a, **_k: _Res())
    assert probe_daemon_alive("http://127.0.0.1:8080") is False


def test_probe_daemon_alive_false_for_500_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return False when /healthz returns 500 error."""
    import pboy.core.probe as probe_mod

    class _Res:
        status_code = 500

        @staticmethod
        def json() -> dict[str, str]:
            return {"error": "Internal Server Error"}

    monkeypatch.setattr(probe_mod.httpx, "get", lambda *_a, **_k: _Res())
    assert probe_daemon_alive("http://127.0.0.1:8080") is False
