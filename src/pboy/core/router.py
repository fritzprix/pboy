"""Hook router: matches incoming CloudEvents against registered hooks."""

from __future__ import annotations

import fnmatch

from pboy.core.filters import evaluate_simple_filter
from pboy.models.cloudevent import TYPE_TO_TOPIC, CloudEvent
from pboy.models.hook import Hook


class HookRouter:
    """Routes CloudEvents to matched hooks based on topic and filter expressions."""

    def match(self, event: CloudEvent, hooks: list[Hook]) -> list[Hook]:
        """Return list of active hooks matching the given event."""
        matched: list[Hook] = []
        event_topic = TYPE_TO_TOPIC.get(event.type, f"{event.source}:{event.type}")
        event_dict = event.model_dump()

        for hook in hooks:
            if hook.status != "active":
                continue

            # Topic matching (supports exact match, wildcard *, or event.type match)
            if not self._topic_matches(hook.topic, event_topic, event.type):
                continue

            # Filter matching
            if hook.filter and not evaluate_simple_filter(hook.filter, event_dict):
                continue

            matched.append(hook)

        return matched

    def _topic_matches(self, hook_topic: str, event_topic: str, event_type: str) -> bool:
        if hook_topic in ("*", event_topic, event_type):
            return True
        if fnmatch.fnmatch(event_topic, hook_topic):
            return True
        return bool(fnmatch.fnmatch(event_type, hook_topic))
