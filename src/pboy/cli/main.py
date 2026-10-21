"""Command Line Interface (CLI) for Pageboy (`pboy` / `pb`)."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, NoReturn, TypeVar

import httpx
import typer
from pydantic import ValidationError

import pboy
from pboy.cli.daemon_client import DEFAULT_DAEMON_URL, DaemonClient, resolve_daemon_url
from pboy.core.config import DEFAULT_PORT
from pboy.harness.hook_contract import format_register_contract_hint, render_hook_template
from pboy.models.hook import HookCreate
from pboy.store.sqlite import SQLiteStore

T = TypeVar("T")

_VALID_INJECT_SOURCES = ("telegram", "slack", "jira", "confluence", "generic")
_cli_daemon_url: str | None = None

DaemonUrlOption = Annotated[
    str | None,
    typer.Option(
        "--daemon-url",
        "-u",
        help=(
            f"Daemon HTTP base URL (auto-connects to {DEFAULT_DAEMON_URL} when running; "
            "pass for custom port)"
        ),
    ),
]

app = typer.Typer(
    name="pboy",
    help=(
        "Zero-Ops Agent Event Orchestrator & Dispatcher.\n\n"
        f"Automatically connects to the local daemon at {DEFAULT_DAEMON_URL} when running "
        "(use --daemon-url / -u for a custom port; falls back to local SQLite when daemon is stopped).\n\n"
        "Hook subprocesses receive CloudEvent JSON on stdin plus env vars: "
        "PB_EVENT_ID, PB_SOURCE, and PB_HOOK_NAME."
    ),
    no_args_is_help=True,
)

hook_app = typer.Typer(
    help=(
        "Manage event hooks (register, list, delete, template).\n\n"
        "Supported topics: telegram:message, slack:message, jira:issue.created, "
        "jira:issue.updated, confluence:page.updated, or wildcards (e.g. telegram:*, *).\n"
        "Matching uses topic / CloudEvent type — not PB_SOURCE alone.\n"
        "Hook exec receives normalized CloudEvent JSON on stdin plus "
        "PB_EVENT_ID, PB_SOURCE, PB_HOOK_NAME (prefer .data.summary / .data.channel_id).\n"
        "Supported filters: .path.to.field == \"value\" or != (invalid filters fail-closed).\n"
        "Scaffold: `pboy hook template --lang python`. "
        "Telegram/Slack reply from a hook: `pboy event reply --summary '…'` "
        "(uses $PB_EVENT_ID; not `pboy telegram`)."
    ),
    no_args_is_help=True,
)
state_app = typer.Typer(
    help=(
        "Manage and report harness event state (defaults to $PB_EVENT_ID inside hooks)."
    ),
    no_args_is_help=True,
)
event_app = typer.Typer(
    help=(
        "Manage, inject, and inspect CloudEvents "
        "(get/complete/reply default to $PB_EVENT_ID inside hooks).\n\n"
        "Chat reply: `pboy event reply --summary '…'` picks telegram or slack "
        "from the event source (or pass `--sync`). "
        "Do not use `pboy telegram chats|…` for bot replies."
    ),
    no_args_is_help=True,
)
channel_app = typer.Typer(
    help=(
        "Manage inbound channels (telegram, slack) and encrypted secrets.\n\n"
        "Replies: `pboy event reply <id> --summary '…'` "
        "(telegram bot or slack thread from the event source)."
    ),
    no_args_is_help=True,
)
telegram_app = typer.Typer(
    help=(
        "Two rails share this name — do not stop at the App API commands:\n\n"
        "• `reply` / `send` — Telegram **bot** reply to an inbound event "
        "(delegates to `pboy event reply` / `complete --sync telegram`).\n"
        "• `chats` / `messages` / `download` / `export` — App API **user account** "
        "read/export only (needs api_id/api_hash + account connect in /ui/settings).\n\n"
        "If the bot channel is already receiving messages, use `pboy telegram reply` "
        "(or `pboy event reply`); do not create a new bot just to send."
    ),
    no_args_is_help=True,
)

_TELEGRAM_BOT_REPLY_TIP = (
    "tip: Telegram bot reply → pboy telegram reply <id> --summary '…' "
    "(same as event reply / complete --sync telegram). "
    "chats|messages|download|export are App API read/export only."
)
_SLACK_REPLY_TIP = (
    "tip: Slack thread reply → pboy event reply <id> --summary '…' "
    "(or complete --sync slack)."
)
files_app = typer.Typer(
    help=(
        "Manage staged files under data_dir/files "
        "(telegram inbound, outbound staging, telegram_app media)."
    ),
    no_args_is_help=True,
)

app.add_typer(hook_app, name="hook")
app.add_typer(state_app, name="state")
app.add_typer(event_app, name="event")
app.add_typer(channel_app, name="channel")
app.add_typer(telegram_app, name="telegram")
app.add_typer(files_app, name="files")


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
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Pageboy (pboy) — Event Orchestrator & Dispatcher."""
    global _cli_daemon_url
    _cli_daemon_url = daemon_url.strip() if daemon_url and daemon_url.strip() else None


