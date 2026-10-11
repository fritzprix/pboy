# Pageboy (`pboy` / `pb`)

> Zero-Ops 에이전트 이벤트 오케스트레이터 & 디스패처 (**Alpha**)

Slack, Telegram, Jira, Confluence 이벤트를 CloudEvents v1.0으로 정규화하고, 매칭된 훅을 로컬 subprocess 하네스로 실행하는 도구입니다.

> **Alpha 상태:** 코어(모델/스토어/정규화/라우팅/하네스 실행/부분 CLI)만 동작합니다.
> `daemon`, `ui`, `event complete`, 채널 어댑터는 아직 미구현이며 호출 시 non-zero로 실패합니다.

## 현재 동작하는 것

| 기능 | 상태 |
|------|------|
| `pboy hook register/list/delete` | ✅ |
| `pboy state put/get` | ✅ |
| `pboy event list/get` | ✅ |
| Engine: normalize → store → match → lease → subprocess | ✅ |
| AES-256-GCM secret storage | ✅ |
| `pboy daemon` + `/healthz` + `POST /v1/events` | ✅ |
| `pboy ui` / `event complete` | ❌ 미구현 (명시적 실패) |
| 채널 수신 (Slack/Telegram/Jira) | ❌ 미구현 |

Production 로드맵·백로그: [`ROADMAP.md`](ROADMAP.md) · [GitHub Issues](https://github.com/fritzprix/pboy/issues)

## 설치

```bash
# 권장: uv
uv sync --extra dev

# 또는 pip + venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

데이터/키 기본 경로: `~/.local/share/pboy/pboy.db`, `~/.config/pboy/secret.key`  
환경변수: `PB_DATABASE_URL`, `PB_SECRET_KEY`, `PB_DAEMON_URL` 등 (`PB_` prefix).

## 테스트 실행

```bash
# 전체
.venv/bin/pytest -q
# 또는: uv run pytest -q

# 계약만
.venv/bin/pytest -m contract -v

# 커버리지
.venv/bin/pytest --cov=pboy --cov-report=term-missing -q

# 린트 / 타입
.venv/bin/ruff check src tests
.venv/bin/mypy src
```

상세: [`tests/README.md`](tests/README.md)

## 데몬 시작

```bash
pboy daemon --host 127.0.0.1 --port 8080
# 다른 터미널:
curl -sf http://127.0.0.1:8080/healthz
```

```bash
# 이벤트 주입 (daemon 실행 중)
curl -sX POST http://127.0.0.1:8080/v1/events \
  -H 'content-type: application/json' \
  -d '{"source":"jira","payload":{...},"dispatch":true}'
```

채널 수신은 아직 없습니다 (`ROADMAP.md` M2).

## 수동으로 돌려보기 (Alpha)

CLI로 훅을 등록한 뒤, 라이브러리 `Engine`으로 이벤트를 넣으면 하네스가 실행됩니다.

```bash
# 1) 훅 등록 — exec 는 로컬에서 실제로 존재하는 명령
pboy hook register \
  --name "echo-demo" \
  --topic "jira:issue.created" \
  --filter '.data.priority == "High"' \
  --exec 'python3 -c "import sys,json; print(json.load(sys.stdin)[\"id\"])"' \
  --concurrency 1 \
  --timeout 30

pboy hook list
```

```bash
# 2) 샘플 이벤트 주입 (Engine)
.venv/bin/python - <<'PY'
import asyncio
from pboy.core.engine import Engine

payload = {
    "webhookEvent": "jira:issue_created",
    "issue": {
        "id": "1",
        "key": "DEMO-1",
        "fields": {
            "summary": "demo",
            "priority": {"name": "High"},
            "project": {"key": "DEMO"},
            "creator": {"accountId": "u1", "displayName": "Demo"},
            "labels": [],
        },
    },
}

async def main() -> None:
    engine = Engine()
    await engine.start()
    try:
        event, matched, executions = await engine.process_event(payload, "jira")
        print("event:", event.id)
        print("matched:", [h.name for h in matched])
        for ex in executions:
            print("exec:", ex.status, ex.exit_code, (ex.stdout or "").strip())
    finally:
        await engine.stop()

asyncio.run(main())
PY
```

```bash
# 3) 조회
pboy event list
pboy event get <event_id>
```

하네스 안에서 상태 보고:

```bash
pboy state put --event-id "$PB_EVENT_ID" --status running --progress 50
pboy state get "$PB_EVENT_ID"
```

## 프로젝트 구조

```
pboy/
├── src/pboy/
│   ├── cli/         # Typer CLI
│   ├── core/        # Normalizer, Router, Lease, Engine, filters
│   ├── channels/    # (planned) inbound adapters
│   ├── store/       # SQLite + AES-GCM secrets
│   ├── harness/     # Subprocess executor
│   ├── ui/          # (planned) local dashboard
│   └── models/      # CloudEvents / Hook / Execution
├── tests/
├── pyproject.toml
└── README.md
```

## 필터 문법

지원: `.path.to.field == "value"` / `!=`  
지원하지 않는 표현식은 **매칭하지 않습니다** (fail-closed).

## License

Apache-2.0
