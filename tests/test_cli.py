"""Unit tests for CLI commands: hook, state, event, main, and __main__ paths."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import pboy.core.config as config_mod
from pboy.cli.main import app, main
from pboy.models.cloudevent import CloudEvent
from pboy.store.sqlite import SQLiteStore


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configure Settings to use an isolated SQLite database and secret key in tmp_path."""
    db_file = tmp_path / "cli_test.db"
    cfg_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("PB_DATABASE_URL", f"sqlite:////{db_file.resolve()}")
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

    # List populated
    res_list = cli_runner.invoke(app, ["hook", "list"])
    assert res_list.exit_code == 0
    assert "my_test_hook" in res_list.output
    assert "jira:issue.created" in res_list.output

    # Delete existing hook
    res_del = cli_runner.invoke(app, ["hook", "delete", "my_test_hook"])
    assert res_del.exit_code == 0
    assert "Hook 'my_test_hook' deleted." in res_del.output


def test_cli_state_put_and_get(cli_runner: CliRunner, cli_db: Path) -> None:
    """Verify state put updates state and state get retrieves or reports missing."""
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

    # Get existing state
    res_get = cli_runner.invoke(app, ["state", "get", "evt_001"])
    assert res_get.exit_code == 0
    assert "'status': 'running'" in res_get.output
    assert "'progress': 75" in res_get.output


def test_cli_event_list_and_get(cli_runner: CliRunner, cli_db: Path) -> None:
    """Verify event list and get when empty, not-found, and populated."""
    # List empty
    res_list_empty = cli_runner.invoke(app, ["event", "list"])
    assert res_list_empty.exit_code == 0
    assert "No events found." in res_list_empty.output

    # Get non-existent
    res_get_missing = cli_runner.invoke(app, ["event", "get", "evt_nonexistent"])
    assert res_get_missing.exit_code == 0
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
    assert "0.1.0" in result.stdout
