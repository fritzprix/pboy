"""Command Line Interface (CLI) for Pageboy (`pboy` / `pb`)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, NoReturn, TypeVar

import httpx
import typer
from pydantic import ValidationError

import pboy
from pboy.cli.daemon_client import DaemonClient, resolve_daemon_url
from pboy.models.hook import HookCreate
from pboy.store.sqlite import SQLiteStore

T = TypeVar("T")

app = typer.Typer(
    name="pboy",
    help="Zero-Ops Agent Event Orchestrator & Dispatcher",
    no_args_is_help=True,
)

hook_app = typer.Typer(help="Manage event hooks", no_args_is_help=True)
state_app = typer.Typer(help="Manage and report harness event state", no_args_is_help=True)
event_app = typer.Typer(help="Manage and inspect CloudEvents", no_args_is_help=True)

app.add_typer(hook_app, name="hook")
app.add_typer(state_app, name="state")
app.add_typer(event_app, name="event")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"pboy version: {pboy.__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: Annotated[
        bool | None,
        typer.Option("--version", "-v", help="Show version and exit", callback=version_callback, is_eager=True),
    ] = None,
) -> None:
    """Pageboy (pboy) — Event Orchestrator & Dispatcher."""
    pass


def _not_implemented(feature: str) -> None:
    """Exit with a clear error for Alpha features that are not wired yet."""
    typer.echo(
        f"Error: '{feature}' is not implemented in this Alpha release.",
        err=True,
    )
    raise typer.Exit(code=1)


def _run_with_store(coro_fn: Callable[[SQLiteStore], Coroutine[Any, Any, T]]) -> T:
    """Execute an async operation with an initialized SQLiteStore, ensuring cleanup."""
    async def _runner() -> T:
        store = SQLiteStore()
        await store.init_db()
        try:
            return await coro_fn(store)
        finally:
            await store.close()

    return asyncio.run(_runner())


# ── Daemon ───────────────────────────────────────────────────────────────────
@app.command("daemon")
def daemon_cmd(
    ui: Annotated[bool, typer.Option("--ui", help="Start embedded web dashboard")] = False,
    host: Annotated[str, typer.Option("--host", help="Bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Server port")] = 8080,
    db: Annotated[str | None, typer.Option("--db", help="SQLite database URL or path")] = None,
) -> None:
    """Start local daemon HTTP API (UI flag reserved until M5)."""
    from pboy.daemon.server import run_daemon

    run_daemon(host=host, port=port, db=db, ui=ui)


# ── UI ───────────────────────────────────────────────────────────────────────
@app.command("ui")
def ui_cmd(
    port: Annotated[int, typer.Option("--port", "-p", help="Web dashboard port")] = 8080,
) -> None:
    """Launch the local web UI dashboard."""
    del port  # reserved for future UI wiring
    _not_implemented("ui")


def _daemon_http_error(exc: httpx.HTTPError) -> NoReturn:
    typer.echo(f"Daemon request failed: {exc}", err=True)
    raise typer.Exit(code=1) from exc


def _daemon_client_or_none() -> DaemonClient | None:
    url = resolve_daemon_url()
    if not url:
        return None
    return DaemonClient(url)


# ── Hooks ────────────────────────────────────────────────────────────────────
@hook_app.command("register")
def hook_register(
    name: Annotated[str, typer.Option("--name", help="Hook name")],
    topic: Annotated[str, typer.Option("--topic", help="Event topic or pattern (e.g. jira:issue.created)")],
    exec_cmd: Annotated[str, typer.Option("--exec", help="Command line to execute on event")],
    filter_expr: Annotated[str | None, typer.Option("--filter", help="Filter expression")] = None,
    concurrency: Annotated[
        int, typer.Option("--concurrency", "-c", help="Max concurrency", min=1)
    ] = 1,
    timeout: Annotated[
        int, typer.Option("--timeout", "-t", help="Timeout in seconds", min=1)
    ] = 180,
) -> None:
    """Register a new event hook (daemon HTTP when PB_DAEMON_URL is set)."""
    try:
        hook_data = HookCreate(
            name=name,
            topic=topic,
            filter=filter_expr,
            exec=exec_cmd,
            concurrency=concurrency,
            timeout=timeout,
        )
    except ValidationError as exc:
        typer.echo(f"Invalid hook: {exc.errors()[0]['msg']}", err=True)
        raise typer.Exit(code=1) from exc

    client = _daemon_client_or_none()
    if client is not None:
        try:
            hook = client.create_hook(hook_data)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(f"Hook '{hook.name}' registered for topic '{hook.topic}'.")
        return

    async def _run(store: SQLiteStore) -> None:
        hook = await store.create_hook(hook_data)
        typer.echo(f"Hook '{hook.name}' registered for topic '{hook.topic}'.")

    _run_with_store(_run)


@hook_app.command("list")
def hook_list() -> None:
    """List all registered hooks (daemon HTTP when PB_DAEMON_URL is set)."""
    client = _daemon_client_or_none()
    if client is not None:
        try:
            hooks = client.list_hooks()
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        if not hooks:
            typer.echo("No registered hooks found.")
            return
        for h in hooks:
            typer.echo(f"• {h.name} [{h.status}] - topic: {h.topic} - exec: {h.exec}")
        return

    async def _run(store: SQLiteStore) -> None:
        hooks = await store.list_hooks()
        if not hooks:
            typer.echo("No registered hooks found.")
            return
        for h in hooks:
            typer.echo(f"• {h.name} [{h.status}] - topic: {h.topic} - exec: {h.exec}")

    _run_with_store(_run)


@hook_app.command("delete")
def hook_delete(
    name: Annotated[str, typer.Argument(help="Hook name to delete")],
) -> None:
    """Delete a hook by name (daemon HTTP when PB_DAEMON_URL is set)."""
    client = _daemon_client_or_none()
    if client is not None:
        try:
            deleted = client.delete_hook(name)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        if deleted:
            typer.echo(f"Hook '{name}' deleted.")
        else:
            typer.echo(f"Hook '{name}' not found.", err=True)
        return

    async def _run(store: SQLiteStore) -> None:
        deleted = await store.delete_hook(name)
        if deleted:
            typer.echo(f"Hook '{name}' deleted.")
        else:
            typer.echo(f"Hook '{name}' not found.", err=True)

    _run_with_store(_run)


# ── State ────────────────────────────────────────────────────────────────────
@state_app.command("put")
def state_put(
    event_id: Annotated[str, typer.Option("--event-id", help="CloudEvent ID")],
    status: Annotated[str, typer.Option("--status", help="Execution status (e.g. running, completed)")],
    progress: Annotated[
        int, typer.Option("--progress", help="Progress percentage (0-100)", min=0, max=100)
    ] = 0,
    broadcast: Annotated[
        bool, typer.Option("--broadcast", help="Broadcast status change (not yet implemented)")
    ] = False,
) -> None:
    """Report harness execution state for an event.

    Uses daemon HTTP API when ``PB_DAEMON_URL`` is set; otherwise local SQLite.
    """
    payload = {"status": status, "progress": progress, "broadcast": broadcast}
    daemon_url = resolve_daemon_url()
    if daemon_url:
        try:
            DaemonClient(daemon_url).put_state(event_id, "execution", payload)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(f"State updated for event '{event_id}': status={status}, progress={progress}%")
        return

    async def _run(store: SQLiteStore) -> None:
        await store.put_state(event_id, "execution", payload)
        typer.echo(f"State updated for event '{event_id}': status={status}, progress={progress}%")

    _run_with_store(_run)


@state_app.command("get")
def state_get(
    event_id: Annotated[str, typer.Argument(help="CloudEvent ID")],
) -> None:
    """Get harness execution state for an event.

    Uses daemon HTTP API when ``PB_DAEMON_URL`` is set; otherwise local SQLite.
    """
    daemon_url = resolve_daemon_url()
    if daemon_url:
        try:
            st = DaemonClient(daemon_url).get_state(event_id, "execution")
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        if st is None:
            typer.echo(f"No state found for event '{event_id}'.", err=True)
        else:
            typer.echo(str(st))
        return

    async def _run(store: SQLiteStore) -> None:
        st = await store.get_state(event_id, "execution")
        if st is None:
            typer.echo(f"No state found for event '{event_id}'.", err=True)
        else:
            typer.echo(str(st))

    _run_with_store(_run)


# ── Event ────────────────────────────────────────────────────────────────────
@event_app.command("list")
def event_list(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of events to list")] = 20,
) -> None:
    """List recently recorded events.

    Uses daemon HTTP API when ``PB_DAEMON_URL`` is set; otherwise local SQLite.
    """
    daemon_url = resolve_daemon_url()
    if daemon_url:
        try:
            events = DaemonClient(daemon_url).list_events(limit=limit)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        if not events:
            typer.echo("No events found.")
            return
        for evt in events:
            typer.echo(f"[{evt.time}] {evt.id} ({evt.source}) - {evt.type}")
        return

    async def _run(store: SQLiteStore) -> None:
        events = await store.list_events(limit=limit)
        if not events:
            typer.echo("No events found.")
            return
        for evt in events:
            typer.echo(f"[{evt.time}] {evt.id} ({evt.source}) - {evt.type}")

    _run_with_store(_run)


@event_app.command("get")
def event_get(
    event_id: Annotated[str, typer.Argument(help="Event ID")],
) -> None:
    """Inspect a CloudEvent by ID.

    Uses daemon HTTP API when ``PB_DAEMON_URL`` is set; otherwise local SQLite.
    """
    daemon_url = resolve_daemon_url()
    if daemon_url:
        try:
            evt = DaemonClient(daemon_url).get_event(event_id)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        if not evt:
            typer.echo(f"Event '{event_id}' not found.", err=True)
        else:
            typer.echo(evt.model_dump_json(indent=2))
        return

    async def _run(store: SQLiteStore) -> None:
        evt = await store.get_event(event_id)
        if not evt:
            typer.echo(f"Event '{event_id}' not found.", err=True)
        else:
            typer.echo(evt.model_dump_json(indent=2))

    _run_with_store(_run)


@event_app.command("complete")
def event_complete(
    event_id: Annotated[str, typer.Argument(help="Event ID")],
    summary: Annotated[str, typer.Option("--summary", help="Resolution summary")] = "Completed",
    sync: Annotated[str | None, typer.Option("--sync", help="Target sync channels")] = None,
) -> None:
    """Mark an event processing as complete."""
    del event_id, summary, sync  # reserved for future completion/sync wiring
    _not_implemented("event complete")


def main() -> None:
    """Entry point function for project.scripts."""
    app()


if __name__ == "__main__":
    main()
