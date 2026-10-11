"""Contract tests: verify implementation against tests/contracts/spec.py."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from typer.testing import CliRunner

from pboy.cli.main import app
from pboy.core.engine import Engine
from pboy.core.filters import evaluate_simple_filter, is_valid_filter_expr
from pboy.core.normalizer import EventNormalizer
from pboy.core.router import HookRouter
from pboy.harness.executor import HarnessExecutor
from pboy.models.cloudevent import (
    TOPIC_TO_TYPE,
    TYPE_TO_TOPIC,
    CloudEvent,
    generate_event_id,
)
from pboy.models.execution import ExecutionData, generate_exec_id
from pboy.models.hook import Hook, HookCreate
from pboy.store.base import StoreBase
from pboy.store.sqlite import SQLiteStore
from tests.contracts.spec import (
    CLI_IMPLEMENTED_GROUPS,
    CLI_NOT_IMPLEMENTED,
    CLOUDEVENT_DATACONTENTTYPE,
    CLOUDEVENT_OPTIONAL_CORE_FIELDS,
    CLOUDEVENT_REQUIRED_FIELDS,
    CLOUDEVENT_SPECVERSION,
    ENGINE_PROCESS_EVENT_ARITY,
    EVENT_DATA_KNOWN_KEYS,
    EVENT_ID_RE,
    EXEC_ID_RE,
    FILTER_INVALID_EXAMPLES,
    FILTER_VALID_EXAMPLES,
    HARNESS_ENV_OPTIONAL,
    HARNESS_ENV_REQUIRED,
    HARNESS_TERMINAL_STATUSES,
    ISO_TIME_RE,
    STORE_REQUIRED_METHODS,
    TOPIC_TYPE_CONTRACT,
)

pytestmark = pytest.mark.contract

PROBE_PATH = Path(__file__).parent / "fixtures" / "harness_probe.py"


def _assert_cloudevent_contract(event: CloudEvent) -> dict[str, Any]:
    dumped = event.model_dump()
    for field in CLOUDEVENT_REQUIRED_FIELDS:
        assert field in dumped, f"missing required CloudEvent field: {field}"
        assert dumped[field], f"empty required CloudEvent field: {field}"
    assert dumped["specversion"] == CLOUDEVENT_SPECVERSION
    assert dumped["datacontenttype"] == CLOUDEVENT_DATACONTENTTYPE
    assert EVENT_ID_RE.match(dumped["id"]), dumped["id"]
    assert ISO_TIME_RE.match(dumped["time"]), dumped["time"]
    for field in CLOUDEVENT_OPTIONAL_CORE_FIELDS:
        assert field in dumped
    if isinstance(dumped["data"], dict):
        unknown = set(dumped["data"]) - EVENT_DATA_KNOWN_KEYS
        # generic source may pass through arbitrary keys; platform sources should not
        if dumped["source"] in {"jira", "slack", "telegram", "confluence"}:
            assert not unknown, f"unknown EventData keys for {dumped['source']}: {unknown}"
    return dumped


# ── CloudEvents / topic map ──────────────────────────────────────────────────
def test_contract_topic_type_bijection() -> None:
    assert TOPIC_TO_TYPE == TOPIC_TYPE_CONTRACT
    assert {v: k for k, v in TOPIC_TYPE_CONTRACT.items()} == TYPE_TO_TOPIC
    assert len(TOPIC_TO_TYPE) == len(set(TOPIC_TO_TYPE.values()))


def test_contract_event_and_exec_id_formats() -> None:
    assert EVENT_ID_RE.match(generate_event_id())
    assert EXEC_ID_RE.match(generate_exec_id())


def test_contract_cloudevent_serialization_roundtrip() -> None:
    event = CloudEvent(
        source="jira",
        type=TOPIC_TYPE_CONTRACT["jira:issue.created"],
        data={"priority": "High", "summary": "x"},
    )
    payload = _assert_cloudevent_contract(event)
    restored = CloudEvent.model_validate_json(json.dumps(payload))
    assert restored.id == event.id
    assert restored.data["priority"] == "High"


# ── Normalizer platform contracts ────────────────────────────────────────────
def test_contract_normalize_jira(sample_jira_payload: dict) -> None:
    event = EventNormalizer().normalize(sample_jira_payload, "jira")
    dumped = _assert_cloudevent_contract(event)
    assert dumped["source"] == "jira"
    assert dumped["type"] == TOPIC_TYPE_CONTRACT["jira:issue.created"]
    assert dumped["data"]["entity_key"] == "INFRA-4029"
    assert dumped["data"]["priority"] == "High"
    assert dumped["data"]["channel_id"] == "INFRA"
    assert dumped["data"]["actor"]["display_name"] == "David Lee"


def test_contract_normalize_slack(sample_slack_payload: dict) -> None:
    event = EventNormalizer().normalize(sample_slack_payload, "slack")
    dumped = _assert_cloudevent_contract(event)
    assert dumped["source"] == "slack"
    assert dumped["type"] == TOPIC_TYPE_CONTRACT["slack:message"]
    assert dumped["data"]["summary"]
    assert dumped["data"]["channel_id"] == "C01234567"


def test_contract_normalize_telegram(sample_telegram_payload: dict) -> None:
    event = EventNormalizer().normalize(sample_telegram_payload, "telegram")
    dumped = _assert_cloudevent_contract(event)
    assert dumped["source"] == "telegram"
    assert dumped["type"] == TOPIC_TYPE_CONTRACT["telegram:message"]
    assert dumped["data"]["entity_key"] == "42"
    assert dumped["data"]["channel_id"] == "-1001"


def test_contract_normalize_confluence(sample_confluence_payload: dict) -> None:
    event = EventNormalizer().normalize(sample_confluence_payload, "confluence")
    dumped = _assert_cloudevent_contract(event)
    assert dumped["source"] == "confluence"
    assert dumped["type"] == TOPIC_TYPE_CONTRACT["confluence:page.updated"]
    assert dumped["data"]["summary"] == "Runbook"


# ── Filter / router ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("expr", FILTER_VALID_EXAMPLES)
def test_contract_filter_valid_examples(expr: str) -> None:
    assert is_valid_filter_expr(expr)


@pytest.mark.parametrize("expr", FILTER_INVALID_EXAMPLES)
def test_contract_filter_invalid_fail_closed(expr: str) -> None:
    if expr.strip():
        assert not is_valid_filter_expr(expr)
    assert not evaluate_simple_filter(expr, {"data": {"priority": "High"}, "source": "jira"})


def test_contract_router_topic_and_filter_semantics() -> None:
    router = HookRouter()
    hooks = [
        Hook(
            name="high",
            topic="jira:issue.created",
            filter='.data.priority == "High"',
            exec="true",
        ),
        Hook(
            name="wildcard-slack",
            topic="slack:*",
            exec="true",
        ),
        Hook(
            name="bad-filter",
            topic="*",
            filter="not-valid-filter",
            exec="true",
        ),
        Hook(
            name="paused",
            topic="*",
            exec="true",
            status="paused",
        ),
    ]
    jira_high = CloudEvent(
        source="jira",
        type=TOPIC_TYPE_CONTRACT["jira:issue.created"],
        data={"priority": "High"},
    )
    names = {h.name for h in router.match(jira_high, hooks)}
    assert names == {"high"}

    slack_evt = CloudEvent(
        source="slack",
        type=TOPIC_TYPE_CONTRACT["slack:message"],
        data={"summary": "hi"},
    )
    assert [h.name for h in router.match(slack_evt, hooks)] == ["wildcard-slack"]


# ── StoreBase interface ──────────────────────────────────────────────────────
def test_contract_storebase_method_surface() -> None:
    abstract_methods = sorted(StoreBase.__abstractmethods__)
    assert abstract_methods == sorted(STORE_REQUIRED_METHODS)
    for name in STORE_REQUIRED_METHODS:
        assert hasattr(SQLiteStore, name)
        assert callable(getattr(SQLiteStore, name))


@pytest.mark.asyncio
async def test_contract_store_crud_semantics(tmp_store: SQLiteStore) -> None:
    assert isinstance(tmp_store, StoreBase)

    hook = await tmp_store.create_hook(
        HookCreate(name="c-hook", topic="jira:issue.created", exec="echo 1")
    )
    assert hook.status == "active"
    assert await tmp_store.get_hook("c-hook") is not None
    assert any(h.name == "c-hook" for h in await tmp_store.list_hooks())

    event = CloudEvent(
        source="jira",
        type=TOPIC_TYPE_CONTRACT["jira:issue.created"],
        data={"priority": "High"},
    )
    await tmp_store.create_event(event)
    loaded = await tmp_store.get_event(event.id)
    assert loaded is not None
    _assert_cloudevent_contract(loaded)

    exec_data = ExecutionData(hook_name="c-hook", event_id=event.id, status="running")
    assert EXEC_ID_RE.match(exec_data.execution_id)
    await tmp_store.record_execution(exec_data)
    assert await tmp_store.get_execution(exec_data.execution_id) is not None

    await tmp_store.put_state(event.id, "execution", {"status": "running", "progress": 10})
    state = await tmp_store.get_state(event.id, "execution")
    assert state == {"status": "running", "progress": 10}

    await tmp_store.put_secret("token", "plain-secret")
    assert await tmp_store.get_secret("token") == "plain-secret"
    assert await tmp_store.get_secret("missing") is None

    assert await tmp_store.delete_hook("c-hook") is True
    assert await tmp_store.get_hook("c-hook") is None


# ── Harness stdin / env ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_contract_harness_stdin_and_env(tmp_store: SQLiteStore) -> None:
    event = CloudEvent(
        source="jira",
        type=TOPIC_TYPE_CONTRACT["jira:issue.created"],
        data={"priority": "High", "summary": "probe"},
    )
    hook = Hook(
        name="probe-hook",
        topic="jira:issue.created",
        exec=f"{sys.executable} {PROBE_PATH}",
        timeout=30,
    )
    lease_token = "lease_contract_token"
    result = await HarnessExecutor(tmp_store).run(hook, event, lease_token=lease_token)

    assert result.status in HARNESS_TERMINAL_STATUSES
    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.stdout is not None

    report = json.loads(result.stdout)
    stdin_event = CloudEvent.model_validate(report["stdin"])
    _assert_cloudevent_contract(stdin_event)
    assert stdin_event.id == event.id

    env = report["env"]
    for key in HARNESS_ENV_REQUIRED:
        assert key in env, f"missing required harness env {key}"
    assert env["PB_EVENT_ID"] == event.id
    assert env["PB_SOURCE"] == event.source
    assert env["PB_HOOK_NAME"] == hook.name
    assert env["PB_DAEMON_URL"].startswith("http")
    for key in HARNESS_ENV_OPTIONAL:
        assert key in env
    assert env["PB_LEASE_TOKEN"] == lease_token


# ── Engine orchestration ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_contract_engine_process_event_arity_and_dispatch(
    engine: Engine, sample_jira_payload: dict
) -> None:
    hints = get_type_hints(Engine.process_event)
    assert "return" in hints

    await engine.store.create_hook(
        HookCreate(
            name="engine-probe",
            topic="jira:issue.created",
            exec=f"{sys.executable} {PROBE_PATH}",
        )
    )

    result = await engine.process_event(sample_jira_payload, "jira", dispatch=False)
    assert isinstance(result, tuple)
    assert len(result) == ENGINE_PROCESS_EVENT_ARITY
    event, matched, executions = result
    _assert_cloudevent_contract(event)
    assert len(matched) == 1
    assert executions == []

    event2, matched2, executions2 = await engine.process_event(sample_jira_payload, "jira")
    assert len(matched2) == 1
    assert len(executions2) == 1
    assert executions2[0].status == "completed"
    assert engine.lease.active_count("engine-probe") == 0
    assert await engine.store.get_event(event2.id) is not None


# ── CLI surface ──────────────────────────────────────────────────────────────
def test_contract_cli_group_surface(cli_runner: CliRunner) -> None:
    help_res = cli_runner.invoke(app, ["--help"])
    assert help_res.exit_code == 0
    for group in CLI_IMPLEMENTED_GROUPS:
        assert group in help_res.output
        group_help = cli_runner.invoke(app, [group, "--help"])
        assert group_help.exit_code == 0


@pytest.mark.parametrize("args", CLI_NOT_IMPLEMENTED)
def test_contract_cli_not_implemented_exits_nonzero(
    cli_runner: CliRunner, args: tuple[str, ...]
) -> None:
    res = cli_runner.invoke(app, list(args))
    assert res.exit_code == 1
    assert "not implemented" in res.output.lower()


def test_contract_cli_hook_register_roundtrip(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI hook register/list/delete must persist through the store contract."""
    import pboy.core.config as config_mod
    from pboy.core.config import Settings

    cfg = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data", secret_key="cli-contract-key")
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    reg = cli_runner.invoke(
        app,
        [
            "hook",
            "register",
            "--name",
            "cli-contract",
            "--topic",
            "jira:issue.created",
            "--filter",
            '.data.priority == "High"',
            "--exec",
            "echo ok",
            "--concurrency",
            "1",
            "--timeout",
            "60",
        ],
    )
    assert reg.exit_code == 0, reg.output
    assert "cli-contract" in reg.output

    listed = cli_runner.invoke(app, ["hook", "list"])
    assert listed.exit_code == 0
    assert "cli-contract" in listed.output

    deleted = cli_runner.invoke(app, ["hook", "delete", "cli-contract"])
    assert deleted.exit_code == 0


def test_contract_engine_signature_documents_dispatch() -> None:
    sig = inspect.signature(Engine.process_event)
    assert "dispatch" in sig.parameters
    assert sig.parameters["dispatch"].default is True
