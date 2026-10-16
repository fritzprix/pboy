"""Explicit outbound reply file for ``event complete --sync``."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, model_validator

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

ReplyFileType = Literal["photo", "document", "audio", "video"]

REPLY_FILE_TYPES: dict[str, ReplyFileType] = {
    "photo": "photo",
    "document": "document",
    "audio": "audio",
    "video": "video",
}

# Bot API upload practical limit for local files.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# Caption limit for sendPhoto/sendDocument/etc.
TELEGRAM_CAPTION_MAX = 1024


def parse_reply_file_type(raw: str | None) -> ReplyFileType:
    """Map a user/API string to ``ReplyFileType`` (default ``document``)."""
    key = (raw or "document").strip().lower()
    mapped = REPLY_FILE_TYPES.get(key)
    if mapped is None:
        raise ValueError("file_type must be one of: photo, document, audio, video")
    return mapped


class ReplyFile(BaseModel):
    """Operator-supplied file to send on completion sync (not inbound echo)."""

    file_path: str | None = None
    file_id: str | None = None
    file_type: ReplyFileType = "document"

    @model_validator(mode="after")
    def _require_source(self) -> Self:
        path = (self.file_path or "").strip() or None
        file_id = (self.file_id or "").strip() or None
        if not path and not file_id:
            raise ValueError("ReplyFile requires file_path or file_id")
        if path and file_id:
            raise ValueError("ReplyFile accepts only one of file_path or file_id")
        self.file_path = path
        self.file_id = file_id
        return self


def resolve_reply_file_path(raw: str, *, files_root: Path) -> Path:
    """Resolve a reply ``file_path`` under ``files_root`` only.

    Absolute paths outside the root, cwd fallbacks, and path traversal are rejected.
    Relative paths are resolved against ``files_root``.
    """
    root = files_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = Path(raw).expanduser()
    path = (
        candidate.resolve()
        if candidate.is_absolute()
        else (root / candidate).resolve()
    )
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PermissionError(
            f"Reply file_path must be under {root} (got {raw!r})"
        ) from exc
    if not path.is_file():
        raise FileNotFoundError(f"Reply file not found under {root}: {raw}")
    return path


def truncate_telegram_caption(text: str, *, limit: int = TELEGRAM_CAPTION_MAX) -> str:
    """Truncate caption to Telegram's limit (keeps full string when short enough)."""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def stage_reply_file_for_daemon(src: Path, *, files_root: Path) -> Path:
    """Ensure ``src`` is readable under ``files_root`` for daemon outbound.

    Files already under the root are returned as-is. Otherwise the file is copied
    into ``files_root/outbound/`` so the daemon path guard accepts it.
    """
    root = files_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    resolved = src.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Reply file not found: {src}")
    try:
        resolved.relative_to(root)
        return resolved
    except ValueError:
        pass
    dest_dir = root / "outbound"
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _SAFE_NAME.sub("_", resolved.name).strip("._") or "upload.bin"
    # Unique prefix so two same-named sources do not overwrite each other.
    dest = dest_dir / f"{uuid.uuid4().hex[:12]}_{name[:160]}"
    shutil.copy2(resolved, dest)
    return dest.resolve()


__all__ = [
    "MAX_UPLOAD_BYTES",
    "REPLY_FILE_TYPES",
    "TELEGRAM_CAPTION_MAX",
    "ReplyFile",
    "ReplyFileType",
    "parse_reply_file_type",
    "resolve_reply_file_path",
    "stage_reply_file_for_daemon",
    "truncate_telegram_caption",
]
