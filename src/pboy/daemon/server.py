"""uvicorn entry helpers for the Pageboy daemon."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from loguru import logger

from pboy.core.config import get_settings
from pboy.daemon.app import create_app
from pboy.daemon.guard import is_loopback_bind


def assert_loopback_bind(host: str) -> None:
    """Refuse to listen on anything but loopback (local-only contract).

    Raises:
        ValueError: when ``host`` is a wildcard or LAN address.
    """
    if is_loopback_bind(host):
        return
    raise ValueError(
        f"Pageboy is a local-only app and cannot bind to {host!r}. Use --host 127.0.0.1 (default)."
    )


def _public_daemon_url(host: str, port: int) -> str:
    """URL injected into harnesses (always loopback; Host header must stay local)."""
    h = host.strip()
    if ":" in h and not h.startswith("["):
        h = f"[{h}]"
    return f"http://{h}:{port}"


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
    try:
        assert_loopback_bind(actual_host)
    except ValueError as exc:
        logger.error("{}", exc)
        raise SystemExit(1) from exc
    from pboy.channels.env_contract import warn_removed_channel_env_vars

    warn_removed_channel_env_vars()
    public_url = _public_daemon_url(actual_host, actual_port)
    ui_url = f"{public_url}/ui"
    app = create_app(db_path=db, enable_ui=ui, public_url=public_url)
    logger.info(
        "Starting pboy daemon on {}",
        public_url,
    )
    if ui:
        logger.info("UI URL: {}", ui_url)
    uvicorn.run(app, host=actual_host, port=actual_port, log_level=settings.log_level.lower())
