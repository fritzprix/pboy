"""Harness execution history domain model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from pboy.models.cloudevent import current_iso_time

ExecutionStatus = Literal["pending", "running", "completed", "failed", "timeout"]


def generate_exec_id() -> str:
    """Generate execution id in exec_YYYYMMDD_<12hex> format."""
    now_str = datetime.now(UTC).strftime("%Y%m%d")
    short_hash = uuid.uuid4().hex[:12]
    return f"exec_{now_str}_{short_hash}"


class ExecutionData(BaseModel):
    """Execution record for an agent harness invocation."""

    execution_id: str = Field(default_factory=generate_exec_id)
    hook_name: str
    event_id: str
    pid: int | None = None
    status: ExecutionStatus = "pending"
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    started_at: str = Field(default_factory=current_iso_time)
    finished_at: str | None = None


__all__ = [
    "ExecutionData",
    "ExecutionStatus",
    "current_iso_time",
    "generate_exec_id",
]
