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
channel_app = typer.Typer(help="Manage inbound channels and secrets", no_args_is_help=True)

app.add_typer(hook_app, name="hook")
app.add_typer(state_app, name="state")
app.add_typer(event_app, name="event")
app.add_typer(channel_app, name="channel")


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
    host: Annotated[str, typer.Option("--host", help="Bind host (loopback only)")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Server port")] = 8080,
    db: Annotated[str | None, typer.Option("--db", help="SQLite database URL or path")] = None,
) -> None:
    """Start local daemon HTTP API (optional ``--ui`` dashboard at ``/ui``)."""
    from pboy.daemon.server import run_daemon

    run_daemon(host=host, port=port, db=db, ui=ui)


# ── UI ───────────────────────────────────────────────────────────────────────
@app.command("ui")
def ui_cmd(
    host: Annotated[str, typer.Option("--host", help="Bind host (loopback only)")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Web dashboard port")] = 8080,
    db: Annotated[str | None, typer.Option("--db", help="SQLite database URL or path")] = None,
) -> None:
    """Launch the daemon with the embedded read-only dashboard (``/ui``)."""
    from pboy.daemon.server import run_daemon

    run_daemon(host=host, port=port, db=db, ui=True)


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


def _echo_event_detail(detail: dict[str, Any], *, verbose: bool) -> None:
    """Print event detail JSON; omit execution stdout/stderr unless ``verbose``."""
    import json

    payload = detail
    if not verbose:
        payload = dict(detail)
        raw_execs = payload.get("executions")
        if isinstance(raw_execs, list):
            trimmed: list[Any] = []
            for item in raw_execs:
                if isinstance(item, dict):
                    row = {k: v for k, v in item.items() if k not in ("stdout", "stderr")}
                    trimmed.append(row)
                else:
                    trimmed.append(item)
            payload["executions"] = trimmed
    typer.echo(json.dumps(payload, indent=2))


@event_app.command("get")
def event_get(
    event_id: Annotated[str, typer.Argument(help="Event ID")],
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Include execution stdout/stderr"),
    ] = False,
) -> None:
    """Inspect a CloudEvent by ID (includes completion / execution state).

    Uses daemon HTTP API when ``PB_DAEMON_URL`` is set; otherwise local SQLite.
    """
    daemon_url = resolve_daemon_url()
    if daemon_url:
        try:
            detail = DaemonClient(daemon_url).get_event_detail(event_id)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        if not detail:
            typer.echo(f"Event '{event_id}' not found.", err=True)
            raise typer.Exit(code=1)
        _echo_event_detail(detail, verbose=verbose)
        return

    async def _run(store: SQLiteStore) -> None:
        from pboy.core.engine import Engine

        engine = Engine(store=store)
        detail = await engine.event_detail(event_id)
        if not detail:
            typer.echo(f"Event '{event_id}' not found.", err=True)
            raise typer.Exit(code=1)
        _echo_event_detail(detail, verbose=verbose)

    _run_with_store(_run)


