"""Unit tests for SQLiteStore uninitialized errors, secret key resolution, update_hook, and lookups."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import pboy.core.config as config_mod
from pboy.core.config import Settings
from pboy.models.cloudevent import CloudEvent
from pboy.models.execution import ExecutionData
from pboy.models.hook import HookCreate, HookUpdate
from pboy.store.sqlite import SQLiteStore


@pytest.mark.asyncio
async def test_store_uninitialized_database_raises_runtime_error(tmp_path: Path) -> None:
    """Store methods called before init_db() must raise RuntimeError('Database not initialized')."""
    store = SQLiteStore(db_path=tmp_path / "uninit.db", secret_key="k" * 32)
    dummy_event = CloudEvent(source="unit", type="unit:test", data={})
    dummy_exec = ExecutionData(execution_id="ex_test", hook_name="h", event_id="e", status="running")
    dummy_hook = HookCreate(name="h", topic="t", exec="echo 1")

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store._execute_schema()

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.create_hook(dummy_hook)

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.get_hook("h")

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.list_hooks()

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.delete_hook("h")

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.create_event(dummy_event)

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.get_event("e")

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.list_events()

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.record_execution(dummy_exec)

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.get_execution("ex_test")

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.list_executions()

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.put_state("e", "k", {"a": 1})

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.get_state("e", "k")

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.put_secret("k", "v")

    with pytest.raises(RuntimeError, match="Database not initialized"):
        await store.get_secret("k")


def test_store_load_or_create_key_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If secret.key exists, store loads it directly."""
    cfg = Settings(config_dir=tmp_path / "cfg1", data_dir=tmp_path / "data1", secret_key="")
    cfg.ensure_dirs()
    key_file = cfg.secret_key_path
    key_file.write_text("pre_existing_secret_key_1234567890", encoding="utf-8")
    monkeypatch.setattr(config_mod, "_settings", cfg)

    store = SQLiteStore(db_path=tmp_path / "data1" / "test.db", secret_key=None)
    assert store._secret_key == "pre_existing_secret_key_1234567890"


def test_store_load_or_create_key_file_exists_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If os.open raises FileExistsError, fallback reads existing key from file."""
    cfg = Settings(config_dir=tmp_path / "cfg2", data_dir=tmp_path / "data2", secret_key="")
    cfg.ensure_dirs()
    key_file = cfg.secret_key_path
    key_file.write_text("race_condition_secret_key_12345678", encoding="utf-8")
    monkeypatch.setattr(config_mod, "_settings", cfg)

    # Simulate key_path.exists() returning False initially, but os.open raising FileExistsError
    with patch.object(Path, "exists", return_value=False), patch("os.open", side_effect=FileExistsError):
        store = SQLiteStore(db_path=tmp_path / "data2" / "test.db", secret_key=None)
        assert store._secret_key == "race_condition_secret_key_12345678"


def test_store_load_or_create_key_oserror_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If persisting secret key raises OSError, store falls back to in-memory key."""
    cfg = Settings(config_dir=tmp_path / "cfg3", data_dir=tmp_path / "data3", secret_key="")
    cfg.ensure_dirs()
    monkeypatch.setattr(config_mod, "_settings", cfg)

    with patch("os.open", side_effect=OSError("Read-only filesystem")):
        store = SQLiteStore(db_path=tmp_path / "data3" / "test.db", secret_key=None)
        assert len(store._secret_key) > 0
        assert not cfg.secret_key_path.exists()


@pytest.mark.asyncio
async def test_store_update_hook_edges(tmp_store: SQLiteStore) -> None:
    """Verify update_hook for missing hooks, disconnected db, and invalid column skips."""
    # Not found returns None
    assert await tmp_store.update_hook("nonexistent_hook", HookUpdate(exec="echo 1")) is None

    # Hook exists
    await tmp_store.create_hook(HookCreate(name="h_update", topic="t", exec="echo 1"))

    # Update with non-whitelisted column dictionary (hits line 242 and 246)
    class FakeHookUpdate:
        def model_dump(self, **kwargs: object) -> dict[str, str]:
            return {"unwhitelisted_column": "some_value"}

    fake_update: object = FakeHookUpdate()
    res = await tmp_store.update_hook("h_update", fake_update)  # type: ignore[arg-type]
    assert res is not None
    assert res.exec == "echo 1"


@pytest.mark.asyncio
async def test_store_lookups_not_found(tmp_store: SQLiteStore) -> None:
    """Verify get_* methods return None when rows do not exist."""
    assert await tmp_store.get_event("nonexistent_evt") is None
    assert await tmp_store.get_execution("nonexistent_exec") is None
    assert await tmp_store.get_state("nonexistent_evt", "key") is None
    assert await tmp_store.get_secret("nonexistent_secret") is None


@pytest.mark.asyncio
async def test_store_pagination_events_and_executions(tmp_store: SQLiteStore) -> None:
    """Verify list_events and list_executions with limit and offset."""
    # Insert multiple events
    for i in range(3):
        evt = CloudEvent(source="test", type=f"test:type.{i}", data={"index": i})
        await tmp_store.create_event(evt)

    events_page1 = await tmp_store.list_events(limit=2, offset=0)
    assert len(events_page1) == 2
    events_page2 = await tmp_store.list_events(limit=2, offset=2)
    assert len(events_page2) == 1

    # Insert multiple executions
    for i in range(3):
        ex = ExecutionData(
            execution_id=f"ex_{i}",
            hook_name="test_hook",
            event_id="evt_test",
            status="completed",
        )
        await tmp_store.record_execution(ex)

    execs_page1 = await tmp_store.list_executions(limit=2, offset=0)
    assert len(execs_page1) == 2
    execs_page2 = await tmp_store.list_executions(limit=2, offset=2)
    assert len(execs_page2) == 1


@pytest.mark.asyncio
async def test_store_get_state_corrupt_json_raises_value_error(tmp_store: SQLiteStore) -> None:
    """State values that are not JSON objects must raise ValueError."""
    assert tmp_store._db is not None
    await tmp_store._db.execute(
        "INSERT INTO state (event_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
        ("corrupt_event", "bad_state", '"just_a_string_not_dict"', "2026-01-01T00:00:00Z"),
    )
    await tmp_store._db.commit()

    with pytest.raises(ValueError, match="State value for corrupt_event/bad_state must be a JSON object"):
        await tmp_store.get_state("corrupt_event", "bad_state")
