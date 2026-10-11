"""Canonical Alpha contracts for Pageboy public surfaces.

These constants are the source of truth for contract tests. Implementation
changes that break them should fail CI rather than silently drift docs.
"""

from __future__ import annotations

import re
from typing import Final

# ── CloudEvents v1.0 ─────────────────────────────────────────────────────────
CLOUDEVENT_SPECVERSION: Final = "1.0"
CLOUDEVENT_DATACONTENTTYPE: Final = "application/json"
CLOUDEVENT_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "specversion",
    "id",
    "source",
    "type",
)
CLOUDEVENT_OPTIONAL_CORE_FIELDS: Final[tuple[str, ...]] = (
    "time",
    "datacontenttype",
    "data",
)

EVENT_ID_RE: Final = re.compile(r"^evt_\d{8}_[0-9a-f]{12}$")
EXEC_ID_RE: Final = re.compile(r"^exec_\d{8}_[0-9a-f]{12}$")
ISO_TIME_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Topic ↔ CloudEvent type bijection (must stay 1:1)
TOPIC_TYPE_CONTRACT: Final[dict[str, str]] = {
    "jira:issue.created": "com.atlassian.jira.issue.created",
    "jira:issue.updated": "com.atlassian.jira.issue.updated",
    "confluence:page.updated": "com.atlassian.confluence.page.updated",
    "telegram:message": "com.telegram.message.received",
    "slack:message": "com.slack.message.received",
}

# Normalized EventData keys produced by platform normalizers (subset allowed)
EVENT_DATA_KNOWN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "entity_key",
        "channel_id",
        "thread_id",
        "actor",
        "summary",
        "description",
        "priority",
        "labels",
        "raw_ref",
        "extra",
    }
)

# ── Filter expression ────────────────────────────────────────────────────────
FILTER_VALID_EXAMPLES: Final[tuple[str, ...]] = (
    '.data.priority == "High"',
    ".source == jira",
    '.data.entity_key != "X-1"',
)
FILTER_INVALID_EXAMPLES: Final[tuple[str, ...]] = (
    "jq(.data)",
    ".data.priority | select(. == \"High\")",
    "completely-invalid",
    "",
)

# ── Harness subprocess contract ──────────────────────────────────────────────
HARNESS_ENV_REQUIRED: Final[tuple[str, ...]] = (
    "PB_EVENT_ID",
    "PB_SOURCE",
    "PB_HOOK_NAME",
    "PB_DAEMON_URL",
)
HARNESS_ENV_OPTIONAL: Final[tuple[str, ...]] = ("PB_LEASE_TOKEN",)
HARNESS_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "timeout"}
)

# ── Store interface ──────────────────────────────────────────────────────────
STORE_REQUIRED_METHODS: Final[tuple[str, ...]] = (
    "init_db",
    "close",
    "create_hook",
    "get_hook",
    "list_hooks",
    "update_hook",
    "delete_hook",
    "create_event",
    "get_event",
    "list_events",
    "record_execution",
    "get_execution",
    "list_executions",
    "put_state",
    "get_state",
    "put_secret",
    "get_secret",
)

# ── CLI surface ──────────────────────────────────────────────────────────────
CLI_IMPLEMENTED_GROUPS: Final[tuple[str, ...]] = ("hook", "state", "event")
CLI_NOT_IMPLEMENTED: Final[tuple[tuple[str, ...], ...]] = (
    ("daemon",),
    ("ui",),
    ("event", "complete", "evt_contract_probe"),
)

# ── Engine process_event ─────────────────────────────────────────────────────
ENGINE_PROCESS_EVENT_ARITY: Final = 3  # (event, matched_hooks, executions)
