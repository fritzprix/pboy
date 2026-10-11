"""Core engine components for Pageboy.

Heavy imports are lazy to avoid circular imports with ``pboy.models.hook``
(which validates filters via ``pboy.core.filters``).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Engine",
    "EventNormalizer",
    "HookRouter",
    "LeaseEngine",
    "Settings",
    "evaluate_simple_filter",
    "get_settings",
    "is_valid_filter_expr",
]


def __getattr__(name: str) -> Any:
    if name in ("Settings", "get_settings"):
        from pboy.core.config import Settings, get_settings

        return {"Settings": Settings, "get_settings": get_settings}[name]
    if name in ("evaluate_simple_filter", "is_valid_filter_expr"):
        from pboy.core.filters import evaluate_simple_filter, is_valid_filter_expr

        return {
            "evaluate_simple_filter": evaluate_simple_filter,
            "is_valid_filter_expr": is_valid_filter_expr,
        }[name]
    if name == "Engine":
        from pboy.core.engine import Engine

        return Engine
    if name == "EventNormalizer":
        from pboy.core.normalizer import EventNormalizer

        return EventNormalizer
    if name == "HookRouter":
        from pboy.core.router import HookRouter

        return HookRouter
    if name == "LeaseEngine":
        from pboy.core.lease import LeaseEngine

        return LeaseEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
