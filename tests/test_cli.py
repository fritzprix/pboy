"""Unit tests for CLI commands: hook, state, event, main, and __main__ paths."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import pboy
import pboy.core.config as config_mod
from pboy.cli.main import app, main
from pboy.models.cloudevent import CloudEvent
from pboy.models.execution import ExecutionData
from pboy.store.sqlite import SQLiteStore


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configure Settings to use an isolated SQLite database and secret key in tmp_path."""
    db_file = tmp_path / "cli_test.db"
    cfg_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    # ``sqlite:///`` + as_posix: Unix keeps a leading ``/`` (four-slash URL);
    # Windows gets ``sqlite:///C:/...`` so Path is not ``\C:\...``.
    monkeypatch.setenv("PB_DATABASE_URL", f"sqlite:///{db_file.resolve().as_posix()}")
    monkeypatch.setenv("PB_SECRET_KEY", "fixed_cli_test_secret_key_32bytes!")
    monkeypatch.setenv("PB_CONFIG_DIR", str(cfg_dir.resolve()))
    monkeypatch.setenv("PB_DATA_DIR", str(data_dir.resolve()))
    monkeypatch.setattr(config_mod, "_settings", None)
    return db_file


def test_cli_hook_register_validation_error(cli_runner: CliRunner, cli_db: Path) -> None:
    """CLI hook register must display error and exit 1 on ValidationError."""
    res = cli_runner.invoke(
        app,
        [
            "hook",
            "register",
            "--name",
            "invalid_hook",
            "--topic",
            "jira:issue.created",
            "--exec",
            "echo 1",
            "--filter",
            "unsupported_filter_syntax(bad)",
        ],
    )
    assert res.exit_code == 1
    assert "Invalid hook:" in res.output or "Invalid hook:" in res.stderr


def test_cli_hook_lifecycle(cli_runner: CliRunner, cli_db: Path) -> None:
    """Verify hook register, list empty/populated, and delete not-found/success."""
    # List when empty
    res_empty = cli_runner.invoke(app, ["hook", "list"])
    assert res_empty.exit_code == 0
    assert "No registered hooks found." in res_empty.output

    # Delete non-existent hook
    res_del_missing = cli_runner.invoke(app, ["hook", "delete", "nonexistent_h"])
    assert res_del_missing.exit_code == 0
    assert "Hook 'nonexistent_h' not found." in res_del_missing.output

    # Register valid hook
    res_reg = cli_runner.invoke(
        app,
        [
            "hook",
            "register",
            "--name",
            "my_test_hook",
            "--topic",
            "jira:issue.created",
            "--exec",
            "echo hello",
        ],
    )
    assert res_reg.exit_code == 0
    assert "Hook 'my_test_hook' registered" in res_reg.output
    assert "PB_SOURCE='jira'" in res_reg.output
    assert "com.atlassian.jira.issue.created" in res_reg.output
    assert "pboy hook template" in res_reg.output

    # List populated
    res_list = cli_runner.invoke(app, ["hook", "list"])
    assert res_list.exit_code == 0
    assert "my_test_hook" in res_list.output
    assert "jira:issue.created" in res_list.output

    # Delete existing hook
    res_del = cli_runner.invoke(app, ["hook", "delete", "my_test_hook"])
    assert res_del.exit_code == 0
    assert "Hook 'my_test_hook' deleted." in res_del.output


def test_cli_hook_register_wildcard_contract_hint(
    cli_runner: CliRunner, cli_db: Path
) -> None:
    res = cli_runner.invoke(
        app,
        [
            "hook",
            "register",
            "--name",
            "wild_hook",
            "--topic",
            "*",
            "--exec",
            "echo hello",
        ],
    )
    assert res.exit_code == 0
    assert "Wildcard topic" in res.output
    assert "PB_SOURCE" in res.output


def test_cli_hook_template_python_and_bash(cli_runner: CliRunner) -> None:
    py = cli_runner.invoke(app, ["hook", "template", "--lang", "python"])
    assert py.exit_code == 0, py.output
    assert "PB_EVENT_ID" in py.output
    assert "PB_SOURCE" in py.output
    assert "json.load" in py.output
    assert "event reply" in py.output

    bash = cli_runner.invoke(
        app, ["hook", "template", "--lang", "bash", "--topic", "telegram:message"]
    )
    assert bash.exit_code == 0, bash.output
    assert "topic hint: telegram:message" in bash.output
    assert "jq -r '.data.summary" in bash.output
    assert bash.output.startswith("#!")

    bad = cli_runner.invoke(app, ["hook", "template", "--lang", "ruby"])
    assert bad.exit_code == 1
    assert "Unsupported language" in bad.output


