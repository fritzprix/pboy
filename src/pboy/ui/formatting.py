"""Jinja filters for dashboard tables: local time, relative hints, short IDs."""

from __future__ import annotations

from datetime import UTC, datetime

from markupsafe import Markup, escape

from pboy.i18n import DEFAULT_LANG, translate


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def relative(then: datetime, now: datetime, lang: str = DEFAULT_LANG) -> str:
    """Short relative hint ("just now", "3 min ago", "2 d ago") in ``lang``."""
    seconds = int((now - then).total_seconds())
    if seconds < 60:
        return translate(lang, "rel.just_now")
    if seconds < 3600:
        return translate(lang, "rel.minutes", n=seconds // 60)
    if seconds < 86400:
        return translate(lang, "rel.hours", n=seconds // 3600)
    return translate(lang, "rel.days", n=seconds // 86400)


def when(value: str | None, now: datetime | None = None, lang: str = DEFAULT_LANG) -> Markup:
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
        rel=relative(parsed, current, lang),
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


__all__ = ["duration", "relative", "short_id", "when"]
