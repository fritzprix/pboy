"""Unit tests for HarnessExecutor error handling, empty exec, timeouts, and env vars."""

from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable
from unittest.mock import patch

import pytest

import pboy.harness.executor as executor_mod
from pboy.harness.executor import HarnessExecutor, _split_exec
from pboy.models.cloudevent import CloudEvent
from pboy.models.hook import Hook
from pboy.store.sqlite import SQLiteStore
from tests.helpers import py_c, py_exit, py_print


def test_split_exec_py_print_roundtrip() -> None:
    argv = _split_exec(py_print("hello"))
    assert argv[-2:] == ["-c", "print('hello')"]
    assert argv[0]


def test_posix_shlex_eats_windows_backslashes() -> None:
    """Regression marker: default shlex (posix=True) collapses ``\\`` escapes (#29)."""
    assert shlex.split(r"C:\Python312\python.exe script.py") == [
        "C:Python312python.exe",
        "script.py",
    ]


def test_split_exec_windows_keeps_backslash_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod.os, "name", "nt")
    assert _split_exec(r"C:\Python312\python.exe script.py") == [
        r"C:\Python312\python.exe",
        "script.py",
    ]


def test_split_exec_windows_unwraps_quoted_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod.os, "name", "nt")
    assert _split_exec(r'"C:\Program Files\Python312\python.exe" script.py') == [
        r"C:\Program Files\Python312\python.exe",
        "script.py",
    ]


@pytest.fixture
def dummy_event() -> CloudEvent:
    return CloudEvent(
        source="unit_test",
        type="test:event.created",
        data={"message": "hello"},
    )


@pytest.mark.asyncio
async def test_executor_invalid_exec_syntax(tmp_store: SQLiteStore, dummy_event: CloudEvent) -> None:
    """Invalid shell quote syntax should fail with exit code 127."""
    executor = HarnessExecutor(store=tmp_store)
    hook = Hook.model_construct(
        name="syntax-err-hook",
        topic="test:event.created",
        exec="echo 'unterminated_quote",
        concurrency=1,
        timeout=10,
        status="active",
        created_at="2026-01-01T00:00:00Z",
    )
    result = await executor.run(hook, dummy_event)
    assert result.status == "failed"
    assert result.exit_code == 127
    assert result.stderr is not None
    assert "Invalid exec command" in result.stderr


@pytest.mark.asyncio
async def test_executor_empty_argv(tmp_store: SQLiteStore, dummy_event: CloudEvent) -> None:
    """Empty or all-whitespace exec should fail with exit code 127."""
    executor = HarnessExecutor(store=tmp_store)
    hook = Hook.model_construct(
        name="empty-exec-hook",
        topic="test:event.created",
        exec="    ",
        concurrency=1,
        timeout=10,
        status="active",
        created_at="2026-01-01T00:00:00Z",
    )
    result = await executor.run(hook, dummy_event)
    assert result.status == "failed"
    assert result.exit_code == 127
    assert result.stderr is not None
    assert result.stderr.startswith("Empty exec command")
    assert "exec=" in result.stderr


@pytest.mark.asyncio
async def test_executor_command_not_found(tmp_store: SQLiteStore, dummy_event: CloudEvent) -> None:
    """Non-existent executable should catch FileNotFoundError and fail with exit code 127."""
    executor = HarnessExecutor(store=tmp_store)
    hook = Hook(
        name="missing-binary-hook",
        topic="test:event.created",
        exec="nonexistent_binary_pboy_test_123456",
        timeout=10,
    )
    result = await executor.run(hook, dummy_event)
    assert result.status == "failed"
    assert result.exit_code == 127
    assert result.stderr is not None
    assert "Command not found" in result.stderr
    assert "nonexistent_binary_pboy_test_123456" in result.stderr
    assert "exec=" in result.stderr
    assert "argv=" in result.stderr


