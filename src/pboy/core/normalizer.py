"""Event normalizer: transforms platform-specific payloads into CloudEvents v1.0."""

from __future__ import annotations

from typing import Any

from pboy.core.telegram_media import (
    extract_telegram_attachments,
    get_local_files,
    merge_local_paths,
)
from pboy.models.cloudevent import (
    TOPIC_TO_TYPE,
    CloudEvent,
    EventActor,
    EventData,
    current_iso_time,
    generate_event_id,
)


def _as_dict(value: Any) -> dict[str, Any]:
    """Safely cast value to dict if applicable, else return empty dict."""
    return value if isinstance(value, dict) else {}


def _as_str(value: Any) -> str | None:
    """Safely return string representation of non-None value, else None."""
    if value is None:
        return None
    s = str(value)
    return s if s else None


class EventNormalizer:
    """Normalizes raw inbound payloads to CloudEvents v1.0 compliant format."""

    def normalize(self, raw_payload: dict[str, Any], source: str) -> CloudEvent:
        """Convert a platform-specific raw payload into a unified CloudEvent."""
        safe_source = source if isinstance(source, str) else "generic"
        source_lower = safe_source.lower()
        payload = _as_dict(raw_payload)

        if source_lower == "jira":
            return self._normalize_jira(payload)
        elif source_lower == "confluence":
            return self._normalize_confluence(payload)
        elif source_lower == "slack":
            return self._normalize_slack(payload)
        elif source_lower == "telegram":
            return self._normalize_telegram(payload)
        else:
            return self._normalize_generic(payload, source_lower)

    def _normalize_jira(self, payload: dict[str, Any]) -> CloudEvent:
        safe_payload = _as_dict(payload)
        webhook_event = safe_payload.get("webhookEvent")
        webhook_event_str = webhook_event if isinstance(webhook_event, str) else "jira:issue_created"
        if "created" in webhook_event_str:
            event_type = TOPIC_TO_TYPE.get("jira:issue.created", "com.atlassian.jira.issue.created")
        else:
            event_type = TOPIC_TO_TYPE.get("jira:issue.updated", "com.atlassian.jira.issue.updated")

        issue = _as_dict(safe_payload.get("issue"))
        fields = _as_dict(issue.get("fields"))
        creator = _as_dict(fields.get("creator") or safe_payload.get("user"))
        priority_obj = _as_dict(fields.get("priority"))
        project_obj = _as_dict(fields.get("project"))

        raw_labels = fields.get("labels")
        labels: list[str] = [str(lbl) for lbl in raw_labels] if isinstance(raw_labels, list) else []

        actor_id = creator.get("accountId") or creator.get("name")
        actor_display = creator.get("displayName")

        data = EventData(
            entity_key=_as_str(issue.get("key")),
            channel_id=_as_str(project_obj.get("key")),
            actor=EventActor(
                id=str(actor_id) if actor_id is not None else None,
                display_name=str(actor_display) if actor_display is not None else None,
            ),
            summary=_as_str(fields.get("summary")),
            description=_as_str(fields.get("description")),
            priority=_as_str(priority_obj.get("name")),
            labels=labels,
            extra={"issue_id": issue.get("id")} if "id" in issue and issue.get("id") is not None else {},
        )

        return CloudEvent(
            id=generate_event_id(),
            source="jira",
            type=event_type,
            time=current_iso_time(),
            data=data.as_cloud_data(),
        )

    def _normalize_confluence(self, payload: dict[str, Any]) -> CloudEvent:
        safe_payload = _as_dict(payload)
        event_type = TOPIC_TO_TYPE.get("confluence:page.updated", "com.atlassian.confluence.page.updated")
        page = _as_dict(safe_payload.get("page") or safe_payload.get("content"))
        space = _as_dict(safe_payload.get("space"))
        actor_data = _as_dict(safe_payload.get("user"))
        body = _as_dict(page.get("body"))
        storage = _as_dict(body.get("storage"))
        version = _as_dict(page.get("version"))

        actor_id = actor_data.get("accountId") or actor_data.get("name")
        actor_display = actor_data.get("displayName")

        data = EventData(
            entity_key=str(page.get("id", "")),
            channel_id=_as_str(space.get("key")),
            actor=EventActor(
                id=str(actor_id) if actor_id is not None else None,
                display_name=str(actor_display) if actor_display is not None else None,
            ),
            summary=_as_str(page.get("title")),
            description=_as_str(storage.get("value")),
            extra={"version": version.get("number")} if "number" in version and version.get("number") is not None else {},
        )

        return CloudEvent(
            id=generate_event_id(),
            source="confluence",
            type=event_type,
            time=current_iso_time(),
            data=data.as_cloud_data(),
        )

    def _normalize_slack(self, payload: dict[str, Any]) -> CloudEvent:
        safe_payload = _as_dict(payload)
        raw_event = safe_payload.get("event")
        event = _as_dict(raw_event) if isinstance(raw_event, dict) else safe_payload
        event_type = TOPIC_TO_TYPE.get("slack:message", "com.slack.message.received")

        client_msg_id = event.get("client_msg_id") or event.get("ts")
        user_id = event.get("user")
        text = _as_str(event.get("text"))

        data = EventData(
            entity_key=str(client_msg_id) if client_msg_id is not None else None,
            channel_id=_as_str(event.get("channel")),
            thread_id=_as_str(event.get("thread_ts")),
            actor=EventActor(id=str(user_id) if user_id is not None else None),
            summary=text,
            description=text,
        )

        return CloudEvent(
            id=generate_event_id(),
            source="slack",
            type=event_type,
            time=current_iso_time(),
            data=data.as_cloud_data(),
        )

    def _normalize_telegram(self, payload: dict[str, Any]) -> CloudEvent:
        safe_payload = _as_dict(payload)
        raw_message = safe_payload.get("message")
        message = _as_dict(raw_message) if isinstance(raw_message, dict) else safe_payload
        event_type = TOPIC_TO_TYPE.get("telegram:message", "com.telegram.message.received")
        sender = _as_dict(message.get("from"))
        chat = _as_dict(message.get("chat"))

        sender_id = sender.get("id")
        sender_name = sender.get("username") or sender.get("first_name")
        text = _as_str(message.get("text"))
        caption = _as_str(message.get("caption"))
        summary = text or caption

        attachments = extract_telegram_attachments(message)
        # Only channel-bound context (never inject/HTTP payload keys).
        attachments = merge_local_paths(attachments, get_local_files())

        data = EventData(
            entity_key=str(message.get("message_id", "")),
            channel_id=str(chat.get("id", "")),
            actor=EventActor(
                id=str(sender_id) if sender_id is not None else "",
                display_name=str(sender_name) if sender_name is not None else None,
            ),
            summary=summary,
            description=summary,
            attachments=attachments,
        )

        return CloudEvent(
            id=generate_event_id(),
            source="telegram",
            type=event_type,
            time=current_iso_time(),
            data=data.as_cloud_data(),
        )

    def _normalize_generic(self, payload: dict[str, Any], source: str) -> CloudEvent:
        safe_payload = _as_dict(payload)
        event_type = safe_payload.get("type")
        if not isinstance(event_type, str) or not event_type:
            event_type = f"com.{source}.event"
        data = safe_payload.get("data")
        if not isinstance(data, dict):
            data = safe_payload
        return CloudEvent(
            id=generate_event_id(),
            source=source,
            type=event_type,
            time=current_iso_time(),
            data=data,
        )
