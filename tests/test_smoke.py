"""Smoke test suite verifying package imports, CLI, models, store, and core engine."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import pboy
from pboy.cli.main import app
from pboy.core.engine import Engine
from pboy.core.filters import evaluate_simple_filter, is_valid_filter_expr
from pboy.core.lease import LeaseEngine
from pboy.core.normalizer import EventNormalizer
from pboy.core.router import HookRouter
from pboy.models.cloudevent import CloudEvent
from pboy.models.hook import Hook, HookCreate, HookUpdate
from pboy.store.sqlite import SQLiteStore
from tests.helpers import py_print


def test_package_metadata() -> None:
    """Verify package metadata is present and valid."""
    assert pboy.__version__ == "0.1.0"
    assert pboy.__author__ == "fritzprix"


def test_cli_help(cli_runner: CliRunner) -> None:
    """Verify CLI top-level and subcommands respond to --help."""
    from tests.conftest import strip_ansi

    res = cli_runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "Zero-Ops Agent Event Orchestrator" in strip_ansi(res.output)

    for sub in ["daemon", "ui", "hook", "state", "event", "channel"]:
        sub_res = cli_runner.invoke(app, [sub, "--help"])
        assert sub_res.exit_code == 0


def test_cli_version(cli_runner: CliRunner) -> None:
    """Verify pboy --version prints current version."""
    res = cli_runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert "0.1.0" in res.output


def test_cloudevent_model() -> None:
    """Verify CloudEvents v1.0 model instantiation and serialization."""
    event = CloudEvent(
        source="jira",
        type="com.atlassian.jira.issue.created",
        data={"summary": "Sample incident", "priority": "High"},
    )
    assert event.specversion == "1.0"
    assert event.id.startswith("evt_")
    assert len(event.id.split("_")[-1]) == 12
    assert event.source == "jira"
    assert event.data["priority"] == "High"


def test_normalizer(sample_jira_payload: dict, sample_slack_payload: dict) -> None:
    """Verify EventNormalizer transforms incoming platform payloads."""
    normalizer = EventNormalizer()

    jira_evt = normalizer.normalize(sample_jira_payload, "jira")
    assert jira_evt.source == "jira"
    assert jira_evt.type == "com.atlassian.jira.issue.created"
    assert jira_evt.data["entity_key"] == "INFRA-4029"
    assert jira_evt.data["priority"] == "High"
    assert jira_evt.data["actor"]["display_name"] == "David Lee"

    slack_evt = normalizer.normalize(sample_slack_payload, "slack")
    assert slack_evt.source == "slack"
    assert slack_evt.type == "com.slack.message.received"
    assert slack_evt.data["summary"] == "Deployment failed on production worker 2"


def test_router() -> None:
    """Verify HookRouter matches topics and filter rules."""
    router = HookRouter()
    hooks = [
        Hook(
            name="high-priority-jira",
            topic="jira:issue.created",
            filter='.data.priority == "High"',
            exec="run_incident.sh",
        ),
        Hook(
            name="all-slack",
            topic="slack:*",
            exec="notify_agent.sh",
        ),
        Hook(
            name="paused-hook",
            topic="jira:issue.created",
            exec="echo paused",
            status="paused",
        ),
    ]

    event = CloudEvent(
        source="jira",
        type="com.atlassian.jira.issue.created",
        data={"priority": "High"},
    )
    matched = router.match(event, hooks)
    assert len(matched) == 1
    assert matched[0].name == "high-priority-jira"

    # When priority is Low, filter should reject
    low_event = CloudEvent(
        source="jira",
        type="com.atlassian.jira.issue.created",
        data={"priority": "Low"},
    )
    matched_low = router.match(low_event, hooks)
    assert len(matched_low) == 0


def test_filter_fail_closed() -> None:
    """Unsupported filter syntax must not match events."""
    assert not is_valid_filter_expr(".data.priority | select(. == \"High\")")
    assert not evaluate_simple_filter("not a filter", {"data": {"priority": "High"}})

    router = HookRouter()
    hooks = [
        Hook(
            name="bad-filter",
            topic="jira:issue.created",
            filter="completely-invalid",
            exec="echo hi",
        )
    ]
    event = CloudEvent(
        source="jira",
        type="com.atlassian.jira.issue.created",
        data={"priority": "High"},
    )
    assert router.match(event, hooks) == []


def test_hook_validation_rejects_bad_values() -> None:
    """HookCreate should reject negative limits and unsupported filters."""
    with pytest.raises(ValidationError):
        HookCreate(name="x", topic="jira:issue.created", exec="echo 1", concurrency=0)
    with pytest.raises(ValidationError):
        HookCreate(name="x", topic="jira:issue.created", exec="echo 1", timeout=-1)
    with pytest.raises(ValidationError):
        HookCreate(
            name="x",
            topic="jira:issue.created",
            exec="echo 1",
            filter="jq(.data)",
        )


@pytest.mark.asyncio
async def test_lease_engine() -> None:
    """Verify LeaseEngine concurrency limits and release."""
    lease = LeaseEngine()
    token1 = await lease.acquire("worker", max_concurrency=2)
    assert token1 is not None
    assert lease.active_count("worker") == 1

    token2 = await lease.acquire("worker", max_concurrency=2)
    assert token2 is not None
    assert lease.active_count("worker") == 2

    # Exceeding concurrency limit returns None
    token3 = await lease.acquire("worker", max_concurrency=2)
    assert token3 is None

    # Releasing unlocks slot
    await lease.release(token1)
    assert lease.active_count("worker") == 1
    token4 = await lease.acquire("worker", max_concurrency=2)
    assert token4 is not None


@pytest.mark.asyncio
async def test_sqlite_store_and_crypto(tmp_store: SQLiteStore) -> None:
    """Verify SQLite storage CRUD and AES-256-GCM secret encryption."""
    await tmp_store.put_secret("api_token", "super_secret_token_val")
    decrypted = await tmp_store.get_secret("api_token")
    assert decrypted == "super_secret_token_val"

    created = await tmp_store.create_hook(
        HookCreate(
            name="test-hook",
            topic="jira:issue.created",
            exec="echo 1",
            concurrency=2,
            timeout=60,
        )
    )
    assert created.name == "test-hook"

    fetched = await tmp_store.get_hook("test-hook")
    assert fetched is not None
    assert fetched.exec == "echo 1"

    all_hooks = await tmp_store.list_hooks()
    assert len(all_hooks) == 1

    await tmp_store.put_state("evt_123", "progress", {"pct": 75})
    state_val = await tmp_store.get_state("evt_123", "progress")
    assert state_val == {"pct": 75}


@pytest.mark.asyncio
async def test_secret_key_file_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Persisted secret.key must be owner-readable/writable only (0600)."""
    import pboy.core.config as config_mod
    from pboy.core.config import Settings

    cfg = Settings(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "data",
        secret_key="",
    )
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    store = SQLiteStore(db_path=tmp_path / "data" / "pboy.db")
    await store.init_db()
    try:
        key_path = cfg.secret_key_path
        assert key_path.exists()
        if os.name != "nt":
            mode = key_path.stat().st_mode & 0o777
            assert mode == stat.S_IRUSR | stat.S_IWUSR
        await store.put_secret("k", "v")
        assert await store.get_secret("k") == "v"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_engine_process_event(engine: Engine, sample_jira_payload: dict) -> None:
    """Verify event intake, normalization, persistence, and routing without dispatch."""
    await engine.store.create_hook(
        HookCreate(
            name="jira-auto-responder",
            topic="jira:issue.created",
            exec="python responder.py",
        )
    )

    event, matched, executions = await engine.process_event(
        sample_jira_payload, "jira", dispatch=False
    )
    assert event.source == "jira"
    assert event.type == "com.atlassian.jira.issue.created"
    assert len(matched) == 1
    assert matched[0].name == "jira-auto-responder"
    assert executions == []

    stored_evt = await engine.store.get_event(event.id)
    assert stored_evt is not None
    assert stored_evt.id == event.id


