"""Daemon package import-surface tests."""

from __future__ import annotations

import sys


def test_daemon_package_does_not_eagerly_import_create_app() -> None:
    for name in list(sys.modules):
        if name == "pboy.daemon" or name.startswith("pboy.daemon."):
            del sys.modules[name]

    import pboy.daemon as daemon_pkg

    assert "pboy.daemon.app" not in sys.modules
    create_app = daemon_pkg.create_app
    assert callable(create_app)
    assert "pboy.daemon.app" in sys.modules
