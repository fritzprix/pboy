"""Legacy daemon detection (pre-0.5.0 upgrade path).

This module detects old daemons (0.4.x) without runtime files to prevent
split-brain writes and double channel polling during upgrades.

Marked for removal in a future release (e.g. 0.6.0 or 1.0.0).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class LegacyDaemonInfo:
    """Information about a detected legacy daemon."""

    url: str
    version: str | None


def probe_legacy_ports(*, timeout: float = 0.3) -> LegacyDaemonInfo | None:
    """Check old default ports (8080, 17680) for a legacy pboy daemon.

    Returns LegacyDaemonInfo if found, None otherwise.
    """
    legacy_ports = [8080, 17680]
    for port in legacy_ports:
        url = f"http://127.0.0.1:{port}"
        try:
            res = httpx.get(f"{url}/healthz", timeout=timeout)
            if res.status_code == 200:
                body = res.json()
                if body.get("service") == "pboy-daemon":
                    version = body.get("version")
                    return LegacyDaemonInfo(url=url, version=version)
        except Exception:
            continue
    return None


def get_stop_instructions() -> str:
    """Return platform-specific instructions for manually stopping a legacy daemon."""
    if sys.platform == "win32":
        return (
            "To stop it manually:\n"
            "1. Find the process: netstat -ano | findstr :<port>\n"
            "2. Verify it's pboy: tasklist /FI \"PID eq <pid>\"\n"
            "3. Stop it: taskkill /PID <pid>\n"
            "   (or Ctrl+C if running in foreground, or stop the service if using systemd/etc.)"
        )
    else:
        return (
            "To stop it manually:\n"
            "1. Find the process: lsof -nP -iTCP:<port> -sTCP:LISTEN\n"
            "2. Verify it's pboy (check COMMAND column)\n"
            "3. Stop it: kill <pid>\n"
            "   (or Ctrl+C if running in foreground, or systemctl stop if using systemd)"
        )


def raise_legacy_daemon_error(info: LegacyDaemonInfo) -> None:
    """Raise SystemExit with a clear error message about the legacy daemon.

    Never returns; always exits non-zero.
    """
    version_str = f" (v{info.version})" if info.version else ""

    msg = (
        f"\nError: A legacy Pageboy daemon{version_str} is running at {info.url}.\n"
        f"Stop it, then rerun this command.\n\n"
        f"{get_stop_instructions()}"
    )

    print(msg, file=sys.stderr)
    raise SystemExit(1)
