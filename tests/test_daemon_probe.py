"""Tests for the neutral daemon health probe."""

from __future__ import annotations

import pytest

from pboy.daemon.probe import probe_daemon_alive


def test_probe_daemon_alive_false_when_unreachable() -> None:
    assert probe_daemon_alive("http://127.0.0.1:1", timeout=0.05) is False


def test_probe_daemon_alive_true_for_pboy_healthz(monkeypatch: pytest.MonkeyPatch) -> None:
    import pboy.daemon.probe as probe_mod

    class _Res:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"service": "pboy-daemon"}

    monkeypatch.setattr(probe_mod.httpx, "get", lambda *_a, **_k: _Res())
    assert probe_daemon_alive("http://127.0.0.1:17680") is True
