"""uvicorn entry helpers for the Pageboy daemon."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from loguru import logger

from pboy.core.config import get_settings
from pboy.daemon.app import create_app


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

    if ui:
        logger.warning("Embedded UI is not implemented yet; starting API-only daemon.")

    app = create_app(db_path=db)
    logger.info("Starting pboy daemon on http://{}:{}", actual_host, actual_port)
    uvicorn.run(app, host=actual_host, port=actual_port, log_level=settings.log_level.lower())