@pytest.mark.asyncio
async def test_engine_dispatches_harness(engine: Engine, sample_jira_payload: dict) -> None:
    """Verify matched hooks are executed via the harness with lease release."""
    await engine.store.create_hook(
        HookCreate(
            name="echo-hook",
            topic="jira:issue.created",
            exec=py_print("harness-ok"),
            timeout=30,
        )
    )

    _event, matched, executions = await engine.process_event(sample_jira_payload, "jira")
    assert len(matched) == 1
    assert len(executions) == 1
    assert executions[0].status == "completed"
    assert executions[0].exit_code == 0
    assert executions[0].stdout is not None
    assert "harness-ok" in executions[0].stdout
    assert engine.lease.active_count("echo-hook") == 0

    stored = await engine.store.get_execution(executions[0].execution_id)
    assert stored is not None
    assert stored.status == "completed"


def test_normalizer_defensive_against_malformed_payloads() -> None:
    """Normalizer must not crash on malformed or non-dict nested structures."""
    norm = EventNormalizer()

    # Empty payload handling
    for src in ("jira", "confluence", "slack", "telegram", "unknown"):
        evt = norm.normalize({}, src)
        assert evt.source == (src if src != "unknown" else "unknown")
        assert evt.id.startswith("evt_")

    # Jira with malformed fields
    bad_jira = {
        "webhookEvent": 99999,  # non-str
        "issue": "not-a-dict",  # non-dict
        "user": None,
    }
    jira_evt = norm.normalize(bad_jira, "jira")
    assert jira_evt.source == "jira"
    assert jira_evt.type == "com.atlassian.jira.issue.created"

    # Confluence with malformed nested bodies
    bad_conf = {
        "page": {"id": 123, "body": "invalid_body", "version": "not-dict"},
        "space": None,
        "user": 42,
    }
    conf_evt = norm.normalize(bad_conf, "confluence")
    assert conf_evt.source == "confluence"
    assert conf_evt.data.get("entity_key") == "123"

    # Slack with non-dict event
    bad_slack = {"event": "string_not_dict"}
    slack_evt = norm.normalize(bad_slack, "slack")
    assert slack_evt.source == "slack"

    # Telegram with non-dict message and sender
    bad_tg = {"message": {"message_id": 99, "from": "invalid_sender", "chat": None}}
    tg_evt = norm.normalize(bad_tg, "telegram")
    assert tg_evt.source == "telegram"
    assert tg_evt.data.get("entity_key") == "99"


