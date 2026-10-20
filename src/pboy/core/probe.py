"""Neutral HTTP health probe for a local Pageboy daemon.

Shared by CLI auto-connect and daemon ensure/server so neither side needs a
private import from the other package.
"""

from __future__ import annotations

import httpx


def probe_daemon_alive(url: str, timeout: float = 0.15) -> bool:
    """Return True if a Pageboy daemon is responding at ``url/healthz``."""
    try:
        res = httpx.get(f"{url.rstrip('/')}/healthz", timeout=timeout)
        return res.status_code == 200 and res.json().get("service") == "pboy-daemon"
    except Exception:
        return False
