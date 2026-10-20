"""Daemon package import-surface tests."""

from __future__ import annotations

import sys


def test_daemon_package_does_not_eagerly_import_create_app() -> None:
    # Only drop the package shell + app; do not wipe sibling submodules
    # (e.g. ensure) or other tests' module-level imports / monkeypatches break.
    sys.modules.pop("pboy.daemon.app", None)
    sys.modules.pop("pboy.daemon", None)

    import pboy.daemon as daemon_pkg

    assert "pboy.daemon.app" not in sys.modules
    create_app = daemon_pkg.create_app
    assert callable(create_app)
    assert "pboy.daemon.app" in sys.modules