@event_app.command("complete")
def event_complete(
    event_id: Annotated[str, typer.Argument(help="Event ID")],
    summary: Annotated[str, typer.Option("--summary", help="Resolution summary")] = "Completed",
    sync: Annotated[
        str | None,
        typer.Option("--sync", help="Outbound sync targets (comma-separated, e.g. telegram)"),
    ] = None,
    file_path: Annotated[
        str | None,
        typer.Option("--file-path", help="Local file to send on telegram sync"),
    ] = None,
    file_id: Annotated[
        str | None,
        typer.Option("--file-id", help="Telegram file_id to re-send on sync"),
    ] = None,
    file_type: Annotated[
        str | None,
        typer.Option(
            "--file-type",
            help="Reply file type: photo|document|audio|video (default document)",
        ),
    ] = None,
) -> None:
    """Mark an event processing as complete (optional outbound sync)."""
    from pathlib import Path

    from pboy.models.completion import parse_sync_targets
    from pboy.models.reply_file import ReplyFile, parse_reply_file_type

    targets = parse_sync_targets(sync)
    if file_type and not (file_path or file_id):
        typer.echo("--file-type requires --file-path or --file-id.", err=True)
        raise typer.Exit(code=1)
    if file_path and file_id:
        typer.echo("Use only one of --file-path or --file-id.", err=True)
        raise typer.Exit(code=1)
    if (file_path or file_id) and "telegram" not in targets:
        typer.echo(
            "Warning: --file-* set without --sync telegram; file will not be sent.",
            err=True,
        )

    # Stage under data_dir/files (absolute) so daemon cwd / path guard are safe.
    resolved_path: str | None = None
    if file_path:
        from pboy.core.config import get_settings
        from pboy.models.reply_file import stage_reply_file_for_daemon

        try:
            staged = stage_reply_file_for_daemon(
                Path(file_path),
                files_root=get_settings().data_dir / "files",
            )
        except OSError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        resolved_path = str(staged)

    reply_file: ReplyFile | None = None
    if resolved_path or file_id:
        try:
            reply_file = ReplyFile(
                file_path=resolved_path,
                file_id=file_id,
                file_type=parse_reply_file_type(file_type),
            )
        except Exception as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    daemon_url = resolve_daemon_url()
    if daemon_url:
        try:
            body = DaemonClient(daemon_url).complete_event(
                event_id,
                summary=summary,
                sync=targets,
                file_path=resolved_path,
                file_id=file_id,
                file_type=file_type or ("document" if reply_file else None),
            )
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        already = body.get("already_completed")
        typer.echo(
            f"Event '{event_id}' completed"
            f"{' (already completed)' if already else ''}."
            f" summary={body.get('summary')!r}"
        )
        sync_results = body.get("sync_results") or []
        for item in sync_results:
            flag = "ok" if item.get("ok") else "FAIL"
            typer.echo(f"  sync {item.get('target')}: {flag} — {item.get('detail')}")
        if any(not item.get("ok") for item in sync_results):
            raise typer.Exit(code=1)
        return

    async def _run(store: SQLiteStore) -> None:
        from pboy.core.engine import Engine

        engine = Engine(store=store)
        try:
            completion, already = await engine.complete_event(
                event_id, summary=summary, sync=targets, reply_file=reply_file
            )
        except KeyError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(
            f"Event '{event_id}' completed"
            f"{' (already completed)' if already else ''}."
            f" summary={completion.summary!r}"
        )
        for item in completion.sync_results:
            flag = "ok" if item.ok else "FAIL"
            typer.echo(f"  sync {item.target}: {flag} — {item.detail}")
        if any(not item.ok for item in completion.sync_results):
            raise typer.Exit(code=1)

    _run_with_store(_run)


# ── Channel ──────────────────────────────────────────────────────────────────
@channel_app.command("list")
def channel_list() -> None:
    """List known channels, enablement, running state, and secret presence."""
    client = _daemon_client_or_none()
    if client is not None:
        try:
            body = client.list_channels()
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        if body.get("env_override"):
            typer.echo("Note: PB_CHANNELS_ENABLED env override is active.")
        for row in body.get("channels", []):
            flags = []
            flags.append("enabled" if row.get("enabled") else "disabled")
            flags.append("running" if row.get("running") else "stopped")
            if row.get("secret_keys") or row.get("secret_key"):
                flags.append("secret=yes" if row.get("has_secret") else "secret=no")
            typer.echo(f"• {row.get('name')} [{', '.join(flags)}]")
        return

    async def _run(store: SQLiteStore) -> None:
        from pboy.channels.registry import ChannelRegistry
        from pboy.core.config import get_settings

        # list-only registry (no emit side effects)
        async def _noop_emit(*_a: Any, **_k: Any) -> None:
            return None

        reg = ChannelRegistry(emit=_noop_emit, store=store, settings=get_settings())
        status = await reg.status()
        if status.get("env_override"):
            typer.echo("Note: PB_CHANNELS_ENABLED env override is active.")
        for row in await reg.list_status():
            flags = []
            flags.append("enabled" if row["enabled"] else "disabled")
            flags.append("running" if row["running"] else "stopped")
            if row.get("secret_keys") or row.get("secret_key"):
                flags.append("secret=yes" if row["has_secret"] else "secret=no")
            typer.echo(f"• {row['name']} [{', '.join(flags)}]")

    _run_with_store(_run)


