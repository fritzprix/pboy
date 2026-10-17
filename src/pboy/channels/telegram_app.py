"""Telegram MTProto App API — user-account rail for dialogs / history / export.

**Two rails (do not conflate):**

- **Bot token** (`telegram.bot_token`) — wake-up conversation via Bot API
  (`pboy.channels.telegram`). Not used here for “all dialogs”.
- **App API** (this module) — `telegram.api_id` + `telegram.api_hash` plus a
  one-time **account connect** that stores opaque ``telegram.app_session``.
  Operator-directed list/messages/download/export over the user account's chats.

Credentials load **only** from the encrypted SQLite store (`/ui/settings`).
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pboy.core.config import Settings, get_settings
from pboy.store.base import StoreBase

TELEGRAM_API_ID_SECRET_KEY = "telegram.api_id"
TELEGRAM_API_HASH_SECRET_KEY = "telegram.api_hash"
TELEGRAM_APP_SESSION_SECRET_KEY = "telegram.app_session"

ExportFormat = Literal["jsonl", "md", "csv"]
LoginStep = Literal["idle", "code", "password"]


@dataclass(slots=True)
class TelegramAppCredentials:
    """Resolved MTProto App API credentials from encrypted store."""

    api_id: int
    api_hash: str
    app_session: str | None = None


async def resolve_telegram_app_credentials(
    store: StoreBase,
    *,
    require_auth: bool = True,
) -> TelegramAppCredentials:
    """Load ``api_id`` / ``api_hash`` and optional ``app_session`` from store.

    Analysis commands require a completed **account connect** (``app_session``).
    Bot token is intentionally not a substitute — it cannot list the user's full
    dialog set.
    """
    raw_id = await store.get_secret(TELEGRAM_API_ID_SECRET_KEY)
    raw_hash = await store.get_secret(TELEGRAM_API_HASH_SECRET_KEY)
    if not raw_id or not raw_id.strip():
        raise RuntimeError(
            "Telegram App API ID (`telegram.api_id`) is not configured. "
            "Set it in /ui/settings first."
        )
    if not raw_hash or not raw_hash.strip():
        raise RuntimeError(
            "Telegram App API Hash (`telegram.api_hash`) is not configured. "
            "Set it in /ui/settings first."
        )
    try:
        api_id = int(raw_id.strip())
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid `telegram.api_id` in store (must be an integer): {raw_id!r}"
        ) from exc

    raw_session = await store.get_secret(TELEGRAM_APP_SESSION_SECRET_KEY)
    app_session = raw_session.strip() if raw_session and raw_session.strip() else None

    if require_auth and not app_session:
        raise RuntimeError(
            "Telegram account is not connected. Complete “계정 연결” in /ui/settings "
            "(phone + code) so dialogs/messages/export can use your user account."
        )

    return TelegramAppCredentials(
        api_id=api_id,
        api_hash=raw_hash.strip(),
        app_session=app_session,
    )


@dataclass(slots=True)
class _PendingLogin:
    """In-memory UI login between ``request_login_code`` and a completed sign-in."""

    phone: str
    phone_code_hash: str
    temp_session: str
    password_needed: bool = False


def mask_phone(phone: str) -> str:
    """Mask the middle of a phone number for display (``+8210****5678``)."""
    text = phone.strip()
    if len(text) <= 7:
        return text[:2] + "*" * max(len(text) - 2, 0)
    return text[:5] + "*" * (len(text) - 9) + text[-4:]


def _parse_chat_target(chat: str | int) -> str | int:
    """Convert numeric chat ID strings (including `-100...`) to `int` for Telethon."""
    if isinstance(chat, int):
        return chat
    text = chat.strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return text


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _format_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_chat_segment(chat: str | int) -> str:
    """Sanitize chat id for use under ``data_dir/files/telegram_app/``."""
    text = str(chat).strip()
    if not text or text in {".", ".."} or "\\" in text or "/" in text:
        raise ValueError(
            f"Invalid chat path segment {chat!r}: use a numeric id or @username "
            "(no path separators or '..')."
        )
    cleaned = text.replace("@", "")
    if not cleaned or ".." in cleaned or cleaned.startswith("."):
        raise ValueError(f"Invalid chat path segment {chat!r}")
    return cleaned


def _map_telethon_error(exc: BaseException) -> RuntimeError | None:
    """Map Telethon RPC errors to RuntimeError for UI/CLI (avoid HTTP 500 / traceback)."""
    try:
        from telethon.errors import RPCError  # type: ignore[import-untyped]
    except ImportError:
        return None
    if isinstance(exc, RPCError):
        return RuntimeError(f"Telegram API error: {exc}")
    return None


def _raise_mapped_telethon(exc: BaseException) -> None:
    mapped = _map_telethon_error(exc)
    if mapped is not None:
        raise mapped from exc


def _normalize_msg_date(msg_date: datetime) -> datetime:
    if msg_date.tzinfo is None:
        return msg_date.replace(tzinfo=UTC)
    return msg_date


def _message_in_range(
    msg_date: Any,
    *,
    since_dt: datetime | None,
    until_dt: datetime | None,
) -> bool:
    if not isinstance(msg_date, datetime):
        return True
    cmp_date = _normalize_msg_date(msg_date)
    if since_dt is not None and cmp_date < since_dt:
        return False
    return not (until_dt is not None and cmp_date > until_dt)


def _build_get_messages_kwargs(
    *,
    limit: int,
    offset_id: int,
    min_id: int,
    max_id: int,
    since_dt: datetime | None,
    until_dt: datetime | None,
    search: str | None,
    from_user: str | None,
    reverse: bool,
) -> tuple[dict[str, Any], datetime | None, datetime | None]:
    """Build Telethon ``get_messages`` kwargs and client-side date filters.

    Telethon's ``offset_date`` meaning flips with ``reverse``: without reverse it is
    an upper bound (older than); with reverse it is a lower bound (newer than).
    We therefore pass the bound Telethon can express and filter the other side
    in-process.
    """
    kwargs: dict[str, Any] = {
        "limit": limit,
        "offset_id": offset_id,
        "min_id": min_id,
        "max_id": max_id,
        "reverse": reverse,
    }
    client_since = since_dt
    client_until = until_dt
    if reverse:
        if since_dt is not None:
            kwargs["offset_date"] = since_dt
            client_since = None
    elif until_dt is not None:
        kwargs["offset_date"] = until_dt
        client_until = None
    if search and search.strip():
        kwargs["search"] = search.strip()
    if from_user and from_user.strip():
        kwargs["from_user"] = _parse_chat_target(from_user)
    return kwargs, client_since, client_until


def _next_offset_id(items: list[dict[str, Any]], *, reverse: bool) -> int | None:
    ids = [int(m["id"]) for m in items if isinstance(m.get("id"), int)]
    if not ids:
        return None
    return max(ids) if reverse else min(ids)


def _extract_sender_dict(msg: Any) -> dict[str, Any] | None:
    sender = getattr(msg, "sender", None)
    sender_id = getattr(msg, "sender_id", None)
    if sender is None and sender_id is None:
        return None
    first = getattr(sender, "first_name", None) or ""
    last = getattr(sender, "last_name", None) or ""
    title = getattr(sender, "title", None) or ""
    display = " ".join(p for p in (first, last) if p).strip() or title or None
    return {
        "id": sender_id if sender_id is not None else getattr(sender, "id", None),
        "username": getattr(sender, "username", None),
        "display_name": display,
    }


def _extract_media_dict(msg: Any) -> dict[str, Any] | None:
    media = getattr(msg, "media", None)
    if media is None:
        return None
    file_obj = getattr(msg, "file", None)
    media_type = type(media).__name__
    return {
        "type": media_type,
        "file_name": getattr(file_obj, "name", None) if file_obj else None,
        "mime_type": getattr(file_obj, "mime_type", None) if file_obj else None,
        "size": getattr(file_obj, "size", None) if file_obj else None,
    }


def serialize_message(msg: Any, *, local_path: str | None = None) -> dict[str, Any]:
    """Convert a Telethon `Message` object (or duck-typed test double) into a dict."""
    reply_to = getattr(msg, "reply_to", None)
    reply_to_msg_id = getattr(reply_to, "reply_to_msg_id", None)
    if reply_to_msg_id is None:
        reply_to_msg_id = getattr(msg, "reply_to_msg_id", None)

    media_info = _extract_media_dict(msg)
    if media_info is not None and local_path is not None:
        media_info["local_path"] = local_path

    raw_date = getattr(msg, "date", None)
    date_str = _format_iso(raw_date) if isinstance(raw_date, datetime) else (
        str(raw_date) if raw_date is not None else None
    )

    return {
        "id": getattr(msg, "id", None),
        "chat_id": getattr(msg, "chat_id", None),
        "date": date_str,
        "text": getattr(msg, "text", None) or getattr(msg, "message", None) or "",
        "sender": _extract_sender_dict(msg),
        "reply_to_msg_id": reply_to_msg_id,
        "views": getattr(msg, "views", None),
        "forwards": getattr(msg, "forwards", None),
        "media": media_info,
    }


def serialize_dialog(dialog: Any) -> dict[str, Any]:
    """Convert a Telethon `Dialog` object (or duck-typed test double) into a dict."""
    entity = getattr(dialog, "entity", None)
    is_channel = bool(getattr(dialog, "is_channel", False))
    is_group = bool(getattr(dialog, "is_group", False))
    is_user = bool(getattr(dialog, "is_user", False))
    if is_channel and not is_group:
        chat_type = "channel"
    elif is_group:
        chat_type = "group"
    elif is_user:
        chat_type = "user"
    else:
        chat_type = "chat"

    raw_date = getattr(dialog, "date", None)
    date_str = _format_iso(raw_date) if isinstance(raw_date, datetime) else (
        str(raw_date) if raw_date is not None else None
    )

    return {
        "id": getattr(dialog, "id", None),
        "title": getattr(dialog, "title", None) or getattr(dialog, "name", None) or "",
        "username": getattr(entity, "username", None) if entity is not None else None,
        "type": chat_type,
        "unread_count": getattr(dialog, "unread_count", 0),
        "last_message_date": date_str,
    }


def format_messages_export(
    messages: list[dict[str, Any]],
    *,
    fmt: ExportFormat,
    chat_label: str = "",
) -> str:
    """Format serialized message dicts into `jsonl`, `md`, or `csv` text."""
    if fmt == "jsonl":
        return "\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + (
            "\n" if messages else ""
        )

    if fmt == "md":
        header = f"# Telegram Chat Export{f' — {chat_label}' if chat_label else ''}\n\n"
        header += f"- **Total messages**: {len(messages)}\n\n---\n\n"
        blocks: list[str] = [header]
        for m in messages:
            sender = m.get("sender") or {}
            author = (
                sender.get("display_name")
                or sender.get("username")
                or str(sender.get("id") or "unknown")
            )
            if sender.get("username") and sender.get("display_name"):
                author = f"{sender['display_name']} (@{sender['username']})"
            date_str = m.get("date") or ""
            msg_id = m.get("id")
            reply_id = m.get("reply_to_msg_id")
            reply_tag = f" (reply to #{reply_id})" if reply_id else ""
            text = (m.get("text") or "").strip() or "_[no text]_"
            media = m.get("media")
            media_line = ""
            if isinstance(media, dict):
                parts = [f"type={media.get('type')}"]
                if media.get("file_name"):
                    parts.append(f"name={media['file_name']}")
                if media.get("size") is not None:
                    parts.append(f"size={media['size']}")
                if media.get("local_path"):
                    parts.append(f"path={media['local_path']}")
                media_line = f"\n\n> Attachment: {', '.join(parts)}"
            blocks.append(
                f"### [#{msg_id}] {author} · `{date_str}`{reply_tag}\n\n{text}{media_line}\n"
            )
        return "\n".join(blocks)

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(
            [
                "id",
                "chat_id",
                "date",
                "sender_id",
                "sender_username",
                "sender_name",
                "reply_to_msg_id",
                "text",
                "media_type",
                "file_name",
                "file_size",
                "local_path",
            ]
        )
        for m in messages:
            sender = m.get("sender") or {}
            media = m.get("media") or {}
            writer.writerow(
                [
                    m.get("id") if m.get("id") is not None else "",
                    m.get("chat_id") if m.get("chat_id") is not None else "",
                    m.get("date") or "",
                    sender.get("id") if sender.get("id") is not None else "",
                    sender.get("username") or "",
                    sender.get("display_name") or "",
                    m.get("reply_to_msg_id") if m.get("reply_to_msg_id") is not None else "",
                    m.get("text") or "",
                    media.get("type") or "",
                    media.get("file_name") or "",
                    media.get("size") if media.get("size") is not None else "",
                    media.get("local_path") or "",
                ]
            )
        return buf.getvalue()

    raise ValueError(f"Unsupported export format: {fmt!r}")


class TelegramAppService:
    """High-level MTProto operations backed by encrypted Store secrets."""

    def __init__(
        self,
        store: StoreBase,
        settings: Settings | None = None,
        *,
        client_factory: Any | None = None,
    ) -> None:
        self._store = store
        self._settings = settings or get_settings()
        self._client_factory = client_factory
        self._pending_login: _PendingLogin | None = None

    @property
    def login_pending(self) -> bool:
        """True after ``request_login_code`` until sign-in, cancel, or disconnect."""
        return self._pending_login is not None

    @property
    def login_step(self) -> LoginStep:
        """Where the interactive login stands: ``idle`` → ``code`` → ``password`` (2FA)."""
        if self._pending_login is None:
            return "idle"
        return "password" if self._pending_login.password_needed else "code"

    @property
    def pending_phone_masked(self) -> str | None:
        """Masked phone the pending code was sent to, for display."""
        if self._pending_login is None:
            return None
        return mask_phone(self._pending_login.phone)

    def cancel_login(self) -> None:
        """Drop a pending login (e.g. wrong phone number or changed App API keys)."""
        self._pending_login = None

    async def _open_client(
        self,
        creds: TelegramAppCredentials,
        *,
        session_override: str | None = None,
        authenticate: bool = True,
    ) -> Any:
        if self._client_factory is not None:
            return await self._client_factory(creds, session_override, authenticate)

        from telethon import TelegramClient  # type: ignore[import-untyped]
        from telethon.sessions import StringSession  # type: ignore[import-untyped]

        session_str = session_override if session_override is not None else (creds.app_session or "")
        client = TelegramClient(StringSession(session_str), creds.api_id, creds.api_hash)
        await client.connect()
        if authenticate and not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(
                "Telegram account session is not authorized. "
                "Complete “계정 연결” in /ui/settings (phone + code)."
            )
        return client

    async def request_login_code(self, phone: str) -> dict[str, Any]:
        """Send an OTP login code to `phone` and keep the pending hash for `verify_login_code`."""
        cleaned_phone = phone.strip()
        if not cleaned_phone:
            raise ValueError("전화번호를 입력해 주세요 (예: +821012345678).")
        creds = await resolve_telegram_app_credentials(self._store, require_auth=False)
        client = await self._open_client(creds, session_override="", authenticate=False)
        try:
            try:
                sent = await client.send_code_request(cleaned_phone)
            except Exception as exc:
                _raise_mapped_telethon(exc)
                raise
            temp_session = client.session.save()
            self._pending_login = _PendingLogin(
                phone=cleaned_phone,
                phone_code_hash=sent.phone_code_hash,
                temp_session=temp_session,
            )
            return {
                "phone": cleaned_phone,
                "code_sent": True,
            }
        finally:
            await client.disconnect()

    async def verify_login_code(
        self,
        code: str,
        *,
        password: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Sign in with the OTP code and persist ``telegram.app_session``.

        When the account has 2FA and no ``password`` is given, the login moves to the
        ``password`` step and ``{"authenticated": False, "password_required": True}`` is
        returned; finish it with ``verify_login_password``.
        """
        cleaned_code = code.strip()
        if not cleaned_code:
            raise ValueError("인증 코드를 입력해 주세요.")
        pending = self._pending_login
        if pending is None:
            raise ValueError("먼저 전화번호로 인증 코드를 요청해 주세요.")
        target_phone = phone.strip() if phone and phone.strip() else pending.phone

        creds = await resolve_telegram_app_credentials(self._store, require_auth=False)
        client = await self._open_client(
            creds, session_override=pending.temp_session, authenticate=False
        )
        try:
            try:
                await client.sign_in(
                    phone=target_phone,
                    code=cleaned_code,
                    phone_code_hash=pending.phone_code_hash,
                )
            except Exception as exc:
                # Handle 2FA SessionPasswordNeededError by class name (works with mocks too)
                if exc.__class__.__name__ != "SessionPasswordNeededError":
                    _raise_mapped_telethon(exc)
                    raise ValueError(f"Telegram 로그인 실패: {exc}") from exc
                if not password or not password.strip():
                    pending.temp_session = str(client.session.save())
                    pending.password_needed = True
                    return {"authenticated": False, "password_required": True}
                await self._sign_in_password(client, password)
            return await self._finish_login(client)
        finally:
            await client.disconnect()

    async def verify_login_password(self, password: str) -> dict[str, Any]:
        """Finish a 2FA login after ``verify_login_code`` reported ``password_required``."""
        if not password.strip():
            raise ValueError("2단계 인증(2FA) 비밀번호를 입력해 주세요.")
        pending = self._pending_login
        if pending is None or not pending.password_needed:
            raise ValueError("먼저 인증 코드를 확인해 주세요.")
        creds = await resolve_telegram_app_credentials(self._store, require_auth=False)
        client = await self._open_client(
            creds, session_override=pending.temp_session, authenticate=False
        )
        try:
            await self._sign_in_password(client, password)
            return await self._finish_login(client)
        finally:
            await client.disconnect()

    async def _sign_in_password(self, client: Any, password: str) -> None:
        try:
            await client.sign_in(password=password.strip())
        except Exception as exc:
            _raise_mapped_telethon(exc)
            raise ValueError(f"Telegram 로그인 실패: {exc}") from exc

    async def _finish_login(self, client: Any) -> dict[str, Any]:
        session_str = str(client.session.save())
        await self._store.put_secret(TELEGRAM_APP_SESSION_SECRET_KEY, session_str)
        self._pending_login = None
        me = await client.get_me()
        return {
            "authenticated": True,
            "user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
        }

    async def disconnect_account(self) -> None:
        """Clear stored ``telegram.app_session`` (account disconnect)."""
        await self._store.put_secret(TELEGRAM_APP_SESSION_SECRET_KEY, "")
        self._pending_login = None

    async def list_chats(
        self,
        *,
        limit: int = 50,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """List channels, supergroups, groups, and direct chats visible to the session."""
        creds = await resolve_telegram_app_credentials(self._store, require_auth=True)
        client = await self._open_client(creds)
        try:
            try:
                dialogs = await client.get_dialogs(limit=limit)
            except Exception as exc:
                _raise_mapped_telethon(exc)
                raise
            rows = [serialize_dialog(d) for d in dialogs]
            if search and search.strip():
                needle = search.strip().lower()
                rows = [
                    r
                    for r in rows
                    if needle in str(r.get("title") or "").lower()
                    or needle in str(r.get("username") or "").lower()
                    or needle in str(r.get("id") or "").lower()
                ]
            return rows
        finally:
            await client.disconnect()

    async def list_messages(
        self,
        chat: str | int,
        *,
        limit: int = 50,
        offset_id: int = 0,
        min_id: int = 0,
        max_id: int = 0,
        since: str | None = None,
        until: str | None = None,
        search: str | None = None,
        from_user: str | None = None,
        reverse: bool = False,
    ) -> dict[str, Any]:
        """Fetch messages from `chat` with pagination metadata (`next_offset_id`, `has_more`)."""
        creds = await resolve_telegram_app_credentials(self._store, require_auth=True)
        client = await self._open_client(creds)
        target = _parse_chat_target(chat)
        since_dt = _parse_iso_datetime(since)
        until_dt = _parse_iso_datetime(until)
        kwargs, client_since, client_until = _build_get_messages_kwargs(
            limit=limit,
            offset_id=offset_id,
            min_id=min_id,
            max_id=max_id,
            since_dt=since_dt,
            until_dt=until_dt,
            search=search,
            from_user=from_user,
            reverse=reverse,
        )
        try:
            try:
                raw_messages = await client.get_messages(target, **kwargs)
            except Exception as exc:
                _raise_mapped_telethon(exc)
                raise
            items: list[dict[str, Any]] = []
            for msg in raw_messages:
                if not _message_in_range(
                    getattr(msg, "date", None),
                    since_dt=client_since,
                    until_dt=client_until,
                ):
                    continue
                items.append(serialize_message(msg))

            return {
                "chat": str(chat),
                "count": len(items),
                "limit": limit,
                "offset_id": offset_id,
                "next_offset_id": _next_offset_id(items, reverse=reverse),
                "has_more": len(raw_messages) >= limit,
                "messages": items,
            }
        finally:
            await client.disconnect()

    async def download_message_media(
        self,
        chat: str | int,
        message_id: int,
        *,
        out_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Download attachment from `message_id` in `chat` to local disk."""
        creds = await resolve_telegram_app_credentials(self._store, require_auth=True)
        client = await self._open_client(creds)
        target = _parse_chat_target(chat)
        try:
            try:
                fetched = await client.get_messages(target, ids=message_id)
            except Exception as exc:
                _raise_mapped_telethon(exc)
                raise
            msg = fetched[0] if isinstance(fetched, list) and fetched else fetched
            if msg is None:
                raise LookupError(f"Message {message_id} not found in chat {chat!r}")
            if getattr(msg, "media", None) is None:
                raise ValueError(f"Message {message_id} in chat {chat!r} has no media attachment")

            if out_path is not None:
                dest = Path(out_path).expanduser().resolve()
                if dest.exists() and dest.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
            else:
                safe_chat = _safe_chat_segment(chat)
                dest = (
                    Path(self._settings.data_dir)
                    / "files"
                    / "telegram_app"
                    / safe_chat
                    / str(message_id)
                )
                dest.mkdir(parents=True, exist_ok=True)

            try:
                saved = await client.download_media(msg, file=str(dest))
            except Exception as exc:
                _raise_mapped_telethon(exc)
                raise
            if not saved:
                raise RuntimeError(
                    f"Failed to download media for message {message_id} in chat {chat!r}"
                )
            resolved_path = str(Path(saved).resolve())
            return {
                "chat": str(chat),
                "message_id": message_id,
                "file_path": resolved_path,
                "media": _extract_media_dict(msg),
            }
        finally:
            await client.disconnect()

    async def export_messages(
        self,
        chat: str | int,
        *,
        out_path: str | Path,
        fmt: ExportFormat = "jsonl",
        limit: int = 500,
        offset_id: int = 0,
        min_id: int = 0,
        max_id: int = 0,
        since: str | None = None,
        until: str | None = None,
        search: str | None = None,
        from_user: str | None = None,
        reverse: bool = False,
        download_media: bool = False,
        media_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Export messages from `chat` into an analysis-friendly file (`jsonl`, `md`, or `csv`)."""
        from loguru import logger

        creds = await resolve_telegram_app_credentials(self._store, require_auth=True)
        client = await self._open_client(creds)
        target = _parse_chat_target(chat)
        since_dt = _parse_iso_datetime(since)
        until_dt = _parse_iso_datetime(until)
        dest_file = Path(out_path).expanduser().resolve()
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        if media_dir is not None:
            media_root = Path(media_dir).expanduser().resolve()
        else:
            media_root = (
                Path(self._settings.data_dir)
                / "files"
                / "telegram_app"
                / _safe_chat_segment(chat)
            )

        kwargs, client_since, client_until = _build_get_messages_kwargs(
            limit=limit,
            offset_id=offset_id,
            min_id=min_id,
            max_id=max_id,
            since_dt=since_dt,
            until_dt=until_dt,
            search=search,
            from_user=from_user,
            reverse=reverse,
        )

        try:
            try:
                raw_messages = await client.get_messages(target, **kwargs)
            except Exception as exc:
                _raise_mapped_telethon(exc)
                raise
            items: list[dict[str, Any]] = []
            downloaded_files = 0
            download_failures = 0
            for msg in raw_messages:
                if not _message_in_range(
                    getattr(msg, "date", None),
                    since_dt=client_since,
                    until_dt=client_until,
                ):
                    continue

                local_media_path: str | None = None
                if download_media and getattr(msg, "media", None) is not None:
                    msg_id = getattr(msg, "id", "unknown")
                    msg_dir = media_root / str(msg_id)
                    msg_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        saved = await client.download_media(msg, file=str(msg_dir))
                    except Exception as exc:
                        download_failures += 1
                        logger.warning(
                            "telegram_app.export media download failed chat={} msg={} err={}",
                            chat,
                            msg_id,
                            exc,
                        )
                        saved = None
                    if saved:
                        local_media_path = str(Path(saved).resolve())
                        downloaded_files += 1

                items.append(serialize_message(msg, local_path=local_media_path))

            content = format_messages_export(items, fmt=fmt, chat_label=str(chat))
            dest_file.write_text(content, encoding="utf-8")

            return {
                "chat": str(chat),
                "format": fmt,
                "out_path": str(dest_file),
                "count": len(items),
                "downloaded_files": downloaded_files,
                "download_failures": download_failures,
                "next_offset_id": _next_offset_id(items, reverse=reverse),
                "has_more": len(raw_messages) >= limit,
            }
        finally:
            await client.disconnect()


__all__ = [
    "TELEGRAM_API_HASH_SECRET_KEY",
    "TELEGRAM_API_ID_SECRET_KEY",
    "TELEGRAM_APP_SESSION_SECRET_KEY",
    "ExportFormat",
    "LoginStep",
    "TelegramAppCredentials",
    "TelegramAppService",
    "format_messages_export",
    "mask_phone",
    "resolve_telegram_app_credentials",
    "serialize_dialog",
    "serialize_message",
]