def test_cli_state_put_and_get(cli_runner: CliRunner, cli_db: Path) -> None:
    """Verify state put updates state and state get retrieves JSON or reports missing."""
    import json

    # Get non-existent state
    res_get_missing = cli_runner.invoke(app, ["state", "get", "evt_nonexistent"])
    assert res_get_missing.exit_code == 0
    assert "No state found for event 'evt_nonexistent'." in res_get_missing.output

    # Put state
    res_put = cli_runner.invoke(
        app,
        [
            "state",
            "put",
            "--event-id",
            "evt_001",
            "--status",
            "running",
            "--progress",
            "75",
        ],
    )
    assert res_put.exit_code == 0
    assert "State updated for event 'evt_001': status=running, progress=75%" in res_put.output

    # Get existing state (valid JSON)
    res_get = cli_runner.invoke(app, ["state", "get", "evt_001"])
    assert res_get.exit_code == 0
    parsed = json.loads(res_get.output)
    assert parsed["status"] == "running"
    assert parsed["progress"] == 75


def test_cli_pb_event_id_env_fallback_and_inject_and_status(
    cli_runner: CliRunner, cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify event inject, status, and PB_EVENT_ID fallback for state put/get, event get/complete."""
    import json

    # 1. pboy status in local SQLite mode
    status_res = cli_runner.invoke(app, ["status"])
    assert status_res.exit_code == 0, status_res.output
    status_json = json.loads(status_res.output)
    assert status_json["mode"] == "local_sqlite"
    assert "cli_test.db" in status_json["db_path"]

    # 2. pboy event inject
    inject_res = cli_runner.invoke(
        app,
        [
            "event",
            "inject",
            "--source",
            "telegram",
            "--payload",
            '{"message":{"message_id":7,"chat":{"id":100},"from":{"id":200,"username":"bob"},"text":"hello"}}',
            "--no-dispatch",
        ],
    )
    assert inject_res.exit_code == 0, inject_res.output
    injected = json.loads(inject_res.output)
    event_id = injected["event_id"]
    assert injected["source"] == "telegram"
    assert injected["type"] == "com.telegram.message.received"

    # 3. Missing event_id without PB_EVENT_ID fails with exit code 1
    monkeypatch.delenv("PB_EVENT_ID", raising=False)
    missing_id_res = cli_runner.invoke(app, ["state", "put", "--status", "running"])
    assert missing_id_res.exit_code == 1
    assert "PB_EVENT_ID" in missing_id_res.output

    # 4. With PB_EVENT_ID set, state put / state get / event get / event complete work without explicit ID
    monkeypatch.setenv("PB_EVENT_ID", event_id)
    put_env = cli_runner.invoke(app, ["state", "put", "--status", "running", "--progress", "40"])
    assert put_env.exit_code == 0, put_env.output

    get_state_env = cli_runner.invoke(app, ["state", "get"])
    assert get_state_env.exit_code == 0, get_state_env.output
    assert json.loads(get_state_env.output)["progress"] == 40

    get_evt_env = cli_runner.invoke(app, ["event", "get"])
    assert get_evt_env.exit_code == 0, get_evt_env.output
    assert json.loads(get_evt_env.output)["id"] == event_id

    complete_env = cli_runner.invoke(app, ["event", "complete", "--summary", "Auto-ID done"])
    assert complete_env.exit_code == 0, complete_env.output
    assert "Auto-ID done" in complete_env.output


def test_cli_event_list_and_get(cli_runner: CliRunner, cli_db: Path) -> None:
    """Verify event list and get when empty, not-found, and populated."""
    # List empty
    res_list_empty = cli_runner.invoke(app, ["event", "list"])
    assert res_list_empty.exit_code == 0
    assert "No events found." in res_list_empty.output

    # Get non-existent
    res_get_missing = cli_runner.invoke(app, ["event", "get", "evt_nonexistent"])
    assert res_get_missing.exit_code == 1
    assert "Event 'evt_nonexistent' not found." in res_get_missing.output

    # Seed an event directly into the database
    async def _seed() -> None:
        store = SQLiteStore()
        await store.init_db()
        try:
            evt = CloudEvent(
                id="evt_seed_100",
                source="jira",
                type="com.atlassian.jira.issue.created",
                data={"summary": "Sample seeded event"},
            )
            await store.create_event(evt)
        finally:
            await store.close()

    asyncio.run(_seed())

    # List populated
    res_list = cli_runner.invoke(app, ["event", "list"])
    assert res_list.exit_code == 0
    assert "evt_seed_100" in res_list.output
    assert "jira" in res_list.output

    # Get existing
    res_get = cli_runner.invoke(app, ["event", "get", "evt_seed_100"])
    assert res_get.exit_code == 0
    assert "evt_seed_100" in res_get.output
    assert '"specversion": "1.0"' in res_get.output
    assert '"executions"' in res_get.output


def test_cli_event_get_verbose_includes_stdout_stderr(cli_runner: CliRunner, cli_db: Path) -> None:
    """Default get omits execution streams; --verbose includes them."""

    async def _seed() -> None:
        store = SQLiteStore()
        await store.init_db()
        try:
            evt = CloudEvent(
                id="evt_verbose_1",
                source="jira",
                type="com.atlassian.jira.issue.created",
                data={"summary": "verbose"},
            )
            await store.create_event(evt)
            await store.record_execution(
                ExecutionData(
                    hook_name="v-hook",
                    event_id="evt_verbose_1",
                    status="completed",
                    exit_code=0,
                    stdout="STREAM_OUT",
                    stderr="STREAM_ERR",
                )
            )
        finally:
            await store.close()

    asyncio.run(_seed())

    quiet = cli_runner.invoke(app, ["event", "get", "evt_verbose_1"])
    assert quiet.exit_code == 0
    assert "v-hook" in quiet.output
    assert "STREAM_OUT" not in quiet.output
    assert "STREAM_ERR" not in quiet.output

    verbose = cli_runner.invoke(app, ["event", "get", "evt_verbose_1", "--verbose"])
    assert verbose.exit_code == 0
    assert "STREAM_OUT" in verbose.output
    assert "STREAM_ERR" in verbose.output


def test_cli_main_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify main() entrypoint triggers app."""
    monkeypatch.setattr(sys, "argv", ["pboy", "--version"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0


def test_cli_dunder_main() -> None:
    """Verify ``python -m pboy.cli.main`` entry works in a clean process."""
    result = subprocess.run(
        [sys.executable, "-m", "pboy.cli.main", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert pboy.__version__ in result.stdout


def test_cli_telegram_bot_reply_discoverability(cli_runner: CliRunner) -> None:
    """Bot reply path must be obvious in help (not confused with App API)."""
    event_help = cli_runner.invoke(app, ["event", "--help"])
    assert event_help.exit_code == 0
    assert "reply" in event_help.output
    assert "complete --sync telegram" in event_help.output or "event reply" in event_help.output

    reply_help = cli_runner.invoke(app, ["event", "reply", "--help"])
    assert reply_help.exit_code == 0
    assert "telegram" in reply_help.output.lower() and "slack" in reply_help.output.lower()
    assert "App API" in reply_help.output

    tg_help = cli_runner.invoke(app, ["telegram", "--help"])
    assert tg_help.exit_code == 0
    # Commands table must list reply — agents stop at read-only if they only see
    # chats/messages/download/export.
    assert "reply" in tg_help.output
    assert "Bot reply" in tg_help.output or "bot" in tg_help.output.lower()
    assert "chats" in tg_help.output and "messages" in tg_help.output

    tg_reply_help = cli_runner.invoke(app, ["telegram", "reply", "--help"])
    assert tg_reply_help.exit_code == 0
    assert "event reply" in tg_reply_help.output or "complete --sync telegram" in tg_reply_help.output
    assert "NOT App API" in tg_reply_help.output or "not App API" in tg_reply_help.output.lower()


def test_cli_event_reply_completes_with_telegram_sync(
    cli_runner: CliRunner, cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """event reply is complete --sync telegram (no bot token → sync FAIL is ok)."""
    inject = cli_runner.invoke(
        app,
        [
            "event",
            "inject",
            "--source",
            "telegram",
            "--payload",
            '{"message":{"message_id":9,"chat":{"id":100},"from":{"id":200,"username":"bob"},"text":"hi"}}',
            "--no-dispatch",
        ],
    )
    assert inject.exit_code == 0, inject.output
    import json

    event_id = json.loads(inject.output)["event_id"]
    monkeypatch.setenv("PB_EVENT_ID", event_id)
    # No bot token in this fixture DB → sync fails but completion is recorded.
    res = cli_runner.invoke(app, ["event", "reply", "--summary", "pong-from-reply"])
    assert "pong-from-reply" in res.output
    assert "sync telegram" in res.output


def test_agent_brief_leads_with_telegram_bot_reply() -> None:
    from pboy.ui.agent_brief import render_agent_brief

    from pboy.core.config import DEFAULT_PORT

    text = render_agent_brief("http://127.0.0.1:8080")
    assert "Reply in chat" in text
    assert "pboy event reply" in text
    assert "--sync slack" in text
    assert "read-only" in text
    assert f"default port ({DEFAULT_PORT})" in text
    assert "{default_port}" not in text
    # Reply section appears before the three jobs checklist.
    assert text.index("Reply in chat") < text.index("Your three jobs")
    assert "Writing a hook script" in text
    assert "PB_SOURCE" in text
    assert "com.telegram.message.received" in text
    assert "pboy hook template" in text
    assert text.index("Hooks and completion") < text.index("Writing a hook script")


def test_cli_telegram_reply_forces_telegram_sync_not_slack(
    cli_runner: CliRunner, cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pboy telegram reply must not follow a Slack event source."""
    import json

    inject = cli_runner.invoke(
        app,
        [
            "event",
            "inject",
            "--source",
            "slack",
            "--payload",
            json.dumps(
                {
                    "event": {
                        "client_msg_id": "m1",
                        "channel": "C1",
                        "user": "U1",
                        "text": "hi",
                        "ts": "1.2",
                    }
                }
            ),
            "--no-dispatch",
        ],
    )
    assert inject.exit_code == 0, inject.output
    event_id = json.loads(inject.output)["event_id"]
    res = cli_runner.invoke(
        app, ["telegram", "reply", event_id, "--summary", "must-be-telegram-sync"]
    )
    # Forced --sync telegram: missing bot token → telegram FAIL, not slack.
    assert "sync telegram" in res.output
    assert "sync slack" not in res.output


def test_cli_event_reply_unsupported_source_exits(
    cli_runner: CliRunner, cli_db: Path
) -> None:
    import json

    inject = cli_runner.invoke(
        app,
        [
            "event",
            "inject",
            "--source",
            "jira",
            "--payload",
            json.dumps(
                {
                    "issue": {
                        "key": "X-1",
                        "fields": {
                            "summary": "s",
                            "priority": {"name": "High"},
                            "project": {"key": "X"},
                            "reporter": {"displayName": "a"},
                        },
                    }
                }
            ),
            "--no-dispatch",
        ],
    )
    assert inject.exit_code == 0, inject.output
    event_id = json.loads(inject.output)["event_id"]
    res = cli_runner.invoke(app, ["event", "reply", event_id, "--summary", "nope"])
    assert res.exit_code == 1
    assert "no outbound" in res.output.lower() or "supported" in res.output.lower()


def test_cli_event_reply_requires_event_id(
    cli_runner: CliRunner, cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PB_EVENT_ID", raising=False)
    res = cli_runner.invoke(app, ["event", "reply", "--summary", "x"])
    assert res.exit_code == 1
    assert "PB_EVENT_ID" in res.output or "Event ID" in res.output


def test_cli_complete_rejects_file_with_slack_sync(
    cli_runner: CliRunner, cli_db: Path, tmp_path: Path
) -> None:
    import json

    inject = cli_runner.invoke(
        app,
        [
            "event",
            "inject",
            "--source",
            "slack",
            "--payload",
            '{"event":{"channel":"C1","user":"U1","text":"hi","ts":"1.2"}}',
            "--no-dispatch",
        ],
    )
    assert inject.exit_code == 0, inject.output
    event_id = json.loads(inject.output)["event_id"]
    blob = tmp_path / "x.bin"
    blob.write_bytes(b"x")
    res = cli_runner.invoke(
        app,
        [
            "event",
            "complete",
            event_id,
            "--summary",
            "file",
            "--sync",
            "slack",
            "--file-path",
            str(blob),
        ],
    )
    assert res.exit_code == 1
    assert "Telegram-only" in res.output or "file" in res.output.lower()
