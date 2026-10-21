"""Ensure a single local Pageboy daemon is running (spawn once, never auto-kill).

``pboy ui`` and ``pboy mcp`` share this: probe ``/healthz``, and if nothing is
listening, start ``python -m pboy daemon --ui`` in the background. The child is
detached; callers do not stop it on exit. A file lock serializes spawn races so
two clients do not start two daemons.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from pboy.cli.daemon_client import DEFAULT_DAEMON_URL
from pboy.core.config import DEFAULT_HOST, DEFAULT_PORT, get_settings
from pboy.core.probe import probe_daemon_alive
from pboy.daemon.runtime import DaemonRuntimeFile
from pboy.daemon.server import _public_daemon_url, assert_loopback_bind


@dataclass(frozen=True)
class EnsureDaemonResult:
    """Outcome of :func:`ensure_daemon`."""

    url: str
    started: bool
    """True when this call spawned a new daemon process."""


class EnsureDaemonError(RuntimeError):
    """Daemon could not be reached or started."""


def probe_daemon(url: str, *, timeout: float = 0.3) -> bool:
    """Return True if a Pageboy daemon answers ``/healthz`` at ``url``."""
    return probe_daemon_alive(url, timeout=timeout)


def daemon_base_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    """Loopback HTTP base URL for the given bind."""
    return _public_daemon_url(host, port)


def parse_daemon_url(url: str) -> tuple[str, int]:
    """Extract host and port from an HTTP daemon URL."""
    cleaned = url.strip().rstrip("/")
    parsed = urlparse(cleaned if "://" in cleaned else f"http://{cleaned}")
    host = parsed.hostname or DEFAULT_HOST
    port = parsed.port or DEFAULT_PORT
    return host, port


def ensure_daemon(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    db: str | Path | None = None,
    ui: bool = True,
    wait_seconds: float = 30.0,
    poll_interval: float = 0.1,
) -> EnsureDaemonResult:
    """Return a live daemon URL, spawning one in the background if needed.

    Does not stop the daemon when the caller exits. If another process wins the
    spawn race, this call waits until that daemon becomes healthy.
    """
    assert_loopback_bind(host)
    url = daemon_base_url(host, port)
    if probe_daemon(url):
        return EnsureDaemonResult(url=url, started=False)

    settings = get_settings()
    lock_path = settings.config_dir / f"daemon-{port}.lock"
    settings.ensure_dirs()

    with _SpawnLock(lock_path):
        if probe_daemon(url):
            return EnsureDaemonResult(url=url, started=False)

        log_path = settings.data_dir / "daemon.log"
        _spawn_daemon(host=host, port=port, db=db, ui=ui, log_path=log_path)
        if not _wait_healthy(url, wait_seconds=wait_seconds, poll_interval=poll_interval):
            hint = f" See {log_path}" if log_path.is_file() else ""
            raise EnsureDaemonError(
                f"Timed out waiting for Pageboy daemon at {url}/healthz.{hint}"
            )
        return EnsureDaemonResult(url=url, started=True)


def ensure_daemon_url(
    daemon_url: str | None = None,
    *,
    db: str | Path | None = None,
    ui: bool = True,
    wait_seconds: float = 30.0,
) -> EnsureDaemonResult:
    """Ensure daemon at ``daemon_url`` or discover via runtime file."""
    if daemon_url and daemon_url.strip():
        host, port = parse_daemon_url(daemon_url)
    else:
        settings = get_settings()
        runtime = DaemonRuntimeFile(settings.data_dir)
        info = runtime.read_if_alive()

        if info:
            try:
                import httpx
                res = httpx.get(f"{info.base_url}/healthz", timeout=0.5)
                if res.status_code == 200:
                    body = res.json()
                    if body.get("service") == "pboy-daemon":
                        token = body.get("instance_token", "")
                        if token == info.instance_token:
                            return EnsureDaemonResult(url=info.base_url, started=False)
            except Exception:
                pass

        # No valid runtime file daemon → check for legacy daemon (upgrade path)
        from pboy.daemon.legacy import handle_legacy_daemon_conflict, probe_legacy_ports

        legacy_info = probe_legacy_ports()
        if legacy_info:
            handle_legacy_daemon_conflict(legacy_info, context="ensure")

        host, port = DEFAULT_HOST, DEFAULT_PORT
        host = settings.host or host
        port = settings.port if settings.port else port

    return ensure_daemon(
        host=host, port=port, db=db, ui=ui, wait_seconds=wait_seconds
    )


def _spawn_daemon(
    *,
    host: str,
    port: int,
    db: str | Path | None,
    ui: bool,
    log_path: Path,
) -> None:
    cmd: list[str] = [
        sys.executable,
        "-m",
        "pboy",
        "daemon",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if ui:
        cmd.append("--ui")
    if db is not None:
        cmd.extend(["--db", str(db)])

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 — kept open for child
    try:
        kwargs: dict[str, Any] = {
            "args": cmd,
            "stdin": subprocess.DEVNULL,
            "stdout": log_fh,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if sys.platform == "win32":
            # Detach from the calling console / MCP host job.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True

        logger.info("Spawning Pageboy daemon: {}", " ".join(cmd))
        subprocess.Popen(**kwargs)
    finally:
        log_fh.close()


def _wait_healthy(
    url: str, *, wait_seconds: float, poll_interval: float
) -> bool:
    deadline = time.monotonic() + wait_seconds
    # First checks can be slow while uvicorn imports; use a slightly longer probe.
    while time.monotonic() < deadline:
        if probe_daemon(url, timeout=0.5):
            return True
        time.sleep(poll_interval)
    return False


class _SpawnLock:
    """Exclusive file lock so only one client spawns the daemon."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: Any = None

    def __enter__(self) -> _SpawnLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a+", encoding="utf-8")
        self._lock()
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            self._unlock()
        finally:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def _lock(self) -> None:
        assert self._fh is not None
        if sys.platform == "win32":
            import msvcrt

            self._fh.seek(0)
            if self._fh.read(1) == "":
                self._fh.write("0")
                self._fh.flush()
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)

    def _unlock(self) -> None:
        if self._fh is None:
            return
        if sys.platform == "win32":
            import msvcrt

            self._fh.seek(0)
            with contextlib.suppress(OSError):
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)


def default_daemon_url() -> str:
    """Default probe URL (exported for tests/docs)."""
    return DEFAULT_DAEMON_URL
