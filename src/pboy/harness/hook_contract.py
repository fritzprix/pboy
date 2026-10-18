"""Shared hook stdin/env contract helpers for CLI tips and scaffolds."""

from __future__ import annotations

from pboy.models.cloudevent import TOPIC_TO_TYPE

# Normalized EventData keys every hook should prefer over platform-raw fields.
NORMALIZED_DATA_KEYS: tuple[str, ...] = (
    "summary",
    "channel_id",
    "entity_key",
    "thread_id",
    "actor",
    "attachments",
    "extra",
)

_SOURCE_BY_TOPIC_PREFIX: dict[str, str] = {
    "telegram": "telegram",
    "slack": "slack",
    "jira": "jira",
    "confluence": "confluence",
}

_TEMPLATE_PYTHON = '''\
#!/usr/bin/env python3
"""Pageboy hook scaffold — CloudEvent on stdin, PB_* env, reply via CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> None:
    event_id = os.environ["PB_EVENT_ID"]
    source = os.environ.get("PB_SOURCE", "")
    hook_name = os.environ.get("PB_HOOK_NAME", "")
    event = json.load(sys.stdin)
    data = event.get("data") or {}

    summary = data.get("summary") or ""
    channel_id = data.get("channel_id")
    actor = (data.get("actor") or {}).get("display_name")

    # Branch on source when the hook topic is a wildcard or covers several channels.
    if source == "telegram":
        pass  # e.g. attachments = data.get("attachments") or []
    elif source == "slack":
        pass  # e.g. thread_id = data.get("thread_id"); ts = (data.get("extra") or {}).get("ts")
    elif source in {"jira", "confluence"}:
        # No chat reply — use: pboy event complete --summary '…'
        print(f"[{hook_name}] {source} entity={data.get('entity_key')} summary={summary!r}")
        return

    reply = f"ack from {hook_name}: {summary}" if summary else f"ack from {hook_name}"
    # event reply picks --sync telegram|slack from the event source (needs channel_id).
    subprocess.run(
        ["pboy", "event", "reply", "--summary", reply],
        check=False,
    )
    # Unused but available: event_id, channel_id, actor
    _ = (event_id, channel_id, actor)


if __name__ == "__main__":
    main()
'''

_TEMPLATE_BASH = '''\
#!/usr/bin/env bash
# Pageboy hook scaffold — CloudEvent on stdin, PB_* env, reply via CLI.
set -euo pipefail

: "${PB_EVENT_ID:?PB_EVENT_ID is required}"
SOURCE="${PB_SOURCE:-}"
HOOK_NAME="${PB_HOOK_NAME:-hook}"

EVENT_JSON=$(cat)
SUMMARY=$(printf '%s' "$EVENT_JSON" | jq -r '.data.summary // empty')
CHANNEL_ID=$(printf '%s' "$EVENT_JSON" | jq -r '.data.channel_id // empty')

case "$SOURCE" in
  telegram)
    # attachments: jq -c '.data.attachments // []'
    ;;
  slack)
    # thread: jq -r '.data.thread_id // empty'; ts: jq -r '.data.extra.ts // empty'
    ;;
  jira|confluence)
    # No chat reply — complete without --sync (or omit reply).
    echo "[$HOOK_NAME] $SOURCE entity=$(printf '%s' "$EVENT_JSON" | jq -r '.data.entity_key // empty') summary=$SUMMARY" >&2
    exit 0
    ;;
esac

if [[ -n "$SUMMARY" ]]; then
  REPLY="ack from ${HOOK_NAME}: ${SUMMARY}"
else
  REPLY="ack from ${HOOK_NAME}"
fi
# channel_id is used by outbound reply routing; keep it on the event (do not invent).
pboy event reply --summary "$REPLY"
: "${CHANNEL_ID}"
'''


def expected_source_for_topic(topic: str) -> str | None:
    """Return PB_SOURCE for an exact topic, or None for wildcards / unknown."""
    if "*" in topic or "?" in topic or "[" in topic:
        return None
    if topic in TOPIC_TO_TYPE:
        return topic.split(":", 1)[0]
    prefix = topic.split(":", 1)[0]
    return _SOURCE_BY_TOPIC_PREFIX.get(prefix)


def expected_type_for_topic(topic: str) -> str | None:
    """Return CloudEvent ``type`` for a known topic, else None."""
    return TOPIC_TO_TYPE.get(topic)


def format_register_contract_hint(*, name: str, topic: str) -> str:
    """Multi-line tip printed after a successful ``hook register``."""
    source = expected_source_for_topic(topic)
    event_type = expected_type_for_topic(topic)
    lines = [
        f"Hook '{name}' registered for topic '{topic}'.",
        "  Contract: stdin = CloudEvent v1.0 JSON; env PB_EVENT_ID, PB_SOURCE, PB_HOOK_NAME",
    ]
    if source and event_type:
        lines.append(f"  For this topic: PB_SOURCE='{source}', type='{event_type}'")
    elif source:
        lines.append(f"  For this topic: PB_SOURCE='{source}' (type varies)")
    else:
        lines.append(
            "  Wildcard topic: branch on os.environ['PB_SOURCE'] "
            "(telegram|slack|jira|confluence)"
        )
    lines.append(
        "  Prefer .data.summary, .data.channel_id, .data.actor, .data.entity_key "
        "(not platform-raw JSON)"
    )
    lines.append("  Reply (telegram/slack): pboy event reply --summary '<text>'")
    lines.append("  Scaffold: pboy hook template --lang python")
    return "\n".join(lines)


def render_hook_template(lang: str, *, topic: str | None = None) -> str:
    """Return a hook script scaffold for ``lang`` (``python`` or ``bash``)."""
    normalized = lang.strip().lower()
    if normalized in {"python", "py", "python3"}:
        body = _TEMPLATE_PYTHON
    elif normalized in {"bash", "sh", "shell"}:
        body = _TEMPLATE_BASH
    else:
        raise ValueError(f"Unsupported language '{lang}' (use python or bash)")

    header_bits = [
        f"# topic hint: {topic}" if topic else None,
        "# Matching uses topic / CloudEvent type — not PB_SOURCE alone.",
        "# Inspect a live payload: pboy event get <id> --verbose",
        "# Dry-run match: pboy event inject --source <src> --payload '…' --no-dispatch",
    ]
    header = "\n".join(bit for bit in header_bits if bit)
    if body.startswith("#!"):
        shebang, _, rest = body.partition("\n")
        return f"{shebang}\n{header}\n{rest}"
    return f"{header}\n{body}"


__all__ = [
    "NORMALIZED_DATA_KEYS",
    "expected_source_for_topic",
    "expected_type_for_topic",
    "format_register_contract_hint",
    "render_hook_template",
]
