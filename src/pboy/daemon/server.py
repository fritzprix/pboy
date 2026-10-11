"""uvicorn entry helpers for the Pageboy daemon."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from loguru import logger

from pboy.core.config import get_settings
from pboy.daemon.app import create_app


def _public_daemon_url(host: str, port: int) -> str:
    """URL injected into harnesses; map wildcard binds to loopback."""
    client_host = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
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

    if ui:
        logger.warning("Embedded UI is not implemented yet; starting API-only daemon.")

    if not settings.auth_token:
        logger.warning(
            "PB_AUTH_TOKEN unset: mutating APIs accept loopback only. "
            "Set PB_AUTH_TOKEN before exposing the daemon."
        )
    else:
        logger.info("Daemon auth token configured (X-PB-Token / Bearer)")

    app = create_app(db_path=db)
    logger.info("Starting pboy daemon on http://{}:{} (PB_DAEMON_URL={})", actual_host, actual_port, settings.daemon_url)
    uvicorn.run(app, host=actual_host, port=actual_port, log_level=settings.log_level.lower())
