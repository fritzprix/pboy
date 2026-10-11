"""Unit tests for HarnessExecutor error handling, empty exec, timeouts, and env vars."""

from __future__ import annotations

import sys
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
