"""Pytest fixtures for Pageboy testing."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from typer.testing import CliRunner

from pboy.core.engine import Engine
from pboy.store.sqlite import SQLiteStore


@pytest.fixture
def cli_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    """Typer CLI runner with color disabled (Rich ANSI breaks plain ``--flag`` asserts)."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    return CliRunner()


def strip_ansi(text: str) -> str:
    """Remove VT100 color sequences from CLI help output."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest_asyncio.fixture
async def tmp_store() -> AsyncGenerator[SQLiteStore, None]:
    """Fixture providing an initialized SQLiteStore with temporary database file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_pboy.db"
        store = SQLiteStore(db_path=db_path, secret_key="test_secret_key_fixed_32bytes!!")
        await store.init_db()
        yield store
        await store.close()


@pytest_asyncio.fixture
async def engine(tmp_store: SQLiteStore) -> AsyncGenerator[Engine, None]:
    """Fixture providing an Engine initialized with tmp_store."""
    eng = Engine(store=tmp_store)
    await eng.start()
    yield eng
    await eng.stop()


@pytest.fixture
def sample_jira_payload() -> dict:
    """Sample Jira issue created webhook payload."""
    return {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "id": "10001",
            "key": "INFRA-4029",
            "fields": {
                "summary": "B200 NVLink training error",
                "description": "Node 04 GPU drop out detected in cluster B.",
                "priority": {"name": "High"},
                "labels": ["gpu", "infra", "urgent"],
                "project": {"key": "INFRA"},
                "creator": {"accountId": "usr_david_01", "displayName": "David Lee"},
            },
        },
    }


@pytest.fixture
def sample_slack_payload() -> dict:
    """Sample Slack message event payload."""
    return {
        "event": {
            "client_msg_id": "slack_msg_12345",
            "channel": "C01234567",
            "user": "U98765432",
            "text": "Deployment failed on production worker 2",
            "ts": "1696800000.000100",
        }
    }


@pytest.fixture
def sample_telegram_payload() -> dict:
    """Sample Telegram message update payload."""
    return {
        "message": {
            "message_id": 42,
            "text": "Restart worker-2",
            "from": {"id": 991, "username": "ops_bot", "first_name": "Ops"},
            "chat": {"id": -1001, "type": "group"},
        }
    }


@pytest.fixture
def sample_confluence_payload() -> dict:
    """Sample Confluence page update payload."""
    return {
        "page": {
            "id": "77881",
            "title": "Runbook",
            "body": {"storage": {"value": "<p>steps</p>"}},
            "version": {"number": 3},
        },
        "space": {"key": "OPS"},
        "user": {"accountId": "usr_ops", "displayName": "Ops Owner"},
    }
