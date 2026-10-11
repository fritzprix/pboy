"""Simple filter expression parsing and evaluation."""

from __future__ import annotations

import re
from typing import Any

# Simple jq-like equality filters: .data.priority == "High"
_FILTER_RE = re.compile(r'^\.?([\w.]+)\s*(==|!=)\s*["\']?([^"\']+)["\']?$')


def is_valid_filter_expr(filter_expr: str) -> bool:
    """Return True if filter_expr uses the supported equality syntax."""
    return bool(_FILTER_RE.match(filter_expr.strip()))


def evaluate_simple_filter(filter_expr: str, event_dict: dict[str, Any]) -> bool:
    """Evaluate simple jq-like expression e.g. .data.priority == "High".

    Unsupported syntax fails closed (returns False) so typos never match all events.
    """
    expr = filter_expr.strip()
    match = _FILTER_RE.match(expr)
    if not match:
        return False

    path_str, op, expected = match.groups()
    parts = path_str.split(".")
    curr: Any = event_dict
    for part in parts:
        if isinstance(curr, dict) and part in curr:
            curr = curr[part]
        else:
            curr = None
            break

    curr_str = str(curr) if curr is not None else ""
    if op == "==":
        return curr_str == expected
    if op == "!=":
        return curr_str != expected
    return False
