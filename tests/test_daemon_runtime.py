"""Tests for daemon runtime file with OS locks (issue #74)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from pboy.daemon.runtime import DaemonRuntimeFile, DaemonRuntimeInfo


def test_daemon_runtime_info_serialization() -> None:
    """DaemonRuntimeInfo can serialize to/from dict."""
    info = DaemonRuntimeInfo(
        pid=1234,
        host="127.0.0.1",
        port=17680,
        base_url="http://127.0.0.1:17680",
        version="0.5.0",
        started_at=1234567890.0,
        instance_token="test-token-abc",
    )
    data = info.to_dict()
    assert data["pid"] == 1234
    assert data["instance_token"] == "test-token-abc"

    restored = DaemonRuntimeInfo.from_dict(data)
    assert restored.pid == info.pid
    assert restored.instance_token == info.instance_token


def test_runtime_file_acquire_exclusive_succeeds(tmp_path: Path) -> None:
    """acquire_exclusive succeeds when no other daemon holds the lock."""
    runtime = DaemonRuntimeFile(tmp_path)

    with runtime.acquire_exclusive() as token:
        assert isinstance(token, str)
        assert len(token) > 0
        assert runtime.lock_path.is_file()


def test_runtime_file_acquire_exclusive_refuses_second_daemon(tmp_path: Path) -> None:
    """Second daemon cannot acquire the lock while first daemon holds it."""
    runtime1 = DaemonRuntimeFile(tmp_path)
    runtime2 = DaemonRuntimeFile(tmp_path)

    with runtime1.acquire_exclusive(), pytest.raises(RuntimeError, match="lock held"):  # noqa: SIM117
        with runtime2.acquire_exclusive():
            pass


def test_runtime_file_lock_released_after_context(tmp_path: Path) -> None:
    """Lock is released when context exits, allowing a second daemon to start."""
    runtime1 = DaemonRuntimeFile(tmp_path)
    runtime2 = DaemonRuntimeFile(tmp_path)

    with runtime1.acquire_exclusive():
        pass

    with runtime2.acquire_exclusive() as token:
        assert isinstance(token, str)


def test_runtime_file_write_info_creates_json(tmp_path: Path) -> None:
    """write_info creates a JSON file with daemon metadata."""
    runtime = DaemonRuntimeFile(tmp_path)

    with runtime.acquire_exclusive() as token:
        runtime.write_info("127.0.0.1", 17680, "http://127.0.0.1:17680", token)

    assert runtime.runtime_path.is_file()
    with open(runtime.runtime_path, encoding="utf-8") as f:
        data = json.load(f)

    assert data["host"] == "127.0.0.1"
    assert data["port"] == 17680
    assert data["instance_token"] == token
    assert data["pid"] == os.getpid()


def test_runtime_file_read_if_alive_returns_none_when_no_file(tmp_path: Path) -> None:
    """read_if_alive returns None when the runtime file doesn't exist."""
    runtime = DaemonRuntimeFile(tmp_path)
    info = runtime.read_if_alive()
    assert info is None


def test_runtime_file_read_if_alive_returns_none_when_lock_not_held(tmp_path: Path) -> None:
    """read_if_alive returns None when file exists but lock is not held (stale file after crash)."""
    runtime = DaemonRuntimeFile(tmp_path)

    with runtime.acquire_exclusive() as token:
        runtime.write_info("127.0.0.1", 17680, "http://127.0.0.1:17680", token)

    info = runtime.read_if_alive()
    assert info is None


def test_runtime_file_read_if_alive_returns_info_when_lock_held(tmp_path: Path) -> None:
    """read_if_alive returns DaemonRuntimeInfo when lock is held by another process."""
    import threading

    runtime = DaemonRuntimeFile(tmp_path)
    token_holder = "daemon-token-xyz"

    def hold_lock() -> None:
        with runtime.acquire_exclusive():
            runtime.write_info("127.0.0.1", 17680, "http://127.0.0.1:17680", token_holder)
            time.sleep(1.0)

    thread = threading.Thread(target=hold_lock, daemon=True)
    thread.start()
    time.sleep(0.1)

    runtime2 = DaemonRuntimeFile(tmp_path)
    info = runtime2.read_if_alive()
    assert info is not None
    assert info.host == "127.0.0.1"
    assert info.port == 17680
    assert info.instance_token == token_holder

    thread.join(timeout=2.0)


