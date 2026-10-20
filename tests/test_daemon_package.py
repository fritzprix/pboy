"""Daemon package import-surface tests."""

from __future__ import annotations

import sys

import pytest


def test_daemon_package_does_not_eagerly_import_create_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Restore via monkeypatch so other tests keep stable module objects.
    monkeypatch.delitem(sys.modules, "pboy.daemon.app", raising=False)
    monkeypatch.delitem(sys.modules, "pboy.daemon", raising=False)

    import pboy.daemon as daemon_pkg

    assert "pboy.daemon.app" not in sys.modules
    create_app = daemon_pkg.create_app
    assert callable(create_app)
    assert "pboy.daemon.app" in sys.modules
    assert "create_app" in dir(daemon_pkg)
    assert "ensure" in dir(daemon_pkg)
