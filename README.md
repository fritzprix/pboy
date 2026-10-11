# Pageboy (`pboy` / `pb`)

> Zero-Ops 에이전트 이벤트 오케스트레이터 & 디스패처 (**Alpha**)

Telegram·Slack 등 **wake-up 채널** 이벤트를 CloudEvents v1.0으로 정규화하고, 매칭된 훅(에이전트 harness)을 로컬 subprocess로 실행하는 도구입니다.  
Jira/Confluence는 데몬 webhook이 아니라 **에이전트가 REST로 pull/update**하는 작업면으로 둡니다 ([`docs/architecture-channels.md`](docs/architecture-channels.md)).

> **Alpha 상태:** 코어 + 데몬 + Telegram/Slack wake-up + Telegram outbound + `/ui` 대시보드(채널·비밀 폼 포함)까지 동작합니다.
> Jira는 훅 exec 프로그램의 REST 작업면(웹훅 ChannelAdapter 아님).

## 현재 동작하는 것

| 기능 | 상태 |
|------|------|
| `pboy hook register/list/delete` | ✅ (데몬 모드 시 HTTP) |
| `pboy state put/get` | ✅ (데몬 모드 시 HTTP) |
| `pboy event list/get` | ✅ (데몬 모드 시 HTTP) |
| Engine: normalize → store → match → lease → subprocess | ✅ |
| AES-256-GCM secret storage | ✅ |
| `pboy daemon` + `/healthz` + `/v1/events` + `/v1/hooks` | ✅ |
| Daemon auth (`PB_AUTH_TOKEN` / `X-PB-Token`) | ✅ |
| `pboy event complete` (+ `--sync telegram`) | ✅ |
| `pboy daemon --ui` / `pboy ui` | ✅ 대시보드 + 채널/비밀 폼 (`/ui`, `/ui/settings`) |
| Channel adapter registry (`PB_CHANNELS_ENABLED`) | ✅ |
| Telegram long-poll inbound | ✅ (`PB_TELEGRAM_BOT_TOKEN` / secret) |
| `pboy channel list/enable/disable/set-secret` | ✅ |
| Slack Socket Mode wake-up | ✅ (`PB_SLACK_*` / secrets) |
| Jira/Confluence ChannelAdapter | ⏸ deferred — agent REST instead |

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
환경변수: `PB_DATABASE_URL`, `PB_SECRET_KEY`, `PB_DAEMON_URL`, `PB_CHANNELS_ENABLED`, `PB_TELEGRAM_BOT_TOKEN` 등 (`PB_` prefix).

Telegram 설정: [`src/pboy/channels/README.md`](src/pboy/channels/README.md)

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

## 한 번에 검증 (권장)

데몬 HTTP 경로가 실제로 도는지 ephemeral DB로 확인합니다 (기본 `~/.local/share/pboy`는 건드리지 않음):

```bash
./scripts/smoke_demo.sh
# 성공 시: OK — daemon + hook + harness path works
```

스키마 migration은 없습니다. 로컬 DB를 리셋하려면 파일을 지우세요:

```bash
rm -f ~/.local/share/pboy/pboy.db
```

## 데몬으로 수동 실행

운영에 가까운 경로입니다. CLI는 **`PB_DAEMON_URL`이 있을 때만** 데몬 HTTP를 쓰고, 없으면 별도 로컬 SQLite로 갑니다 (split-brain 주의).

```bash
export PB_AUTH_TOKEN=dev-secret
export PB_DAEMON_URL=http://127.0.0.1:8080

# 터미널 1 — API + 선택적 대시보드
pboy daemon --host 127.0.0.1 --port 8080 --ui
# 브라우저: http://127.0.0.1:8080/ui

# 터미널 2
curl -sf "$PB_DAEMON_URL/healthz"

pboy hook register \
  --name echo-demo \
  --topic 'jira:issue.created' \
  --exec 'echo ok' \
  --timeout 30

curl -sX POST "$PB_DAEMON_URL/v1/events" \
  -H 'content-type: application/json' \
  -H "X-PB-Token: $PB_AUTH_TOKEN" \
  -d '{
    "source":"jira",
    "dispatch":true,
    "payload":{
      "webhookEvent":"jira:issue_created",
      "issue":{
        "id":"1","key":"DEMO-1",
        "fields":{
          "summary":"demo",
          "priority":{"name":"High"},
          "project":{"key":"DEMO"},
          "creator":{"accountId":"u1","displayName":"Demo"},
          "labels":[]
        }
      }
    }
  }'

pboy event list
```

## Telegram inbound

```bash
pboy channel set-secret telegram --value '<BotFather token>'
pboy channel enable telegram

export PB_AUTH_TOKEN=dev-secret
pboy daemon --host 127.0.0.1 --port 8080

# 다른 터미널 — 훅 등록 후 봇에게 메시지
export PB_DAEMON_URL=http://127.0.0.1:8080
pboy hook register --name tg-echo --topic 'telegram:message' --exec 'echo hi' --timeout 30
pboy channel list
```

상세·BotFather 절차: [`src/pboy/channels/README.md`](src/pboy/channels/README.md). Slack/Jira inbound는 M4.

완료 후 텔레그램 답장:

```bash
pboy event complete "$PB_EVENT_ID" --summary 'Resolved' --sync telegram
pboy event get "$PB_EVENT_ID"   # completion / execution_state 포함
```

## 프로젝트 구조

```
pboy/
├── src/pboy/
│   ├── cli/         # Typer CLI
│   ├── core/        # Normalizer, Router, Lease, Engine, filters
│   ├── channels/    # Telegram long-poll (+ fake); Slack later
│   ├── store/       # SQLite + AES-GCM secrets
│   ├── harness/     # Subprocess executor
│   ├── ui/          # embedded read-only dashboard (HTMX)
│   └── models/      # CloudEvents / Hook / Execution
├── scripts/smoke_demo.sh
├── tests/
├── pyproject.toml
└── README.md
```

## 필터 문법

지원: `.path.to.field == "value"` / `!=`  
지원하지 않는 표현식은 **매칭하지 않습니다** (fail-closed).

## License

Apache-2.0
