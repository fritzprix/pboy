"""Unit tests for HarnessExecutor error handling, empty exec, timeouts, and env vars."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from unittest.mock import patch

import pytest

from pboy.harness.executor import HarnessExecutor
from pboy.models.cloudevent import CloudEvent
from pboy.models.hook import Hook
from pboy.store.sqlite import SQLiteStore


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
    assert result.stderr == "Empty exec command"


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


@pytest.mark.asyncio
async def test_executor_timeout_kills_process(tmp_store: SQLiteStore, dummy_event: CloudEvent) -> None:
    """Harness execution exceeding timeout must be killed and marked as timeout."""
    executor = HarnessExecutor(store=tmp_store)
    # Run python script that sleeps for 5 seconds with a 1 second timeout
    hook = Hook(
        name="timeout-hook",
        topic="test:event.created",
        exec=f"{sys.executable} -c 'import time; time.sleep(5)'",
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
            exec="echo smoke-ok",
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
            exec="sh -c 'exit 42'",
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
        exec=f"{sys.executable} -c 'import os; print(os.environ.get(\"PB_LEASE_TOKEN\"))'",
        timeout=10,
    )
    result = await executor.run(hook, dummy_event, lease_token="test_lease_token_val_42")
    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.stdout is not None
    assert "test_lease_token_val_42" in result.stdout


@pytest.mark.asyncio
async def test_executor_injects_daemon_url_only_when_configured(
    tmp_store: SQLiteStore,
    dummy_event: CloudEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pboy.core.config as config_mod
    from pboy.core.config import Settings

    cfg = Settings(daemon_url="http://127.0.0.1:9090", auth_token="tok")
    monkeypatch.setattr(config_mod, "_settings", cfg)

    executor = HarnessExecutor(store=tmp_store)
    hook = Hook(
        name="daemon-env",
        topic="test:event.created",
        exec=(
            f"{sys.executable} -c "
            "'import os; print(os.environ.get(\"PB_DAEMON_URL\",\"\")); "
            "print(os.environ.get(\"PB_AUTH_TOKEN\",\"\"))'"
        ),
        timeout=10,
    )
    result = await executor.run(hook, dummy_event)
    assert result.status == "completed"
    assert result.stdout is not None
    assert "http://127.0.0.1:9090" in result.stdout
    assert "tok" in result.stdout
