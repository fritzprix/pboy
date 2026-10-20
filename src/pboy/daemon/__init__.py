"""Local daemon HTTP server for Pageboy."""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

__all__ = ["create_app"]

_SUBMODULES = frozenset({"app", "ensure", "guard", "schemas", "server"})


def __dir__() -> list[str]:
    return sorted({*globals().keys(), *__all__, *_SUBMODULES})


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from pboy.daemon.app import create_app

        return create_app
    full = f"{__name__}.{name}"
    # Only resolve real package submodules (keeps hasattr from surprising imports).
    if name not in _SUBMODULES or importlib.util.find_spec(full) is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as exc:
        # Missing submodule itself → AttributeError; missing *dependency* → re-raise.
        if exc.name == full:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
        raise
