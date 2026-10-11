"""HTTP request/response models for the Pageboy daemon API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EventSource = Literal["jira", "slack", "telegram", "confluence", "generic"]


class EventInjectRequest(BaseModel):
    """Inject a raw platform payload into the Engine."""

    source: EventSource
    payload: dict[str, Any] = Field(default_factory=dict)
    dispatch: bool = True


class ExecutionSummary(BaseModel):
    execution_id: str
    hook_name: str
    status: str
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None


class EventInjectResponse(BaseModel):
    event_id: str
    source: str
    type: str
    matched_hooks: list[str]
    executions: list[ExecutionSummary]


class StatePutRequest(BaseModel):
    key: str = "execution"
    value: dict[str, Any] = Field(default_factory=dict)


class StatePutResponse(BaseModel):
    event_id: str
    key: str
    value: dict[str, Any]


class StateGetResponse(BaseModel):
    event_id: str
    key: str
    value: dict[str, Any] | None


class EventListItem(BaseModel):
    id: str
    source: str
    type: str
    time: str


class EventListResponse(BaseModel):
    events: list[EventListItem]
