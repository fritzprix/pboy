"""Runtime file for daemon discovery using OS locks.

The daemon writes a runtime file (daemon.json) in the data dir and holds an
exclusive OS lock on it for its lifetime. Clients discover the daemon by reading
this file and checking if the lock is still held.

Liveness is decided by the OS lock, not file existence. A crash or power loss
leaves the file but releases the lock, so clients treat it as stale.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class DaemonRuntimeInfo:
    """Information about a running daemon written to the runtime file."""

    pid: int
    host: str
    port: int
    base_url: str
    version: str
    started_at: float
    instance_token: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "base_url": self.base_url,
            "version": self.version,
            "started_at": self.started_at,
            "instance_token": self.instance_token,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DaemonRuntimeInfo:
        return cls(
            pid=data["pid"],
            host=data["host"],
            port=data["port"],
            base_url=data["base_url"],
            version=data["version"],
            started_at=data["started_at"],
            instance_token=data["instance_token"],
        )


class DaemonRuntimeFile:
    """Manages the daemon runtime file with OS-level locking.

    Usage (daemon):
        runtime = DaemonRuntimeFile(data_dir)
        with runtime.acquire_exclusive() as token:
            runtime.write_info(host, port, base_url, token)
            # daemon runs...
        runtime.cleanup()  # best-effort removal

    Usage (client discovery):
        runtime = DaemonRuntimeFile(data_dir)
        info = runtime.read_if_alive()
        if info:
            # connect to info.base_url and verify /healthz token
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.runtime_path = data_dir / "daemon.json"
        self.lock_path = data_dir / "daemon.lock"
        self._lock_fh: Any = None

    @contextlib.contextmanager
    def acquire_exclusive(self) -> Any:
        """Acquire an exclusive lock, refusing if already held (another daemon running).

        Yields the instance_token for this daemon start.
        Raises RuntimeError if the lock is already held.
        """
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock_fh = open(self.lock_path, "a+", encoding="utf-8")  # noqa: SIM115
        token = secrets.token_urlsafe(32)
        try:
            if not self._try_lock():
                raise RuntimeError(
                    f"Another Pageboy daemon is already running (lock held: {self.lock_path})"
                )
            yield token
        finally:
            self._unlock()
            if self._lock_fh is not None:
                self._lock_fh.close()
                self._lock_fh = None

    def _try_lock(self) -> bool:
        """Try to acquire an exclusive non-blocking lock. Return False if already held."""
        assert self._lock_fh is not None
        if sys.platform == "win32":
            import msvcrt

            # Ensure file has at least 1 byte before locking. Never read/write locked bytes.
            # On Windows, msvcrt.locking creates mandatory locks: reading locked bytes raises PermissionError.
            try:
                size = self._lock_fh.seek(0, 2)  # Seek to end to get size
                if size == 0:
                    self._lock_fh.write("L")
                    self._lock_fh.flush()
                self._lock_fh.seek(0)
            except (OSError, PermissionError):
                # Another process may have locked it between open and here
                return False

            try:
                # Lock byte 0. msvcrt.locking creates mandatory locks on Windows.
                msvcrt.locking(self._lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except (OSError, PermissionError):
                # EACCES/EDEADLOCK or PermissionError: another process holds the lock
                return False
        else:
            import fcntl

            try:
                fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False

    def _unlock(self) -> None:
        """Release the lock."""
        if self._lock_fh is None:
            return
        if sys.platform == "win32":
            import msvcrt

            self._lock_fh.seek(0)
            with contextlib.suppress(OSError):
                msvcrt.locking(self._lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)

    def write_info(self, host: str, port: int, base_url: str, instance_token: str) -> None:
        """Write daemon info atomically (temp file + rename) while holding the lock."""
        import pboy

        info = DaemonRuntimeInfo(
            pid=os.getpid(),
            host=host,
            port=port,
            base_url=base_url,
            version=pboy.__version__,
            started_at=time.time(),
            instance_token=instance_token,
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.runtime_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(info.to_dict(), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            temp_path.replace(self.runtime_path)
            logger.debug("Wrote daemon runtime file: {}", self.runtime_path)
        except Exception as exc:
            logger.warning("Failed to write runtime file {}: {}", self.runtime_path, exc)
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)

    def read_if_alive(self) -> DaemonRuntimeInfo | None:
        """Read runtime info if the lock is still held (daemon alive).

        Returns None if the file doesn't exist, is stale (lock not held), or invalid.
        """
        if not self.runtime_path.is_file():
            return None
        try:
            with open(self.runtime_path, encoding="utf-8") as f:
                data = json.load(f)
            info = DaemonRuntimeInfo.from_dict(data)
        except Exception as exc:
            logger.debug("Invalid runtime file {}: {}", self.runtime_path, exc)
            return None

        if self._is_lock_held():
            return info
        logger.debug("Runtime file exists but lock is not held (stale): {}", self.runtime_path)
        return None

    def _is_lock_held(self) -> bool:
        """Check if the lock file is currently held by another process."""
        if not self.lock_path.is_file():
            return False
        try:
            if sys.platform == "win32":
                import msvcrt

                # On Windows, msvcrt.locking creates mandatory locks. Another process holding
                # the lock will cause PermissionError when we try to read/write locked bytes,
                # even though we can open the file.
                try:
                    with open(self.lock_path, "r+", encoding="utf-8") as fh:
                        # Never read/write locked bytes. Just try to acquire and release the lock.
                        fh.seek(0)
                        try:
                            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                            return False
                        except (OSError, PermissionError):
                            # EACCES/EDEADLOCK or PermissionError: lock is held
                            return True
                except (OSError, PermissionError):
                    # Can't open or lock: assume held
                    return True
            else:
                with open(self.lock_path, "a+", encoding="utf-8") as fh:
                    import fcntl

                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                        return False
                    except OSError:
                        return True
        except Exception as exc:
            logger.debug("Failed to check lock {}: {}", self.lock_path, exc)
            return False

    def cleanup(self) -> None:
        """Best-effort cleanup of runtime and lock files (not required for correctness)."""
        with contextlib.suppress(OSError):
            self.runtime_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            self.lock_path.unlink(missing_ok=True)

    def wait_for_lock_release(self, timeout: float = 10.0, poll_interval: float = 0.1) -> bool:
        """Wait until the lock is released or timeout. Return True if released."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._is_lock_held():
                return True
            time.sleep(poll_interval)
        return False