def test_runtime_file_cleanup_removes_files(tmp_path: Path) -> None:
    """cleanup() removes runtime and lock files."""
    runtime = DaemonRuntimeFile(tmp_path)

    with runtime.acquire_exclusive() as token:
        runtime.write_info("127.0.0.1", 17680, "http://127.0.0.1:17680", token)

    assert runtime.runtime_path.is_file()
    assert runtime.lock_path.is_file()

    runtime.cleanup()

    assert not runtime.runtime_path.is_file()
    assert not runtime.lock_path.is_file()


def test_runtime_file_wait_for_lock_release_returns_true_when_released(tmp_path: Path) -> None:
    """wait_for_lock_release returns True when lock is released before timeout."""
    import threading

    runtime = DaemonRuntimeFile(tmp_path)

    def hold_briefly() -> None:
        with runtime.acquire_exclusive():
            time.sleep(0.2)

    thread = threading.Thread(target=hold_briefly, daemon=True)
    thread.start()
    time.sleep(0.05)

    runtime2 = DaemonRuntimeFile(tmp_path)
    released = runtime2.wait_for_lock_release(timeout=1.0, poll_interval=0.05)
    assert released is True

    thread.join(timeout=2.0)


def test_runtime_file_wait_for_lock_release_returns_false_on_timeout(tmp_path: Path) -> None:
    """wait_for_lock_release returns False if lock is still held at timeout."""
    import threading

    runtime = DaemonRuntimeFile(tmp_path)

    def hold_long() -> None:
        with runtime.acquire_exclusive():
            time.sleep(2.0)

    thread = threading.Thread(target=hold_long, daemon=True)
    thread.start()
    time.sleep(0.05)

    runtime2 = DaemonRuntimeFile(tmp_path)
    released = runtime2.wait_for_lock_release(timeout=0.3, poll_interval=0.05)
    assert released is False

    thread.join(timeout=3.0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific lock test")
def test_runtime_file_windows_locking(tmp_path: Path) -> None:
    """Windows msvcrt.locking works as expected."""
    runtime1 = DaemonRuntimeFile(tmp_path)
    runtime2 = DaemonRuntimeFile(tmp_path)

    with runtime1.acquire_exclusive() as token:
        assert isinstance(token, str)

        with pytest.raises(RuntimeError, match="lock held"), runtime2.acquire_exclusive():
            pass


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-specific lock test")
def test_runtime_file_posix_locking(tmp_path: Path) -> None:
    """POSIX fcntl.flock works as expected."""
    runtime1 = DaemonRuntimeFile(tmp_path)
    runtime2 = DaemonRuntimeFile(tmp_path)

    with runtime1.acquire_exclusive() as token:
        assert isinstance(token, str)

        with pytest.raises(RuntimeError, match="lock held"), runtime2.acquire_exclusive():
            pass


def test_runtime_file_invalid_json_returns_none(tmp_path: Path) -> None:
    """read_if_alive returns None when runtime file contains invalid JSON."""
    runtime = DaemonRuntimeFile(tmp_path)

    runtime.runtime_path.write_text("not valid json", encoding="utf-8")
    runtime.lock_path.touch()

    info = runtime.read_if_alive()
    assert info is None


def test_runtime_file_missing_fields_returns_none(tmp_path: Path) -> None:
    """read_if_alive returns None when runtime file is missing required fields."""
    runtime = DaemonRuntimeFile(tmp_path)

    with open(runtime.runtime_path, "w", encoding="utf-8") as f:
        json.dump({"host": "127.0.0.1"}, f)
    runtime.lock_path.touch()

    info = runtime.read_if_alive()
    assert info is None
