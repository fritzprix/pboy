"""CloudEvents v1.0 domain model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# Topic <-> CloudEvents type mappings
TOPIC_TO_TYPE: dict[str, str] = {
    "jira:issue.created": "com.atlassian.jira.issue.created",
    "jira:issue.updated": "com.atlassian.jira.issue.updated",
    "confluence:page.updated": "com.atlassian.confluence.page.updated",
    "telegram:message": "com.telegram.message.received",
    "slack:message": "com.slack.message.received",
}

TYPE_TO_TOPIC: dict[str, str] = {v: k for k, v in TOPIC_TO_TYPE.items()}


def generate_event_id() -> str:
    """Generate event id in evt_YYYYMMDD_<12hex> format."""
    now_str = datetime.now(UTC).strftime("%Y%m%d")
    short_hash = uuid.uuid4().hex[:12]
    return f"evt_{now_str}_{short_hash}"


def current_iso_time() -> str:
    """Current UTC time in ISO 8601 format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventActor(BaseModel):
    """Actor identity triggering the event."""

    id: str | None = None
    display_name: str | None = None


TelegramFileType = Literal["photo", "document", "audio", "video"]


class EventAttachment(BaseModel):
    """Inbound file attached to an event (e.g. Telegram photo/document)."""

    file_id: str
    file_type: TelegramFileType
    file_unique_id: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    local_path: str | None = None


class EventData(BaseModel):
    """Normalized payload data inside CloudEvent."""

    entity_key: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None
    actor: EventActor | None = None
    summary: str | None = None
    description: str | None = None
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    raw_ref: str | None = None
    attachments: list[EventAttachment] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    def as_cloud_data(self) -> dict[str, Any]:
        """Serialize for CloudEvent.data.

        Omits empty ``attachments`` only (Telegram media). Keeps empty ``labels`` /
        ``extra`` so existing hooks that read ``data["labels"]`` keep working.
        """
        dumped = self.model_dump(exclude_none=True)
        if not self.attachments:
            dumped.pop("attachments", None)
        return dumped


class CloudEvent(BaseModel):
    """CloudEvents v1.0 compliant event model."""

    specversion: str = "1.0"
    id: str = Field(default_factory=generate_event_id)
    source: str
    type: str
    time: str = Field(default_factory=current_iso_time)
    datacontenttype: str = "application/json"
    data: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "TOPIC_TO_TYPE",
    "TYPE_TO_TOPIC",
    "CloudEvent",
    "EventActor",
    "EventAttachment",
    "EventData",
    "TelegramFileType",
    "current_iso_time",
    "generate_event_id",
]
