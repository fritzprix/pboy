"""Unit tests for models, filters, configuration, and normalizer edge cases."""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from pboy.core.config import Settings
from pboy.core.filters import evaluate_simple_filter
from pboy.core.normalizer import EventNormalizer
from pboy.models.hook import HookCreate, HookUpdate


def test_hook_filter_validators() -> None:
    """Verify HookCreate and HookUpdate filter validators with empty/whitespace/valid/invalid."""
    # HookCreate: None and whitespace filter should be preserved without error
    hc_none = HookCreate(name="h1", topic="t1", exec="echo 1", filter=None)
    assert hc_none.filter is None

    hc_blank = HookCreate(name="h2", topic="t2", exec="echo 1", filter="   ")
    assert hc_blank.filter == "   "

    # HookUpdate: None and whitespace filter
    hu_none = HookUpdate(filter=None)
    assert hu_none.filter is None

    hu_blank = HookUpdate(filter="   ")
    assert hu_blank.filter == "   "

    # HookUpdate: valid filter
    hu_valid = HookUpdate(filter='.data.priority == "High"')
    assert hu_valid.filter == '.data.priority == "High"'

    # HookUpdate: invalid filter raises ValidationError
    with pytest.raises(ValidationError, match="Unsupported filter syntax"):
        HookUpdate(filter="invalid_expr(no_equality)")


def test_filters_path_miss_and_operators() -> None:
    """Verify filter evaluation for missing paths, != operator, and unknown operator fail-closed."""
    payload = {
        "data": {
            "priority": "High",
            "nested": {"count": 10},
            "scalar": "hello",
        }
    }

    # Path miss: key does not exist
    assert not evaluate_simple_filter('.data.missing == "val"', payload)

    # Path miss: intermediate is not a dict (accessing .sub on string "hello")
    assert not evaluate_simple_filter('.data.scalar.sub == "val"', payload)

    # Operator != matches
    assert evaluate_simple_filter('.data.priority != "Low"', payload)

    # Operator != does not match
    assert not evaluate_simple_filter('.data.priority != "High"', payload)

    # Operator == matches
    assert evaluate_simple_filter('.data.priority == "High"', payload)

    # Unknown operator fail-closed (line 42) via regex patch
    with patch("pboy.core.filters._FILTER_RE", re.compile(r'^\.?([\w.]+)\s*(<>)\s*["\']?([^"\']+)["\']?$')):
        assert not evaluate_simple_filter(".data.priority <> High", payload)


def test_config_database_url_resolution(tmp_path: Path) -> None:
    """Verify Settings.db_path prefix stripping and expanduser handling."""
    # sqlite+aiosqlite:/// prefix
    s1 = Settings(database_url="sqlite+aiosqlite:////tmp/db_aio.sqlite")
    assert s1.db_path == Path("/tmp/db_aio.sqlite")

    # sqlite:/// prefix
    s2 = Settings(database_url="sqlite:////tmp/db_sync.sqlite")
    assert s2.db_path == Path("/tmp/db_sync.sqlite")

    # Raw file path without prefix
    s3 = Settings(database_url="/tmp/db_plain.sqlite")
    assert s3.db_path == Path("/tmp/db_plain.sqlite")

    # User tilde expansion
    home = Path(os.path.expanduser("~"))
    s4 = Settings(database_url="sqlite:///~/test_home.db")
    assert s4.db_path == home / "test_home.db"

    # None database_url falls back to data_dir / "pboy.db"
    s5 = Settings(data_dir=tmp_path / "custom_data", database_url=None)
    assert s5.db_path == tmp_path / "custom_data" / "pboy.db"


def test_normalizer_jira_updated_event() -> None:
    """Verify normalization of Jira issue_updated webhook event (line 57)."""
    norm = EventNormalizer()
    payload = {
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "key": "OPS-123",
            "fields": {
                "summary": "Fix pipeline",
                "priority": {"name": "Critical"},
            },
        },
    }
    evt = norm.normalize(payload, "jira")
    assert evt.source == "jira"
    assert evt.type == "com.atlassian.jira.issue.updated"
    assert evt.data.get("entity_key") == "OPS-123"
    assert evt.data.get("priority") == "Critical"
