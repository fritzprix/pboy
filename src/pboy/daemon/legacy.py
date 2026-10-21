"""Legacy daemon detection and safe stopping (pre-0.5.0 upgrade path).

This module handles the transition from port-based discovery (0.4.x) to runtime
file discovery (0.5.0+). It detects old daemons without runtime files and helps
users migrate safely without split-brain scenarios.

Marked for removal in a future release (e.g. 0.6.0 or 1.0.0).
"""

from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass

import httpx
from loguru import logger


@dataclass(frozen=True)
class LegacyDaemonInfo:
    """Information about a detected legacy daemon."""

    url: str
    pid: int | None
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
                    pid = _find_pid_for_port(port)
                    version = body.get("version")
                    return LegacyDaemonInfo(url=url, pid=pid, version=version)
        except Exception:
            continue
    return None


def _find_pid_for_port(port: int) -> int | None:
    """Find the PID that owns the listening socket on the given port.

    Returns None if not found or if psutil is not available.
    """
    try:
        import psutil
    except ImportError:
        logger.debug("psutil not available; cannot identify process for port {}", port)
        return None

    try:
        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.status == psutil.CONN_LISTEN
                and hasattr(conn.laddr, "ip")
                and hasattr(conn.laddr, "port")
                and conn.laddr.ip in ("127.0.0.1", "0.0.0.0", "::")  # type: ignore[union-attr]
                and conn.laddr.port == port  # type: ignore[union-attr]
            ):
                pid: int | None = conn.pid
                return pid
    except Exception as exc:
        logger.debug("Failed to find PID for port {}: {}", port, exc)
    return None


def verify_pboy_process(pid: int) -> bool:
    """Verify that the given PID is actually a pboy daemon process.

    Checks that the cmdline contains 'pboy' or 'python -m pboy'.
    Returns False if verification fails or psutil is unavailable.
    """
    try:
        import psutil

        proc = psutil.Process(pid)
        cmdline = proc.cmdline()
        cmdline_str = " ".join(cmdline).lower()
        return "pboy" in cmdline_str or "python -m pboy" in cmdline_str or "python3 -m pboy" in cmdline_str
    except Exception as exc:
        logger.debug("Failed to verify process {}: {}", pid, exc)
        return False


def stop_legacy_daemon(info: LegacyDaemonInfo, *, force: bool = False, timeout: float = 10.0) -> bool:
    """Stop a legacy daemon safely.

    Returns True if stopped successfully, False otherwise.
    Raises RuntimeError if verification fails.
    """
    if not info.pid:
        raise RuntimeError(
            f"Cannot stop legacy daemon at {info.url}: PID not found. "
            "Install psutil (pip install psutil) or stop manually."
        )

    if not verify_pboy_process(info.pid):
        raise RuntimeError(
            f"Cannot stop legacy daemon: PID {info.pid} failed verification. "
            "It may not be a pboy process. Stop manually."
        )

    logger.info("Stopping legacy daemon at {} (PID {})...", info.url, info.pid)

    try:
        if sys.platform == "win32":
            import signal as win_signal

            os.kill(info.pid, win_signal.CTRL_C_EVENT)  # type: ignore[attr-defined]
        else:
            os.kill(info.pid, signal.SIGTERM)
    except ProcessLookupError:
        logger.debug("Process {} already exited", info.pid)
        return True
    except Exception as exc:
        raise RuntimeError(f"Failed to send SIGTERM to PID {info.pid}: {exc}") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            import psutil

            if not psutil.pid_exists(info.pid):
                logger.info("Legacy daemon stopped successfully.")
                return True
        except ImportError:
            try:
                os.kill(info.pid, 0)
            except ProcessLookupError:
                logger.info("Legacy daemon stopped successfully.")
                return True
        time.sleep(0.1)

    if not force:
        logger.warning("Legacy daemon did not stop within {}s", timeout)
        return False

    logger.warning("Force-killing legacy daemon PID {}...", info.pid)
    try:
        if sys.platform == "win32":
            import subprocess

            subprocess.run(["taskkill", "/F", "/PID", str(info.pid)], check=False)
        else:
            os.kill(info.pid, signal.SIGKILL)
        time.sleep(1.0)
        return True
    except Exception as exc:
        logger.error("Force-kill failed: {}", exc)
        return False


def prompt_stop_legacy(info: LegacyDaemonInfo) -> bool:
    """Prompt the user to stop a legacy daemon (interactive TTY only).

    Returns True if user confirmed and daemon was stopped, False otherwise.
    """
    version_str = f" (v{info.version})" if info.version else ""
    pid_str = f" (PID {info.pid})" if info.pid else ""
    print(
        f"\nA legacy Pageboy daemon{version_str} is running at {info.url}{pid_str}.\n"
        "Stop it and continue? [y/N] ",
        end="",
        flush=True,
    )

    try:
        response = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return False

    if response not in ("y", "yes"):
        print("Cancelled. Stop the daemon manually and try again.")
        return False

    try:
        if stop_legacy_daemon(info, force=False, timeout=10.0):
            print("Legacy daemon stopped successfully.")
            return True
        print(
            "Warning: daemon did not stop within 10s. "
            "Stop it manually or use pboy daemon stop --legacy --force"
        )
        return False
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return False


def check_replace_legacy_env() -> bool:
    """Check if PB_REPLACE_LEGACY_DAEMON=1 opt-in is set."""
    return os.environ.get("PB_REPLACE_LEGACY_DAEMON", "").strip() == "1"


def handle_legacy_daemon_conflict(info: LegacyDaemonInfo, *, context: str = "CLI") -> None:
    """Handle a detected legacy daemon conflict.

    - Interactive TTY: prompt to stop
    - Non-interactive: error unless PB_REPLACE_LEGACY_DAEMON=1

    Raises SystemExit on conflict/cancellation.
    """
    if sys.stdin.isatty() and sys.stdout.isatty():
        if prompt_stop_legacy(info):
            return
        raise SystemExit(1)

    if check_replace_legacy_env():
        logger.info("PB_REPLACE_LEGACY_DAEMON=1 set; stopping legacy daemon...")
        try:
            if stop_legacy_daemon(info, force=False, timeout=15.0):
                return
            raise RuntimeError("Legacy daemon did not stop within 15s")
        except RuntimeError as exc:
            logger.error("{}", exc)
            raise SystemExit(1) from exc

    version_str = f" (v{info.version})" if info.version else ""
    pid_str = f" (PID {info.pid})" if info.pid else ""
    logger.error(
        "Error: A legacy Pageboy daemon{} is running at {}{}, but this is a non-interactive session.",
        version_str,
        info.url,
        pid_str,
    )
    logger.error(
        "Stop it manually: pboy daemon stop --legacy\n"
        "Or set PB_REPLACE_LEGACY_DAEMON=1 to stop automatically (use with caution in automation)."
    )
    raise SystemExit(1)
