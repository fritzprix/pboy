"""Jinja filters for dashboard tables: local time, relative hints, short IDs."""

from __future__ import annotations

from datetime import UTC, datetime

from markupsafe import Markup, escape


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def relative_ko(then: datetime, now: datetime) -> str:
    """Short Korean relative hint ("방금", "3분 전", "2일 전")."""
    seconds = int((now - then).total_seconds())
    if seconds < 60:
        return "방금"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    return f"{seconds // 86400}일 전"


def when(value: str | None, now: datetime | None = None) -> Markup:
    """Render an ISO timestamp as local ``MM-DD HH:MM:SS`` plus a relative hint.

    The daemon is loopback-only, so the server's local zone is the viewer's.
    The full ISO value stays in ``datetime`` / ``title``.
    """
    parsed = _parse_iso(value)
    if parsed is None:
        return Markup("—") if not value else Markup(escape(value))
    local = parsed.astimezone()
    current = now or datetime.now(UTC)
    return Markup(
        '<time datetime="{iso}" title="{iso}">{local} <span class="rel">· {rel}</span></time>'
    ).format(
        iso=value,
        local=local.strftime("%m-%d %H:%M:%S"),
        rel=relative_ko(parsed, current),
    )


def duration(started: str | None, finished: str | None) -> str:
    """Human duration between two ISO timestamps (``—`` while running)."""
    start = _parse_iso(started)
    end = _parse_iso(finished)
    if start is None or end is None:
        return "—"
    seconds = max(0, int((end - start).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def short_id(value: str | None, keep: int = 6) -> str:
    """``evt_20261017_481a6ab75069`` → ``evt_…b75069`` (prefix + last ``keep``)."""
    if not value:
        return ""
    prefix, sep, _rest = value.partition("_")
    if not sep or len(value) <= len(prefix) + 2 + keep:
        return value
    return f"{prefix}_…{value[-keep:]}"


__all__ = ["duration", "relative_ko", "short_id", "when"]