@pytest.mark.asyncio
async def test_lease_engine_ttl_and_safe_active_count() -> None:
    """LeaseEngine with TTL must expire leases and maintain safe active_count."""
    lease = LeaseEngine(default_ttl_seconds=0.05)
    tok = await lease.acquire("ttl-worker", max_concurrency=1)
    assert tok is not None
    assert lease.active_count("ttl-worker") == 1

    # Before TTL expiry, max_concurrency rejects second acquire
    tok2 = await lease.acquire("ttl-worker", max_concurrency=1)
    assert tok2 is None

    # Wait for TTL to expire
    await asyncio.sleep(0.06)
    assert lease.active_count("ttl-worker") == 0

    # Can acquire again after TTL expiry
    tok3 = await lease.acquire("ttl-worker", max_concurrency=1)
    assert tok3 is not None
    assert lease.active_count("ttl-worker") == 1

    # Releasing non-existent token returns False and does not make count negative
    assert await lease.release("invalid_token") is False
    assert lease.active_count("ttl-worker") == 1
    assert await lease.release(tok3) is True
    assert lease.active_count("ttl-worker") == 0


@pytest.mark.asyncio
async def test_sqlite_update_hook_whitelist(tmp_store: SQLiteStore) -> None:
    """SQLiteStore.update_hook must strictly allow only known whitelisted columns."""
    created = await tmp_store.create_hook(
        HookCreate(name="whitelisted-hook", topic="jira:issue.created", exec="echo 1")
    )
    assert created.status == "active"

    # Legitimate update
    updated = await tmp_store.update_hook(
        "whitelisted-hook",
        HookUpdate(exec="echo updated", concurrency=3, status="paused"),
    )
    assert updated is not None
    assert updated.exec == "echo updated"
    assert updated.concurrency == 3
    assert updated.status == "paused"

    # Empty update returns existing hook without error
    same = await tmp_store.update_hook("whitelisted-hook", HookUpdate())
    assert same is not None
    assert same.exec == "echo updated"


@pytest.mark.asyncio
async def test_engine_concurrent_dispatch(engine: Engine, sample_jira_payload: dict) -> None:
    """Multiple matched hooks run concurrently with preserved lease semantics."""
    await engine.store.create_hook(
        HookCreate(
            name="hook-alpha",
            topic="jira:issue.created",
            exec=py_print("alpha"),
            timeout=10,
        )
    )
    await engine.store.create_hook(
        HookCreate(
            name="hook-beta",
            topic="jira:issue.created",
            exec=py_print("beta"),
            timeout=10,
        )
    )

    _event, matched, executions = await engine.process_event(sample_jira_payload, "jira")
    assert len(matched) == 2
    assert len(executions) == 2
    hook_names = [e.hook_name for e in executions]
    assert "hook-alpha" in hook_names
    assert "hook-beta" in hook_names
    for ex in executions:
        assert ex.status == "completed"
    assert engine.lease.active_count("hook-alpha") == 0
    assert engine.lease.active_count("hook-beta") == 0