@channel_app.command("enable")
def channel_enable(
    name: Annotated[str, typer.Argument(help="Channel name (e.g. telegram)")],
) -> None:
    """Enable a channel (persisted in store unless PB_CHANNELS_ENABLED overrides)."""
    client = _daemon_client_or_none()
    if client is not None:
        try:
            body = client.enable_channel(name)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(f"Channel '{body.get('name')}' enabled (running={body.get('running')}).")
        return

    async def _run(store: SQLiteStore) -> None:
        from pboy.channels.registry import ChannelRegistry
        from pboy.core.config import get_settings

        async def _noop_emit(*_a: Any, **_k: Any) -> None:
            return None

        reg = ChannelRegistry(emit=_noop_emit, store=store, settings=get_settings())
        try:
            # Persist only — starting adapters requires the daemon process.
            enabled = await reg.resolve_enabled_names()
            key = name.strip().lower()
            if key not in reg.known_names():
                raise ValueError(f"Unknown channel '{key}'. Known: {', '.join(reg.known_names())}")
            if key not in enabled:
                enabled.append(key)
                from pboy.channels.config import (
                    env_channels_enabled_override,
                    save_enabled_to_store,
                )

                if env_channels_enabled_override() is None:
                    await save_enabled_to_store(store, enabled)
                else:
                    typer.echo(
                        "PB_CHANNELS_ENABLED is set; store not updated. "
                        "Unset it or include the channel in the env list.",
                        err=True,
                    )
                    raise typer.Exit(code=1)
            typer.echo(
                f"Channel '{key}' enabled in store. Restart (or use daemon CLI) to start polling."
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    _run_with_store(_run)


@channel_app.command("disable")
def channel_disable(
    name: Annotated[str, typer.Argument(help="Channel name (e.g. telegram)")],
) -> None:
    """Disable a channel in the persisted enable list."""
    client = _daemon_client_or_none()
    if client is not None:
        try:
            body = client.disable_channel(name)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(f"Channel '{body.get('name')}' disabled (running={body.get('running')}).")
        return

    async def _run(store: SQLiteStore) -> None:
        from pboy.channels.config import (
            env_channels_enabled_override,
            load_enabled_from_store,
            save_enabled_to_store,
        )

        key = name.strip().lower()
        if env_channels_enabled_override() is not None:
            typer.echo(
                "PB_CHANNELS_ENABLED is set; store not updated. Unset it to use persisted config.",
                err=True,
            )
            raise typer.Exit(code=1)
        enabled = [n for n in await load_enabled_from_store(store) if n != key]
        await save_enabled_to_store(store, enabled)
        typer.echo(f"Channel '{key}' disabled in store. Restart daemon if it was running.")

    _run_with_store(_run)


@channel_app.command("set-secret")
def channel_set_secret(
    name: Annotated[str, typer.Argument(help="Channel name (e.g. telegram, slack)")],
    value: Annotated[
        str | None,
        typer.Option("--value", help="Secret value (prefer env/store over shell history)"),
    ] = None,
    secret_name: Annotated[
        str | None,
        typer.Option(
            "--secret-name",
            help="Secret sub-key (slack: bot_token | app_token; telegram: bot_token | allowlist)",
        ),
    ] = None,
) -> None:
    """Store an encrypted channel secret (e.g. telegram bot token; empty clears optional allowlist)."""
    from pboy.channels.config import optional_secret_keys_for_channel, resolve_secret_key

    try:
        resolved_key = resolve_secret_key(name, secret_name)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    is_optional = resolved_key in optional_secret_keys_for_channel(name)
    secret_value = value
    if secret_value is None:
        secret_value = typer.prompt("Secret value", hide_input=True)
    if not secret_value.strip() and not is_optional:
        typer.echo("Secret value must not be empty.", err=True)
        raise typer.Exit(code=1)

    client = _daemon_client_or_none()
    if client is not None:
        try:
            body = client.set_channel_secret(
                name, secret_value, secret_name=secret_name
            )
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(f"Stored secret '{body.get('key')}' for channel '{name}'.")
        return

    async def _run(store: SQLiteStore) -> None:
        await store.put_secret(resolved_key, secret_value.strip())
        typer.echo(f"Stored secret '{resolved_key}' for channel '{name.strip().lower()}'.")

    _run_with_store(_run)


def main() -> None:
    """Entry point function for project.scripts."""
    app()


if __name__ == "__main__":
    main()
