"""Event completion / lifecycle state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pboy.models.cloudevent import current_iso_time

COMPLETION_STATE_KEY = "completion"
EXECUTION_STATE_KEY = "execution"

CompletionStatus = Literal["completed"]


class SyncResult(BaseModel):
    """Outbound sync attempt for a completion."""

    target: str
    ok: bool
    detail: str = ""
    message_id: str | None = None


class EventCompletion(BaseModel):
    """Persisted completion record (state key ``completion``)."""

    status: CompletionStatus = "completed"
    summary: str = "Completed"
    completed_at: str = Field(default_factory=current_iso_time)
    sync: list[str] = Field(default_factory=list)
    sync_results: list[SyncResult] = Field(default_factory=list)

    def as_state(self) -> dict[str, Any]:
        return self.model_dump()


def parse_sync_targets(raw: str | None) -> list[str]:
    """Parse ``--sync telegram`` / ``telegram,slack`` into normalized target names."""
    if not raw:
        return []
    targets: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        token = part.strip().lower()
        if not token:
            continue
        # Allow telegram:message → telegram
        name = token.split(":", 1)[0]
        if name in seen:
            continue
        seen.add(name)
        targets.append(name)
    return targets


__all__ = [
    "COMPLETION_STATE_KEY",
    "EXECUTION_STATE_KEY",
    "CompletionStatus",
    "EventCompletion",
    "SyncResult",
    "parse_sync_targets",
]
