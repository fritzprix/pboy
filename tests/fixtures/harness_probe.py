#!/usr/bin/env python3
"""Probe harness used by contract tests.

Reads CloudEvent JSON from stdin and prints a JSON report of stdin + PB_* env.
"""

from __future__ import annotations

import json
import os
import sys

REQUIRED_ENV = (
    "PB_EVENT_ID",
    "PB_SOURCE",
    "PB_HOOK_NAME",
)
OPTIONAL_ENV = (
    "PB_LEASE_TOKEN",
)


def main() -> None:
    stdin_raw = sys.stdin.read()
    event = json.loads(stdin_raw) if stdin_raw.strip() else None
    env = {key: os.environ[key] for key in REQUIRED_ENV if key in os.environ}
    for key in OPTIONAL_ENV:
        if key in os.environ:
            env[key] = os.environ[key]
    json.dump({"stdin": event, "env": env}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
