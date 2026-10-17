"""HTTP request/response models for the Pageboy daemon API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pboy.models.hook import Hook, HookCreate

EventSource = Literal["jira", "slack", "telegram", "confluence", "generic"]


class EventInjectRequest(BaseModel):
    """Inject a raw platform payload into the Engine."""

    source: EventSource = Field(
        description=(
            "Inbound platform identifier (`telegram`, `slack`, `jira`, `confluence`, or `generic`). "
            "Determines how `payload` is normalized into CloudEvent `data`."
        ),
        examples=["telegram"],
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Platform-specific raw event payload. See README for expected shape per `source` "
            "(e.g. Telegram expects `{\"message\": {\"message_id\": 1, \"chat\": {\"id\": 123}, "
            "\"from\": {\"id\": 456, \"username\": \"alice\"}, \"text\": \"hello\"}}`)."
        ),
        examples=[
            {
                "message": {
                    "message_id": 42,
                    "chat": {"id": 123456789},
                    "from": {"id": 987654321, "username": "alice"},
                    "text": "hello from curl",
                }
            }
        ],
    )
    dispatch: bool = Field(
        default=True,
        description="Whether to match and execute registered hooks immediately.",
    )


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


class HookListResponse(BaseModel):
    hooks: list[Hook]


class HookCreateRequest(HookCreate):
    """Create hook payload (same fields as HookCreate)."""


class HookDeleteResponse(BaseModel):
    deleted: bool
    name: str


class EventCompleteRequest(BaseModel):
    summary: str = Field(
        default="Completed",
        description="Human-readable completion summary (sent as reply text/caption when `sync` is set).",
        examples=["Resolved"],
    )
    sync: list[str] = Field(
        default_factory=list,
        description=(
            "Outbound targets to notify on completion as a JSON list of strings "
            "(e.g. `[\"telegram\"]`). Note: the CLI `--sync` flag accepts a comma-separated "
            "string, whereas the HTTP API requires `list[str]`."
        ),
        examples=[["telegram"]],
    )
    # Explicit reply attachment (never auto-echoes inbound event attachments).
    file_path: str | None = None
    file_id: str | None = None
    file_type: str | None = None


class SyncResultModel(BaseModel):
    target: str
    ok: bool
    detail: str = ""
    message_id: str | None = None


class EventCompleteResponse(BaseModel):
    event_id: str
    status: str
    summary: str
    completed_at: str
    already_completed: bool
    sync_results: list[SyncResultModel] = Field(default_factory=list)


class ChannelStatusItem(BaseModel):
    name: str
    enabled: bool
    running: bool
    secret_key: str | None = None
    secret_keys: list[str] = Field(default_factory=list)
    has_secret: bool = False


class ChannelListResponse(BaseModel):
    channels: list[ChannelStatusItem]


class ChannelMutationResponse(BaseModel):
    name: str
    enabled: bool
    running: bool


class SecretPutRequest(BaseModel):
    value: str = ""
    secret_name: str | None = None


class SecretPutResponse(BaseModel):
    key: str
    stored: bool = True


class SecretStatusResponse(BaseModel):
    key: str
    exists: bool