@pytest.mark.asyncio
async def test_executor_oserror_on_spawn(tmp_store: SQLiteStore, dummy_event: CloudEvent) -> None:
    """OSError during process spawn should return failed status with exit code 127."""
    executor = HarnessExecutor(store=tmp_store)
    hook = Hook(
        name="oserror-hook",
        topic="test:event.created",
        exec="echo test",
        timeout=10,
    )
    with patch("asyncio.create_subprocess_exec", side_effect=OSError("Exec format error")):
        result = await executor.run(hook, dummy_event)
        assert result.status == "failed"
        assert result.exit_code == 127
        assert result.stderr is not None
        assert "Failed to spawn: Exec format error" in result.stderr
        assert "exec='echo test'" in result.stderr
        assert "argv=" in result.stderr


@pytest.mark.asyncio
async def test_executor_timeout_kills_process(tmp_store: SQLiteStore, dummy_event: CloudEvent) -> None:
    """Harness execution exceeding timeout must be killed and marked as timeout."""
    executor = HarnessExecutor(store=tmp_store)
    # Run python script that sleeps for 5 seconds with a 1 second timeout
    hook = Hook(
        name="timeout-hook",
        topic="test:event.created",
        exec=py_c("import time; time.sleep(5)"),
        timeout=1,
    )
    result = await executor.run(hook, dummy_event)
    assert result.status == "timeout"
    assert result.exit_code == -1
    assert result.stderr is not None
    assert "Timed out after 1s" in result.stderr


def _run_under_uvloop(coro_factory: Callable[[], Awaitable[None]]) -> None:
    uvloop = pytest.importorskip("uvloop")
    loop = uvloop.new_event_loop()
    try:
        loop.run_until_complete(coro_factory())
    finally:
        loop.close()


def test_executor_echo_ignores_stdin_under_uvloop(
    tmp_store: SQLiteStore,
    dummy_event: CloudEvent,
) -> None:
    """Short-lived commands that ignore stdin must not fail under uvloop."""

    async def _run() -> None:
        executor = HarnessExecutor(store=tmp_store)
        hook = Hook(
            name="echo-stdin",
            topic="test:event.created",
            exec=py_print("smoke-ok"),
            timeout=10,
        )
        result = await executor.run(hook, dummy_event)
        assert result.status == "completed"
        assert result.exit_code == 0
        assert result.stdout is not None
        assert "smoke-ok" in result.stdout

    _run_under_uvloop(_run)


def test_executor_nonzero_exit_ignores_stdin_under_uvloop(
    tmp_store: SQLiteStore,
    dummy_event: CloudEvent,
) -> None:
    """Non-zero exit with ignored stdin must still record failed + exit_code."""

    async def _run() -> None:
        executor = HarnessExecutor(store=tmp_store)
        hook = Hook(
            name="fail-stdin",
            topic="test:event.created",
            exec=py_exit(42),
            timeout=10,
        )
        result = await executor.run(hook, dummy_event)
        assert result.status == "failed"
        assert result.exit_code == 42

    _run_under_uvloop(_run)


@pytest.mark.asyncio
async def test_executor_passes_lease_token_in_env(tmp_store: SQLiteStore, dummy_event: CloudEvent) -> None:
    """Executor must expose PB_LEASE_TOKEN to subprocess environment when provided."""
    executor = HarnessExecutor(store=tmp_store)
    hook = Hook(
        name="env-hook",
        topic="test:event.created",
        exec=py_c("import os; print(os.environ.get('PB_LEASE_TOKEN'))"),
        timeout=10,
    )
    result = await executor.run(hook, dummy_event, lease_token="test_lease_token_val_42")
    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.stdout is not None
    assert "test_lease_token_val_42" in result.stdout


@pytest.mark.asyncio
async def test_executor_does_not_inject_daemon_url_or_auth_token(
    tmp_store: SQLiteStore,
    dummy_event: CloudEvent,
) -> None:
    executor = HarnessExecutor(store=tmp_store)
    hook = Hook(
        name="clean-env",
        topic="test:event.created",
        exec=py_c(
            "import os; "
            "print('HAS_DAEMON=' + str('PB_DAEMON_URL' in os.environ)); "
            "print('HAS_AUTH=' + str('PB_AUTH_TOKEN' in os.environ))"
        ),
        timeout=10,
    )
    result = await executor.run(hook, dummy_event)
    assert result.status == "completed"
    assert result.stdout is not None
    assert "HAS_DAEMON=False" in result.stdout
    assert "HAS_AUTH=False" in result.stdout
