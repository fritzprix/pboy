"""Minimal subprocess harness executor."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex

from loguru import logger

from pboy.core.config import get_settings
from pboy.models.cloudevent import CloudEvent, current_iso_time
from pboy.models.execution import ExecutionData, ExecutionStatus
from pboy.models.hook import Hook
from pboy.store.base import StoreBase


def _unwrap_win_token(token: str) -> str:
    """Strip one layer of Windows-style double quotes kept by ``shlex.split(posix=False)``."""
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1].replace('\\"', '"')
    return token


def _split_exec(command: str) -> list[str]:
    """Tokenize ``hook.exec`` for ``create_subprocess_exec`` (POSIX + Windows)."""
    if os.name == "nt":
        # Keep backslash paths; shlex retains surrounding quotes — unwrap for execve-style spawn.
        return [_unwrap_win_token(tok) for tok in shlex.split(command, posix=False)]
    return shlex.split(command)


async def _communicate(
    proc: asyncio.subprocess.Process,
    stdin_payload: bytes,
) -> tuple[bytes, bytes]:
    """Feed stdin and collect stdout/stderr concurrently (stdlib communicate shape).

    Children that exit without reading stdin (e.g. ``echo``) close the pipe early.
    Under uvloop (uvicorn[standard]) that surfaces as RuntimeError instead of
    BrokenPipeError — treat both as non-fatal and still collect output.
    """

    async def _feed_stdin() -> None:
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(stdin_payload)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            pass
        finally:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError):
                proc.stdin.close()
                await proc.stdin.wait_closed()

    async def _read(stream: asyncio.StreamReader | None) -> bytes:
        return await stream.read() if stream is not None else b""

    _, stdout_b, stderr_b = await asyncio.gather(
        _feed_stdin(),
        _read(proc.stdout),
        _read(proc.stderr),
    )
    await proc.wait()
    return stdout_b, stderr_b


class HarnessExecutor:
    """Spawn hook commands as subprocesses with CloudEvent stdin and PB_* env."""

    def __init__(self, store: StoreBase) -> None:
        self.store = store

    async def run(
        self,
        hook: Hook,
        event: CloudEvent,
        lease_token: str | None = None,
    ) -> ExecutionData:
        """Execute hook.exec, capture output, and persist an execution record."""
        settings = get_settings()
        exec_data = ExecutionData(
            hook_name=hook.name,
            event_id=event.id,
            status="running",
            started_at=current_iso_time(),
        )
        await self.store.record_execution(exec_data)

        try:
            argv = _split_exec(hook.exec)
        except ValueError as exc:
            return await self._finish(
                exec_data,
                status="failed",
                exit_code=127,
                stderr=f"Invalid exec command: {exc}",
            )

        if not argv:
            return await self._finish(
                exec_data,
                status="failed",
                exit_code=127,
                stderr="Empty exec command",
            )

        env = os.environ.copy()
        env.update(
            {
                "PB_EVENT_ID": event.id,
                "PB_SOURCE": event.source,
                "PB_HOOK_NAME": hook.name,
            }
        )
        # Only inject daemon URL when the daemon process configured one (non-empty).
        if settings.daemon_url.strip():
            env["PB_DAEMON_URL"] = settings.daemon_url.strip()
        if lease_token:
            env["PB_LEASE_TOKEN"] = lease_token

        stdin_payload = event.model_dump_json().encode("utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            return await self._finish(
                exec_data,
                status="failed",
                exit_code=127,
                stderr=f"Command not found: {argv[0]}",
            )
        except OSError as exc:
            return await self._finish(
                exec_data,
                status="failed",
                exit_code=127,
                stderr=f"Failed to spawn: {exc}",
            )

        exec_data.pid = proc.pid
        await self.store.record_execution(exec_data)

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                _communicate(proc, stdin_payload),
                timeout=float(hook.timeout),
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.communicate()
            logger.warning(
                "Harness timed out for hook={} event={} timeout={}s",
                hook.name,
                event.id,
                hook.timeout,
            )
            return await self._finish(
                exec_data,
                status="timeout",
                exit_code=-1,
                stderr=f"Timed out after {hook.timeout}s",
            )

        exit_code = proc.returncode if proc.returncode is not None else -1
        final_status: ExecutionStatus = "completed" if exit_code == 0 else "failed"
        return await self._finish(
            exec_data,
            status=final_status,
            exit_code=exit_code,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
        )

    async def _finish(
        self,
        exec_data: ExecutionData,
        *,
        status: ExecutionStatus,
        exit_code: int | None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> ExecutionData:
        exec_data.status = status
        exec_data.exit_code = exit_code
        exec_data.stdout = stdout
        exec_data.stderr = stderr
        exec_data.finished_at = current_iso_time()
        await self.store.record_execution(exec_data)
        return exec_data


__all__ = ["HarnessExecutor"]
