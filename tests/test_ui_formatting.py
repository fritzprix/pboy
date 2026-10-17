from __future__ import annotations

from datetime import UTC, datetime

from pboy.ui.formatting import duration, relative_ko, short_id, when


def test_short_id_keeps_prefix_and_tail() -> None:
    assert short_id("evt_20261017_481a6ab75069") == "evt_…b75069"
    assert short_id("exec_20261017_0123456789ab") == "exec_…6789ab"
    assert short_id("plain") == "plain"
    assert short_id("a_1234567") == "a_1234567"
    assert short_id(None) == ""


def test_relative_ko_buckets() -> None:
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
    assert relative_ko(datetime(2026, 10, 17, 11, 59, 30, tzinfo=UTC), now) == "방금"
    assert relative_ko(datetime(2026, 10, 17, 11, 57, 0, tzinfo=UTC), now) == "3분 전"
    assert relative_ko(datetime(2026, 10, 17, 9, 0, 0, tzinfo=UTC), now) == "3시간 전"
    assert relative_ko(datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC), now) == "2일 전"
    assert relative_ko(datetime(2026, 10, 17, 12, 5, 0, tzinfo=UTC), now) == "방금"


def test_when_keeps_iso_and_escapes_garbage() -> None:
    now = datetime(2026, 10, 17, 3, 10, 0, tzinfo=UTC)
    html = str(when("2026-10-17T03:04:52Z", now=now))
    assert 'datetime="2026-10-17T03:04:52Z"' in html
    assert "5분 전" in html
    assert str(when(None)) == "—"
    assert str(when("<bad>")) == "&lt;bad&gt;"


def test_duration() -> None:
    assert duration("2026-10-17T03:00:00Z", "2026-10-17T03:00:09Z") == "9s"
    assert duration("2026-10-17T03:00:00Z", "2026-10-17T03:01:05Z") == "1m 5s"
    assert duration("2026-10-17T03:00:00Z", "2026-10-17T05:02:00Z") == "2h 2m"
    assert duration("2026-10-17T03:00:00Z", None) == "—"
