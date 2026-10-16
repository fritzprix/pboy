"""Telegram message media helpers (pure extraction; no Bot API I/O)."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from pboy.models.cloudevent import EventAttachment, TelegramFileType

# Trusted channel downloads only — never read from inject/HTTP payloads.
_local_files_ctx: ContextVar[dict[str, str] | None] = ContextVar(
    "pboy_telegram_local_files", default=None
)


def set_local_files(mapping: dict[str, str] | None) -> Token[dict[str, str] | None]:
    """Bind file_id → local_path for the current task (channel emit path)."""
    return _local_files_ctx.set(mapping)


def reset_local_files(token: Token[dict[str, str] | None]) -> None:
    """Restore the previous local-files context."""
    _local_files_ctx.reset(token)


def get_local_files() -> dict[str, str] | None:
    """Return the channel-bound local file map, if any."""
    return _local_files_ctx.get()


def extract_telegram_attachments(message: dict[str, Any]) -> list[EventAttachment]:
    """Build attachment metadata from a Telegram ``message`` object (no I/O)."""
    photo = message.get("photo")
    if isinstance(photo, list) and photo:
        best = _largest_photo(photo)
        if best is not None:
            file_id = _as_str(best.get("file_id"))
            if file_id:
                return [
                    EventAttachment(
                        file_id=file_id,
                        file_type="photo",
                        file_unique_id=_as_str(best.get("file_unique_id")),
                        file_size=_as_int(best.get("file_size")),
                    )
                ]

    media: list[tuple[str, TelegramFileType]] = [
        ("document", "document"),
        ("audio", "audio"),
        ("video", "video"),
    ]
    for key, file_type in media:
        obj = message.get(key)
        if not isinstance(obj, dict):
            continue
        file_id = _as_str(obj.get("file_id"))
        if not file_id:
            continue
        file_name = _as_str(obj.get("file_name"))
        if file_type == "audio" and not file_name:
            file_name = _as_str(obj.get("title"))
        return [
            EventAttachment(
                file_id=file_id,
                file_type=file_type,
                file_unique_id=_as_str(obj.get("file_unique_id")),
                file_name=file_name,
                mime_type=_as_str(obj.get("mime_type")),
                file_size=_as_int(obj.get("file_size")),
            )
        ]
    return []


def merge_local_paths(
    attachments: list[EventAttachment],
    local_files: dict[str, str] | None,
) -> list[EventAttachment]:
    """Attach ``local_path`` values from a file_id → path map."""
    if not local_files:
        return attachments
    out: list[EventAttachment] = []
    for att in attachments:
        path = local_files.get(att.file_id)
        if path:
            out.append(att.model_copy(update={"local_path": path}))
        else:
            out.append(att)
    return out


def _largest_photo(photos: list[Any]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1
    for item in photos:
        if not isinstance(item, dict):
            continue
        size = _as_int(item.get("file_size")) or 0
        w = _as_int(item.get("width")) or 0
        h = _as_int(item.get("height")) or 0
        score = size if size > 0 else w * h
        if score >= best_score and item.get("file_id"):
            best = item
            best_score = score
    return best


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


__all__ = [
    "extract_telegram_attachments",
    "get_local_files",
    "merge_local_paths",
    "reset_local_files",
    "set_local_files",
]
