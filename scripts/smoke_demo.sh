#!/usr/bin/env bash
# Operator smoke: install → daemon → hook via HTTP → inject event → harness runs.
# Uses an ephemeral DB under $TMPDIR so the default ~/.local/share/pboy is untouched.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/pboy" ]]; then
  PBOY="$ROOT/.venv/bin/pboy"
  PYTHON="$ROOT/.venv/bin/python"
elif command -v pboy >/dev/null 2>&1; then
  PBOY="$(command -v pboy)"
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="$(command -v python3)"
  fi
else
  echo "pboy not found. Run: uv sync --extra dev  (or pip install -e '.[dev]')" >&2
  exit 1
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/pboy-smoke.XXXXXX")"
DAEMON_PID=""

cleanup() {
  if [[ -n "${DAEMON_PID}" ]] && kill -0 "$DAEMON_PID" 2>/dev/null; then
    kill "$DAEMON_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$DAEMON_PID" 2>/dev/null || break
      sleep 0.05
    done
    if kill -0 "$DAEMON_PID" 2>/dev/null; then
      kill -9 "$DAEMON_PID" 2>/dev/null || true
    fi
    wait "$DAEMON_PID" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

pick_port() {
  if [[ -n "${PB_SMOKE_PORT:-}" ]]; then
    echo "$PB_SMOKE_PORT"
    return
  fi
  "$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

export PB_AUTH_TOKEN="${PB_AUTH_TOKEN:-smoke-token}"
export PB_DATABASE_URL="$WORK/smoke.db"
export PB_SECRET_KEY="smoke-secret-key-32bytes-long!!"
HOST="127.0.0.1"
PORT="$(pick_port)"
export PB_DAEMON_URL="http://${HOST}:${PORT}"

echo "==> starting daemon on $PB_DAEMON_URL (db=$PB_DATABASE_URL)"
"$PBOY" daemon --host "$HOST" --port "$PORT" --db "$PB_DATABASE_URL" >"$WORK/daemon.log" 2>&1 &
DAEMON_PID=$!

for _ in $(seq 1 50); do
  if curl -sf "$PB_DAEMON_URL/healthz" >/dev/null; then
    break
  fi
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    echo "daemon exited early; log:" >&2
    cat "$WORK/daemon.log" >&2
    exit 1
  fi
  sleep 0.1
done
curl -sf "$PB_DAEMON_URL/healthz" | grep -q '"status":"ok"'

echo "==> register hook via CLI (daemon HTTP)"
"$PBOY" hook delete smoke-echo >/dev/null 2>&1 || true
"$PBOY" hook register \
  --name smoke-echo \
  --topic 'jira:issue.created' \
  --exec 'echo smoke-ok' \
  --timeout 30

HOOKS="$("$PBOY" hook list)"
echo "$HOOKS" | grep -q smoke-echo

echo "==> inject event"
INJECT="$(curl -sf -X POST "$PB_DAEMON_URL/v1/events" \
  -H 'content-type: application/json' \
  -H "X-PB-Token: $PB_AUTH_TOKEN" \
  -d '{
    "source": "jira",
    "dispatch": true,
    "payload": {
      "webhookEvent": "jira:issue_created",
      "issue": {
        "id": "1",
        "key": "SMOKE-1",
        "fields": {
          "summary": "smoke",
          "priority": {"name": "High"},
          "project": {"key": "SMOKE"},
          "creator": {"accountId": "u1", "displayName": "Smoke"},
          "labels": []
        }
      }
    }
  }')"

EVENT_ID="$(printf '%s' "$INJECT" | "$PYTHON" -c '
import json, sys
body = json.load(sys.stdin)
assert body.get("matched_hooks") == ["smoke-echo"], body
stdout = (body.get("executions") or [{}])[0].get("stdout") or ""
assert "smoke-ok" in stdout, body
print(body["event_id"])
')"
"$PBOY" event list | grep -q "$EVENT_ID"

echo "==> OK — daemon + hook + harness path works (event_id=$EVENT_ID)"
