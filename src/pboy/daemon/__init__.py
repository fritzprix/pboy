"""Local daemon HTTP server for Pageboy."""

from __future__ import annotations

import importlib
from typing import Any

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from pboy.daemon.app import create_app

        return create_app
    # Allow ``getattr(pboy.daemon, "ensure")`` / monkeypatch paths to resolve
    # submodules (PEP 562 would otherwise raise before importlib loads them).
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