def _active_daemon_url(subcommand_url: str | None = None) -> str | None:
    """Resolve daemon URL from subcommand ``--daemon-url``, root ``--daemon-url``, or default auto-probe."""
    return resolve_daemon_url(subcommand_url or _cli_daemon_url)


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


def _resolve_event_id_or_exit(event_id: str | None) -> str:
    """Resolve explicit event_id or fall back to ``PB_EVENT_ID`` environment variable."""
    candidate = (event_id or os.environ.get("PB_EVENT_ID", "")).strip()
    if candidate:
        return candidate
    typer.echo(
        "Event ID is required (pass <event_id> / --event-id or set PB_EVENT_ID).",
        err=True,
    )
    raise typer.Exit(code=1)


# ── Daemon / Status ──────────────────────────────────────────────────────────
@app.command("daemon")
def daemon_cmd(
    ui: Annotated[bool, typer.Option("--ui", help="Start embedded web dashboard")] = False,
    host: Annotated[str, typer.Option("--host", help="Bind host (loopback only)")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Server port")] = DEFAULT_PORT,
    db: Annotated[str | None, typer.Option("--db", help="SQLite database URL or path")] = None,
    allow_dual_daemon: Annotated[
        bool,
        typer.Option(
            "--allow-dual-daemon",
            help="Allow starting on default port while :8080 daemon runs (testing only)",
        ),
    ] = False,
) -> None:
    """Start local daemon HTTP API (optional ``--ui`` dashboard at ``/ui``)."""
    from pboy.daemon.server import run_daemon

    run_daemon(host=host, port=port, db=db, ui=ui, allow_dual_daemon=allow_dual_daemon)


@app.command("ui")
def ui_cmd(
    host: Annotated[str, typer.Option("--host", help="Bind host (loopback only)")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Web dashboard port")] = DEFAULT_PORT,
    db: Annotated[str | None, typer.Option("--db", help="SQLite database URL or path")] = None,
    allow_dual_daemon: Annotated[
        bool,
        typer.Option(
            "--allow-dual-daemon",
            help="Allow starting on default port while :8080 daemon runs (testing only)",
        ),
    ] = False,
) -> None:
    """Launch the daemon with the embedded web dashboard (``/ui`` and ``/ui/settings``).

    If a daemon is already listening on this host/port, print its URL and exit
    (do not start a second process). Otherwise this process becomes the daemon.
    """
    from pboy.daemon.ensure import daemon_base_url, probe_daemon
    from pboy.daemon.server import run_daemon

    url = daemon_base_url(host, port)
    if probe_daemon(url):
        typer.echo(f"Daemon already running at {url}/ui")
        raise typer.Exit(code=0)
    run_daemon(host=host, port=port, db=db, ui=True, allow_dual_daemon=allow_dual_daemon)


@app.command("mcp")
def mcp_cmd(
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Start the Pageboy MCP server on stdio (for Cursor / MCP hosts).

    Ensures a local daemon is running (spawns ``pboy daemon --ui`` in the
    background if needed; does not stop it on exit). Tools: list_events,
    get_event, reply_event, list_hooks, register_hook, delete_hook, status,
    telegram_list_chats, telegram_list_messages. Event inject and secret writes
    are not exposed.
    """
    from pboy.core.config import get_settings
    from pboy.daemon.ensure import EnsureDaemonError, ensure_daemon_url
    from pboy.mcp.server import run_mcp_stdio

    explicit = daemon_url.strip() if daemon_url and daemon_url.strip() else None
    settings = get_settings()
    db = str(settings.db_path) if settings.database_url else None
    try:
        # Prefer an explicit URL; otherwise ensure the default (or already-probed) daemon.
        ensured = ensure_daemon_url(
            explicit or _active_daemon_url(None),
            db=db,
            ui=True,
        )
    except EnsureDaemonError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        # Non-loopback bind refused.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if ensured.started:
        typer.echo(f"Started Pageboy daemon at {ensured.url} (left running).", err=True)
    run_mcp_stdio(daemon_url=ensured.url)


@app.command("status")
def status_cmd(
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Inspect daemon health (/healthz) when running, or local SQLite store status."""
    resolved_url = _active_daemon_url(daemon_url)
    if resolved_url:
        try:
            body = DaemonClient(resolved_url).healthz()
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        body_out = {"mode": "daemon", "daemon_url": resolved_url, **body}
        typer.echo(json.dumps(body_out, indent=2))
        return

    async def _run(store: SQLiteStore) -> None:
        from pboy.channels.registry import ChannelRegistry
        from pboy.core.config import get_settings

        async def _noop_emit(*_a: Any, **_k: Any) -> None:
            return None

        reg = ChannelRegistry(emit=_noop_emit, store=store, settings=get_settings())
        payload = {
            "mode": "local_sqlite",
            "daemon_url": None,
            "db_path": str(store.db_path),
            "channels": await reg.status(),
        }
        typer.echo(json.dumps(payload, indent=2))

    _run_with_store(_run)


def _daemon_http_error(exc: httpx.HTTPError) -> NoReturn:
    typer.echo(f"Daemon request failed: {exc}", err=True)
    raise typer.Exit(code=1) from exc


def _daemon_client_or_none(subcommand_url: str | None = None) -> DaemonClient | None:
    url = _active_daemon_url(subcommand_url)
    if not url:
        return None
    return DaemonClient(url)


def _warn_local_store_fallback(*, action: str) -> None:
    """Warn when a mutating CLI falls back to local SQLite (split-brain risk).

    Skipped when ``PB_DATABASE_URL`` is set — that path intentionally targets a
    local DB file and never auto-probes the default daemon URL.
    """
    if os.environ.get("PB_DATABASE_URL", "").strip():
        return
    typer.echo(
        f"Warning: no daemon at {DEFAULT_DAEMON_URL}; {action} using local SQLite. "
        "If the daemon is on another port, pass --daemon-url "
        f"(e.g. -u http://127.0.0.1:<port>) so this write hits the running daemon.",
        err=True,
    )


# ── Hooks ────────────────────────────────────────────────────────────────────
@hook_app.command("register")
def hook_register(
    name: Annotated[str, typer.Option("--name", help="Unique hook name")],
    topic: Annotated[
        str,
        typer.Option(
            "--topic",
            help=(
                "Event topic or wildcard: telegram:message, slack:message, "
                "jira:issue.created, jira:issue.updated, confluence:page.updated, telegram:*, *"
            ),
        ),
    ],
    exec_cmd: Annotated[
        str,
        typer.Option(
            "--exec",
            help=(
                "Command to spawn: normalized CloudEvent JSON on stdin; "
                "PB_EVENT_ID, PB_SOURCE, PB_HOOK_NAME in env; "
                "read .data.summary / .data.channel_id (not platform-raw JSON); "
                "scaffold with `pboy hook template`"
            ),
        ),
    ],
    filter_expr: Annotated[
        str | None,
        typer.Option(
            "--filter",
            help='Filter expression: .path.to.field == "value" or != (e.g. .data.summary == "ping")',
        ),
    ] = None,
    concurrency: Annotated[
        int, typer.Option("--concurrency", "-c", help="Max concurrent executions", min=1)
    ] = 1,
    timeout: Annotated[
        int, typer.Option("--timeout", "-t", help="Timeout in integer seconds (e.g. 30)", min=1)
    ] = 180,
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Register a new event hook (via daemon HTTP when running, else local SQLite)."""
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

    client = _daemon_client_or_none(daemon_url)
    if client is not None:
        try:
            hook = client.create_hook(hook_data)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(format_register_contract_hint(name=hook.name, topic=hook.topic))
        return

    _warn_local_store_fallback(action="hook register")

    async def _run(store: SQLiteStore) -> None:
        hook = await store.create_hook(hook_data)
        typer.echo(format_register_contract_hint(name=hook.name, topic=hook.topic))

    _run_with_store(_run)


@hook_app.command("template")
def hook_template(
    lang: Annotated[
        str,
        typer.Option(
            "--lang",
            "-l",
            help="Scaffold language: python or bash",
        ),
    ] = "python",
    topic: Annotated[
        str | None,
        typer.Option(
            "--topic",
            help="Optional topic hint embedded as a comment (e.g. telegram:message)",
        ),
    ] = None,
) -> None:
    """Print a hook script scaffold that reads CloudEvent stdin and PB_SOURCE."""
    try:
        typer.echo(render_hook_template(lang, topic=topic), nl=False)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@hook_app.command("list")
def hook_list(
    daemon_url: DaemonUrlOption = None,
) -> None:
    """List all registered hooks."""
    client = _daemon_client_or_none(daemon_url)
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
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Delete a hook by name."""
    client = _daemon_client_or_none(daemon_url)
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

    _warn_local_store_fallback(action="hook delete")

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
    status: Annotated[str, typer.Option("--status", help="Execution status (e.g. running, completed)")],
    event_id: Annotated[
        str | None,
        typer.Option("--event-id", help="CloudEvent ID (defaults to $PB_EVENT_ID)"),
    ] = None,
    progress: Annotated[
        int, typer.Option("--progress", help="Progress percentage (0-100)", min=0, max=100)
    ] = 0,
    broadcast: Annotated[
        bool, typer.Option("--broadcast", help="Broadcast status change (not yet implemented)")
    ] = False,
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Report harness execution state for an event (defaults ``--event-id`` to ``$PB_EVENT_ID``)."""
    resolved_id = _resolve_event_id_or_exit(event_id)
    payload = {"status": status, "progress": progress, "broadcast": broadcast}
    resolved_url = _active_daemon_url(daemon_url)
    if resolved_url:
        try:
            DaemonClient(resolved_url).put_state(resolved_id, "execution", payload)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(f"State updated for event '{resolved_id}': status={status}, progress={progress}%")
        return

    async def _run(store: SQLiteStore) -> None:
        await store.put_state(resolved_id, "execution", payload)
        typer.echo(f"State updated for event '{resolved_id}': status={status}, progress={progress}%")

    _run_with_store(_run)


@state_app.command("get")
def state_get(
    event_id: Annotated[
        str | None,
        typer.Argument(help="CloudEvent ID (defaults to $PB_EVENT_ID)"),
    ] = None,
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Get harness execution state JSON for an event."""
    resolved_id = _resolve_event_id_or_exit(event_id)
    resolved_url = _active_daemon_url(daemon_url)
    if resolved_url:
        try:
            st = DaemonClient(resolved_url).get_state(resolved_id, "execution")
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        if st is None:
            typer.echo(f"No state found for event '{resolved_id}'.", err=True)
        else:
            typer.echo(json.dumps(st))
        return

    async def _run(store: SQLiteStore) -> None:
        st = await store.get_state(resolved_id, "execution")
        if st is None:
            typer.echo(f"No state found for event '{resolved_id}'.", err=True)
        else:
            typer.echo(json.dumps(st))

    _run_with_store(_run)


# ── Event ────────────────────────────────────────────────────────────────────
@event_app.command("inject")
def event_inject(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            "-s",
            help="Event source: telegram | slack | jira | confluence | generic",
        ),
    ],
    payload: Annotated[
        str,
        typer.Option(
            "--payload",
            "-p",
            help=(
                'Raw JSON object string for the platform (e.g. \'{"message":{"chat":{"id":1},"text":"hi"}}\')'
            ),
        ),
    ] = "{}",
    dispatch: Annotated[
        bool,
        typer.Option(
            "--dispatch/--no-dispatch",
            help="Whether to match and run hooks immediately (use --no-dispatch for dry-run matching)",
        ),
    ] = True,
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Inject a raw platform event into the Engine (for testing hooks or manual triggers).

    Note: bypasses inbound channel allowlists (e.g. ``telegram.allowlist``).
    """
    src = source.strip().lower()
    if src not in _VALID_INJECT_SOURCES:
        typer.echo(
            f"Invalid --source '{source}'. Must be one of: {', '.join(_VALID_INJECT_SOURCES)}.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        parsed_payload = json.loads(payload)
    except ValueError as exc:
        typer.echo(f"Invalid --payload JSON: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(parsed_payload, dict):
        typer.echo("Invalid --payload: root value must be a JSON object.", err=True)
        raise typer.Exit(code=1)

    resolved_url = _active_daemon_url(daemon_url)
    if resolved_url:
        try:
            body = DaemonClient(resolved_url).inject_event(
                src, parsed_payload, dispatch=dispatch
            )
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(json.dumps(body, indent=2))
        return

    async def _run(store: SQLiteStore) -> None:
        from pboy.core.engine import Engine

        engine = Engine(store=store)
        event, matched, executions = await engine.process_event(
            parsed_payload, src, dispatch=dispatch
        )
        result = {
            "event_id": event.id,
            "source": event.source,
            "type": event.type,
            "matched_hooks": [h.name for h in matched],
            "executions": [
                {
                    "execution_id": ex.execution_id,
                    "hook_name": ex.hook_name,
                    "status": ex.status,
                    "exit_code": ex.exit_code,
                    "stdout": ex.stdout,
                    "stderr": ex.stderr,
                }
                for ex in executions
            ],
        }
        typer.echo(json.dumps(result, indent=2))

    _run_with_store(_run)


@event_app.command("list")
def event_list(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of events to list")] = 20,
    daemon_url: DaemonUrlOption = None,
) -> None:
    """List recently recorded events."""
    resolved_url = _active_daemon_url(daemon_url)
    if resolved_url:
        try:
            events = DaemonClient(resolved_url).list_events(limit=limit)
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
    event_id: Annotated[
        str | None,
        typer.Argument(help="Event ID (defaults to $PB_EVENT_ID)"),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Include execution stdout/stderr"),
    ] = False,
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Inspect a CloudEvent by ID as JSON (includes completion / execution state)."""
    resolved_id = _resolve_event_id_or_exit(event_id)
    resolved_url = _active_daemon_url(daemon_url)
    if resolved_url:
        try:
            detail = DaemonClient(resolved_url).get_event_detail(resolved_id)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        if not detail:
            typer.echo(f"Event '{resolved_id}' not found.", err=True)
            raise typer.Exit(code=1)
        _echo_event_detail(detail, verbose=verbose)
        return

    async def _run(store: SQLiteStore) -> None:
        from pboy.core.engine import Engine

        engine = Engine(store=store)
        detail = await engine.event_detail(resolved_id)
        if not detail:
            typer.echo(f"Event '{resolved_id}' not found.", err=True)
            raise typer.Exit(code=1)
        _echo_event_detail(detail, verbose=verbose)

    _run_with_store(_run)


@event_app.command("complete")
def event_complete(
    event_id: Annotated[
        str | None,
        typer.Argument(help="Event ID (defaults to $PB_EVENT_ID)"),
    ] = None,
    summary: Annotated[str, typer.Option("--summary", help="Resolution summary text")] = "Completed",
    sync: Annotated[
        str | None,
        typer.Option(
            "--sync",
            help=(
                "Outbound sync targets (comma-separated). "
                "Use `telegram` to send the summary as a bot reply "
                "(prefer `pboy event reply` for the same effect)."
            ),
        ),
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
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Mark an event complete; add ``--sync telegram`` to reply in the originating chat.

    Prefer ``pboy event reply`` when you only need a Telegram bot reply.
    ``pboy telegram …`` is a different rail (App API) and does not send bot messages.
    """
    from pathlib import Path

    from pboy.models.completion import parse_sync_targets
    from pboy.models.reply_file import ReplyFile, parse_reply_file_type

    resolved_id = _resolve_event_id_or_exit(event_id)
    targets = parse_sync_targets(sync)
    if file_type and not (file_path or file_id):
        typer.echo("--file-type requires --file-path or --file-id.", err=True)
        raise typer.Exit(code=1)
    if file_path and file_id:
        typer.echo("Use only one of --file-path or --file-id.", err=True)
        raise typer.Exit(code=1)
    wants_file = bool(file_path or file_id)
    if wants_file and "slack" in targets:
        typer.echo(
            "--file-* is Telegram-only; remove --sync slack (or the file options).",
            err=True,
        )
        raise typer.Exit(code=1)
    if wants_file and "telegram" not in targets:
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

    resolved_url = _active_daemon_url(daemon_url)
    if resolved_url:
        try:
            body = DaemonClient(resolved_url).complete_event(
                resolved_id,
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
            f"Event '{resolved_id}' completed"
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
                resolved_id, summary=summary, sync=targets, reply_file=reply_file
            )
        except KeyError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(
            f"Event '{resolved_id}' completed"
            f"{' (already completed)' if already else ''}."
            f" summary={completion.summary!r}"
        )
        for item in completion.sync_results:
            flag = "ok" if item.ok else "FAIL"
            typer.echo(f"  sync {item.target}: {flag} — {item.detail}")
        if any(not item.ok for item in completion.sync_results):
            raise typer.Exit(code=1)

    _run_with_store(_run)


def _reply_sync_for_event_source(source: str) -> str:
    """Map CloudEvent ``source`` to a completion sync target."""
    name = source.strip().lower()
    if name == "telegram":
        return "telegram"
    if name == "slack":
        return "slack"
    raise ValueError(
        f"event reply has no outbound for source={source!r} "
        "(supported: telegram, slack). Pass --sync explicitly if needed."
    )


@event_app.command("reply")
def event_reply(
    event_id: Annotated[
        str | None,
        typer.Argument(help="Event ID (defaults to $PB_EVENT_ID)"),
    ] = None,
    summary: Annotated[
        str, typer.Option("--summary", help="Reply text sent to the originating chat")
    ] = "Completed",
    sync: Annotated[
        str | None,
        typer.Option(
            "--sync",
            help=(
                "Outbound target (telegram|slack). "
                "Default: derived from the event source."
            ),
        ),
    ] = None,
    file_path: Annotated[
        str | None,
        typer.Option("--file-path", help="Local file to send (Telegram sync only)"),
    ] = None,
    file_id: Annotated[
        str | None,
        typer.Option("--file-id", help="Telegram file_id to re-send (Telegram sync only)"),
    ] = None,
    file_type: Annotated[
        str | None,
        typer.Option(
            "--file-type",
            help="Reply file type: photo|document|audio|video (Telegram; default document)",
        ),
    ] = None,
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Reply on the originating chat (Telegram bot or Slack thread).

    Shortcut for ``pboy event complete --sync <telegram|slack>``. Default target
    follows the event ``source``. ``pboy telegram chats|messages|…`` is App API
    only and cannot send bot replies.
    """
    resolved_id = _resolve_event_id_or_exit(event_id)
    targets = sync
    if not targets:
        resolved_url = _active_daemon_url(daemon_url)
        source: str | None = None
        if resolved_url:
            try:
                event = DaemonClient(resolved_url).get_event(resolved_id)
            except httpx.HTTPError as exc:
                _daemon_http_error(exc)
            if event is None:
                typer.echo(f"Event '{resolved_id}' not found.", err=True)
                raise typer.Exit(code=1)
            source = event.source
        else:

            async def _load_source(store: SQLiteStore) -> str:
                loaded = await store.get_event(resolved_id)
                if loaded is None:
                    raise KeyError(f"Event '{resolved_id}' not found.")
                return loaded.source

            try:
                source = _run_with_store(_load_source)
            except KeyError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(code=1) from exc
        try:
            targets = _reply_sync_for_event_source(source or "")
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    event_complete(
        event_id=resolved_id,
        summary=summary,
        sync=targets,
        file_path=file_path,
        file_id=file_id,
        file_type=file_type,
        daemon_url=daemon_url,
    )


# ── Channel ──────────────────────────────────────────────────────────────────
@channel_app.command("list")
def channel_list(
    daemon_url: DaemonUrlOption = None,
) -> None:
    """List known channels, enablement, running state, and secret presence."""
    client = _daemon_client_or_none(daemon_url)
    if client is not None:
        try:
            body = client.list_channels()
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        saw_telegram = False
        saw_slack = False
        for row in body.get("channels", []):
            flags = []
            flags.append("enabled" if row.get("enabled") else "disabled")
            flags.append("running" if row.get("running") else "stopped")
            if row.get("secret_keys") or row.get("secret_key"):
                flags.append("secret=yes" if row.get("has_secret") else "secret=no")
            name = str(row.get("name") or "")
            if name == "telegram":
                saw_telegram = True
            if name == "slack":
                saw_slack = True
            typer.echo(f"• {name} [{', '.join(flags)}]")
        if saw_telegram:
            typer.echo(_TELEGRAM_BOT_REPLY_TIP, err=True)
        if saw_slack:
            typer.echo(_SLACK_REPLY_TIP, err=True)
        return

    async def _run(store: SQLiteStore) -> None:
        from pboy.channels.registry import ChannelRegistry
        from pboy.core.config import get_settings

        # list-only registry (no emit side effects)
        async def _noop_emit(*_a: Any, **_k: Any) -> None:
            return None

        reg = ChannelRegistry(emit=_noop_emit, store=store, settings=get_settings())
        saw_telegram = False
        saw_slack = False
        for row in await reg.list_status():
            flags = []
            flags.append("enabled" if row["enabled"] else "disabled")
            flags.append("running" if row["running"] else "stopped")
            if row.get("secret_keys") or row.get("secret_key"):
                flags.append("secret=yes" if row["has_secret"] else "secret=no")
            name = str(row["name"])
            if name == "telegram":
                saw_telegram = True
            if name == "slack":
                saw_slack = True
            typer.echo(f"• {name} [{', '.join(flags)}]")
        if saw_telegram:
            typer.echo(_TELEGRAM_BOT_REPLY_TIP, err=True)
        if saw_slack:
            typer.echo(_SLACK_REPLY_TIP, err=True)

    _run_with_store(_run)


@channel_app.command("enable")
def channel_enable(
    name: Annotated[str, typer.Argument(help="Channel name (e.g. telegram, slack)")],
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Enable a channel (persisted in encrypted store)."""
    client = _daemon_client_or_none(daemon_url)
    if client is not None:
        try:
            body = client.enable_channel(name)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(f"Channel '{body.get('name')}' enabled (running={body.get('running')}).")
        return

    _warn_local_store_fallback(action="channel enable")

    async def _run(store: SQLiteStore) -> None:
        from pboy.channels.config import save_enabled_to_store
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
                await save_enabled_to_store(store, enabled)
            typer.echo(
                f"Channel '{key}' enabled in store. Restart (or use daemon CLI) to start polling."
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    _run_with_store(_run)


@channel_app.command("disable")
def channel_disable(
    name: Annotated[str, typer.Argument(help="Channel name (e.g. telegram, slack)")],
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Disable a channel in the persisted enable list."""
    client = _daemon_client_or_none(daemon_url)
    if client is not None:
        try:
            body = client.disable_channel(name)
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(f"Channel '{body.get('name')}' disabled (running={body.get('running')}).")
        return

    _warn_local_store_fallback(action="channel disable")

    async def _run(store: SQLiteStore) -> None:
        from pboy.channels.config import load_enabled_from_store, save_enabled_to_store

        key = name.strip().lower()
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
            help=(
                "Secret sub-key (slack: bot_token | app_token; "
                "telegram: bot_token | allowlist | api_id | api_hash | app_session)"
            ),
        ),
    ] = None,
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Store an encrypted channel secret (empty clears optional keys like allowlist/api_*)."""
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

    client = _daemon_client_or_none(daemon_url)
    if client is not None:
        try:
            body = client.set_channel_secret(
                name, secret_value, secret_name=secret_name
            )
        except httpx.HTTPError as exc:
            _daemon_http_error(exc)
        typer.echo(f"Stored secret '{body.get('key')}' for channel '{name}'.")
        return

    _warn_local_store_fallback(action="channel set-secret")

    async def _run(store: SQLiteStore) -> None:
        await store.put_secret(resolved_key, secret_value.strip())
        typer.echo(f"Stored secret '{resolved_key}' for channel '{name.strip().lower()}'.")

    _run_with_store(_run)


def _telegram_service(store: SQLiteStore) -> Any:
    from pboy.channels.telegram_app import TelegramAppService
    from pboy.core.config import get_settings

    return TelegramAppService(store=store, settings=get_settings())


@telegram_app.command("reply")
@telegram_app.command("send", hidden=True)
def telegram_reply(
    event_id: Annotated[
        str | None,
        typer.Argument(help="Event ID (defaults to $PB_EVENT_ID)"),
    ] = None,
    summary: Annotated[str, typer.Option("--summary", help="Reply text sent via bot token")] = (
        "Completed"
    ),
    file_path: Annotated[
        str | None,
        typer.Option("--file-path", help="Local file to send with the reply"),
    ] = None,
    file_id: Annotated[
        str | None,
        typer.Option("--file-id", help="Telegram file_id to re-send with the reply"),
    ] = None,
    file_type: Annotated[
        str | None,
        typer.Option(
            "--file-type",
            help="Reply file type: photo|document|audio|video (default document)",
        ),
    ] = None,
    daemon_url: DaemonUrlOption = None,
) -> None:
    """Bot reply to an inbound event (NOT App API).

    Listed under ``pboy telegram`` so ``--help`` does not look read-only.
    Same as ``pboy event reply`` / ``pboy event complete --sync telegram`` —
    uses the bot token and event ``channel_id``, not the user-account session.
    """
    typer.echo(
        "note: bot-token reply via event complete "
        "(not App API chats/messages).",
        err=True,
    )
    event_reply(
        event_id=event_id,
        summary=summary,
        sync="telegram",
        file_path=file_path,
        file_id=file_id,
        file_type=file_type,
        daemon_url=daemon_url,
    )


@telegram_app.command("chats")
@telegram_app.command("dialogs", hidden=True)
def telegram_chats(
    limit: Annotated[int, typer.Option("--limit", help="Max dialogs to return")] = 50,
    search: Annotated[
        str | None,
        typer.Option("--search", help="Filter by title / username / id substring"),
    ] = None,
) -> None:
    """List dialogs visible to the connected Telegram user account."""

    async def _run(store: SQLiteStore) -> None:
        service = _telegram_service(store)
        try:
            rows = await service.list_chats(limit=limit, search=search)
        except RuntimeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))

    _run_with_store(_run)


@telegram_app.command("messages")
def telegram_messages(
    chat: Annotated[str, typer.Argument(help="Chat id or @username (must already be in your dialogs)")],
    limit: Annotated[int, typer.Option("--limit")] = 50,
    offset_id: Annotated[int, typer.Option("--offset-id")] = 0,
    min_id: Annotated[int, typer.Option("--min-id")] = 0,
    max_id: Annotated[int, typer.Option("--max-id")] = 0,
    since: Annotated[str | None, typer.Option("--since", help="ISO datetime")] = None,
    until: Annotated[str | None, typer.Option("--until", help="ISO datetime")] = None,
    search: Annotated[str | None, typer.Option("--search")] = None,
    from_user: Annotated[str | None, typer.Option("--from-user")] = None,
    reverse: Annotated[bool, typer.Option("--reverse")] = False,
) -> None:
    """Fetch messages from a chat (pagination via --offset-id / next_offset_id)."""

    async def _run(store: SQLiteStore) -> None:
        service = _telegram_service(store)
        try:
            body = await service.list_messages(
                chat,
                limit=limit,
                offset_id=offset_id,
                min_id=min_id,
                max_id=max_id,
                since=since,
                until=until,
                search=search,
                from_user=from_user,
                reverse=reverse,
            )
        except (RuntimeError, ValueError, LookupError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(json.dumps(body, indent=2, ensure_ascii=False))

    _run_with_store(_run)


@telegram_app.command("download")
def telegram_download(
    chat: Annotated[str, typer.Argument(help="Chat id or @username")],
    message_id: Annotated[int, typer.Argument(help="Message id with media")],
    out: Annotated[
        str | None,
        typer.Option("--out", help="Output file or directory"),
    ] = None,
) -> None:
    """Download media attached to a message."""

    async def _run(store: SQLiteStore) -> None:
        service = _telegram_service(store)
        try:
            body = await service.download_message_media(chat, message_id, out_path=out)
        except (RuntimeError, ValueError, LookupError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(json.dumps(body, indent=2, ensure_ascii=False))

    _run_with_store(_run)


@telegram_app.command("export")
def telegram_export(
    chat: Annotated[str, typer.Argument(help="Chat id or @username")],
    out: Annotated[str, typer.Option("--out", help="Output path")],
    fmt: Annotated[
        str,
        typer.Option("--format", help="jsonl | md | csv"),
    ] = "jsonl",
    limit: Annotated[int, typer.Option("--limit")] = 500,
    offset_id: Annotated[int, typer.Option("--offset-id")] = 0,
    min_id: Annotated[int, typer.Option("--min-id")] = 0,
    max_id: Annotated[int, typer.Option("--max-id")] = 0,
    since: Annotated[str | None, typer.Option("--since")] = None,
    until: Annotated[str | None, typer.Option("--until")] = None,
    search: Annotated[str | None, typer.Option("--search")] = None,
    from_user: Annotated[str | None, typer.Option("--from-user")] = None,
    reverse: Annotated[bool, typer.Option("--reverse")] = False,
    download_media: Annotated[
        bool,
        typer.Option("--download-media", help="Also save attachments under data_dir"),
    ] = False,
) -> None:
    """Export chat history to jsonl, markdown, or csv for analysis."""
    from pboy.channels.telegram_app import ExportFormat

    cleaned = fmt.strip().lower()
    export_fmt: ExportFormat
    if cleaned == "jsonl":
        export_fmt = "jsonl"
    elif cleaned == "md":
        export_fmt = "md"
    elif cleaned == "csv":
        export_fmt = "csv"
    else:
        typer.echo("--format must be jsonl, md, or csv", err=True)
        raise typer.Exit(code=1)

    async def _run(store: SQLiteStore) -> None:
        service = _telegram_service(store)
        try:
            body = await service.export_messages(
                chat,
                out_path=out,
                fmt=export_fmt,
                limit=limit,
                offset_id=offset_id,
                min_id=min_id,
                max_id=max_id,
                since=since,
                until=until,
                search=search,
                from_user=from_user,
                reverse=reverse,
                download_media=download_media,
            )
        except (RuntimeError, ValueError, LookupError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(json.dumps(body, indent=2, ensure_ascii=False))

    _run_with_store(_run)


@files_app.command("prune")
def files_prune(
    older_than: Annotated[
        str,
        typer.Option(
            "--older-than",
            help="Delete files with mtime older than this age (e.g. 7d, 24h, 30m; bare number = days)",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Report what would be removed without deleting "
                "(includes empty dirs that would remain after counted file removals)"
            ),
        ),
    ] = False,
) -> None:
    """Delete old staged files under data_dir/files (telegram, outbound, telegram_app).

    Default retention is forever — run this when disk grows. Does not talk to the daemon.
    """
    from pboy.core.config import get_settings
    from pboy.core.files_retention import parse_older_than, prune_files_tree

    try:
        age = parse_older_than(older_than)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    files_root = get_settings().data_dir / "files"
    result = prune_files_tree(files_root, older_than=age, dry_run=dry_run)
    action = "would remove" if dry_run else "removed"
    typer.echo(
        f"{action} {result.removed_files} file(s) "
        f"({result.removed_bytes} bytes), {result.removed_dirs} empty dir(s) "
        f"under {files_root}"
    )


def main() -> None:
    """Entry point function for project.scripts."""
    app()


if __name__ == "__main__":
    main()
