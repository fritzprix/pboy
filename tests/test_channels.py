"""Channel adapter interface, fake sink, and registry enablement."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from pboy.channels.config import save_enabled_to_store
from pboy.channels.fake import FakeChannel
from pboy.channels.registry import ChannelRegistry
from pboy.core.config import Settings
from pboy.core.engine import Engine
from pboy.daemon.app import create_app
from pboy.models.hook import HookCreate
from pboy.store.sqlite import SQLiteStore
from tests.helpers import py_print


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> Engine:
    store = SQLiteStore(db_path=tmp_path / "ch.db", secret_key="channel-test-key")
    eng = Engine(store=store)
    await eng.start()
    yield eng
    await eng.stop()


@pytest.mark.asyncio
async def test_fake_channel_pushes_into_engine(
    engine: Engine,
    sample_jira_payload: dict,
) -> None:
    await engine.store.create_hook(
        HookCreate(
            name="from-fake",
            topic="jira:issue.created",
            exec=py_print("from-channel"),
            timeout=30,
        )
    )
    fake = FakeChannel(emit=engine.process_event, source="jira")
    await fake.start()
    try:
        event, matched, executions = await fake.push(sample_jira_payload, dispatch=True)
        assert event.source == "jira"
        assert [h.name for h in matched] == ["from-fake"]
        assert len(executions) == 1
        assert executions[0].status == "completed"
        assert "from-channel" in (executions[0].stdout or "")
        stored = await engine.store.get_event(event.id)
        assert stored is not None
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_fake_push_requires_start(engine: Engine, sample_jira_payload: dict) -> None:
    fake = FakeChannel(emit=engine.process_event)
    with pytest.raises(RuntimeError, match="not started"):
        await fake.push(sample_jira_payload)


@pytest.mark.asyncio
async def test_registry_starts_only_enabled(engine: Engine) -> None:
    await save_enabled_to_store(engine.store, ["fake"])
    settings = Settings()
    registry = ChannelRegistry(emit=engine.process_event, store=engine.store, settings=settings)
    disabled = FakeChannel(emit=engine.process_event, name="disabled-fake", source="jira")
    registry.register(disabled)

    await registry.start_enabled()
    try:
        assert registry.running == ["fake"]
        started = registry.get("fake")
        assert isinstance(started, FakeChannel)
        assert started.started is True
        assert disabled.started is False
    finally:
        await registry.stop_all()
        assert registry.running == []
        fake_after = registry.get("fake")
        assert isinstance(fake_after, FakeChannel)
        assert fake_after.started is False


@pytest.mark.asyncio
async def test_registry_empty_enabled_starts_nothing(engine: Engine) -> None:
    settings = Settings()
    registry = ChannelRegistry(emit=engine.process_event, store=engine.store, settings=settings)
    registry.register(FakeChannel(emit=engine.process_event, name="fake"))
    await registry.start_enabled()
    assert registry.running == []
    fake = registry.get("fake")
    assert isinstance(fake, FakeChannel)
    assert fake.started is False


@pytest.mark.asyncio
async def test_daemon_lifespan_starts_enabled_fake(
    tmp_path: Path,
    sample_jira_payload: dict,
) -> None:
    db = tmp_path / "daemon-ch.db"
    seed = SQLiteStore(db_path=db, secret_key="daemon-ch-key")
    await seed.init_db()
    await save_enabled_to_store(seed, ["fake"])
    await seed.close()

    settings = Settings()
    app = create_app(
        db_path=db,
        secret_key="daemon-ch-key",
        settings=settings,
    )

    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        health = await client.get("/healthz")
        assert health.status_code == 200
        body = health.json()
        assert body["channels"]["enabled"] == ["fake"]
        assert body["channels"]["running"] == ["fake"]

        eng: Engine = app.state.engine
        await eng.store.create_hook(
            HookCreate(
                name="lifespan-hook",
                topic="jira:issue.created",
                exec=py_print("lifespan-ok"),
                timeout=30,
            )
        )
        fake = app.state.channel_registry.get("fake")
        assert isinstance(fake, FakeChannel)
        _event, matched, executions = await fake.push(sample_jira_payload)
        assert [h.name for h in matched] == ["lifespan-hook"]
        assert "lifespan-ok" in (executions[0].stdout or "")


@pytest.mark.asyncio
async def test_daemon_lifespan_skips_channels_when_disabled(tmp_path: Path) -> None:
    settings = Settings()

    app = create_app(
        db_path=tmp_path / "daemon-off.db",
        secret_key="daemon-off-key",
        settings=settings,
    )
    async with app.router.lifespan_context(app):
        assert app.state.channel_registry.running == []
        health_transport = ASGITransport(app=app)
        async with AsyncClient(transport=health_transport, base_url="http://127.0.0.1") as client:
            body = (await client.get("/healthz")).json()
            assert body["channels"]["running"] == []
            assert body["channels"]["enabled"] == []
