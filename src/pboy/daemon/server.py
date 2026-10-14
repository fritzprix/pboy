"""uvicorn entry helpers for the Pageboy daemon."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from loguru import logger

from pboy.core.config import get_settings
from pboy.daemon.app import create_app

_BIND_LOOPBACK = frozenset({"127.0.0.1", "::1", "[::1]", "localhost"})
_BIND_WILDCARDS = frozenset({"0.0.0.0", "::", "[::]"})


def is_loopback_bind(host: str) -> bool:
    """True when the listen address is loopback-only (not a wildcard / LAN bind)."""
    h = host.strip().lower()
    if h in _BIND_LOOPBACK:
        return True
    # 127.0.0.0/8 (e.g. Debian/Ubuntu 127.0.1.1)
    return h.startswith("127.")


def assert_bind_auth_policy(host: str, auth_token: str) -> None:
    """Require ``PB_AUTH_TOKEN`` for non-loopback binds (``0.0.0.0``, ``::``, LAN IPs).

    Raises:
        ValueError: when the bind is not loopback and ``auth_token`` is empty.
    """
    if is_loopback_bind(host):
        return
    if auth_token.strip():
        return
    kind = "wildcard" if host.strip() in _BIND_WILDCARDS else "non-loopback"
    raise ValueError(
        f"PB_AUTH_TOKEN is required when binding to {kind} address {host!r}. "
        "Set PB_AUTH_TOKEN or use --host 127.0.0.1 for local-only mode."
    )


def _public_daemon_url(host: str, port: int) -> str:
    """URL injected into harnesses; map wildcard binds to loopback."""
    client_host = "127.0.0.1" if host in _BIND_WILDCARDS else host
    return f"http://{client_host}:{port}"


def run_daemon(
    *,
    host: str | None = None,
    port: int | None = None,
    db: str | Path | None = None,
    ui: bool = False,
) -> None:
    """Start the daemon HTTP server (blocking)."""
    settings = get_settings()
    actual_host = host or settings.host
    actual_port = port if port is not None else settings.port
    settings.daemon_url = _public_daemon_url(actual_host, actual_port)

    try:
        assert_bind_auth_policy(actual_host, settings.auth_token)
    except ValueError as exc:
        logger.error("{}", exc)
        raise SystemExit(1) from exc

    if settings.auth_token.strip():
        logger.info(
            "Daemon auth token configured (X-PB-Token / Bearer required for all clients, "
            "including loopback — safe behind reverse proxies)"
        )
    elif is_loopback_bind(actual_host):
        logger.info(
            "Local mode ({}): no PB_AUTH_TOKEN; loopback + local Host only. "
            "Set PB_AUTH_TOKEN before reverse-proxy or --host 0.0.0.0",
            actual_host,
        )


    app = create_app(db_path=db, enable_ui=ui)
    logger.info(
        "Starting pboy daemon on http://{}:{} (PB_DAEMON_URL={})",
        actual_host,
        actual_port,
        settings.daemon_url,
    )
    if ui:
        if settings.auth_token.strip():
            logger.info("Dashboard: {}/ui (login required — PB_AUTH_TOKEN set)", settings.daemon_url)
        else:
            logger.info(
                "Dashboard: {}/ui (local Host only until PB_AUTH_TOKEN is set)",
                settings.daemon_url,
            )
    uvicorn.run(app, host=actual_host, port=actual_port, log_level=settings.log_level.lower())
