# Pageboy (`pboy` / `pb`)

> Zero-Ops 에이전트 이벤트 오케스트레이터 & 디스패처 (**Alpha**)

Telegram·Slack 등 **wake-up 채널** 이벤트를 CloudEvents v1.0으로 정규화하고, 매칭된 훅(`exec` 서브프로세스)을 로컬에서 실행합니다.  
Jira/Confluence는 데몬 webhook이 아니라 **훅이 실행하는 프로그램이 REST로 다루는 작업면**입니다 ([`docs/architecture-channels.md`](docs/architecture-channels.md)).

> **Alpha:** 코어 · 데몬 · Telegram/Slack wake-up · Telegram outbound · 로컬 `/ui` 대시보드(채널·비밀 폼)까지 동작합니다.  
> 다음 로드맵은 packaging / security / ops docs (**M6**).

## 현재 동작하는 것

| 기능 | 상태 |
|------|------|
| `pboy hook` / `state` / `event` CLI | ✅ (`PB_DAEMON_URL` 있으면 데몬 HTTP) |
| Engine: normalize → store → match → lease → subprocess | ✅ |
| AES-256-GCM secret storage | ✅ |
| `pboy daemon` + `/healthz` + `/v1/*` | ✅ |
| Daemon auth (`PB_AUTH_TOKEN` / `X-PB-Token`) | ✅ |
| `pboy event complete` (+ `--sync telegram`) | ✅ |
| `pboy daemon --ui` / `pboy ui` | ✅ `/ui` 한눈에 · `/ui/settings` 채널·비밀 · `/ui/login` |
| Telegram long-poll | ✅ UI 또는 store / env |
| Slack Socket Mode | ✅ UI 또는 store / env (`bot_token` + `app_token`) |
| `pboy channel list/enable/disable/set-secret` | ✅ |
| Jira/Confluence ChannelAdapter | ⏸ deferred — agent REST instead |

로드맵: [`ROADMAP.md`](ROADMAP.md) · [Issues](https://github.com/fritzprix/pboy/issues)

## 빠른 시작 (UI 권장)

채널 토큰은 **환경변수 없이** 브라우저에서 넣을 수 있습니다.

```bash
uv sync --extra dev

# 선택·권장: 대시보드 저장/원격 접근용 비밀번호
export PB_AUTH_TOKEN=dev-secret

pboy ui
# 같음: pboy daemon --host 127.0.0.1 --port 8080 --ui
```

브라우저:

1. [http://127.0.0.1:8080/ui](http://127.0.0.1:8080/ui) — 훅 · 이벤트 · 실행 기록  
2. [http://127.0.0.1:8080/ui/settings](http://127.0.0.1:8080/ui/settings) — Telegram / Slack 토큰 붙여넣기 · 채널 켜기  
   - 단계 안내 · 공식 링크 · 입력란 tooltip 포함  
   - 저장한 비밀값은 다시 보여 주지 않음 (`저장됨`만 표시)  
3. `PB_AUTH_TOKEN`을 켠 경우 저장 전에 [/ui/login](http://127.0.0.1:8080/ui/login) (비밀번호 = 그 토큰)

Jira는 wake-up 채널이 아닙니다. 설정 화면의 안내대로 Atlassian API 토큰을 **선택** 보관할 수 있고, 실제 호출은 훅 `exec` 프로그램이 합니다.

## 설치

```bash
uv sync --extra dev

# 또는
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

| 항목 | 기본 |
|------|------|
| DB | `~/.local/share/pboy/pboy.db` |
| 암호화 키 | `~/.config/pboy/secret.key` |

환경변수는 **선택**입니다 (`PB_` prefix). 채널 토큰·enable은 UI/store가 우선 경로이고, env가 있으면 덮어쓸 수 있습니다.

| 변수 | 용도 |
|------|------|
| `PB_AUTH_TOKEN` | 데몬 변경 API · UI 로그인 (권장) |
| `PB_DAEMON_URL` | CLI가 데몬 HTTP를 쓰게 함 (예: `http://127.0.0.1:8080`) |
| `PB_TELEGRAM_BOT_TOKEN` / `PB_SLACK_*` | 채널 토큰 (UI `set-secret` 대신) |
| `PB_CHANNELS_ENABLED` | 켜진 채널 목록 (UI enable 대신; 설정 시 store 갱신 제한) |
| `PB_DATABASE_URL` / `PB_SECRET_KEY` | DB·암호화 키 경로 오버라이드 |

채널·BotFather·Slack 앱 절차: [`src/pboy/channels/README.md`](src/pboy/channels/README.md)  
UI 구조: [`src/pboy/ui/README.md`](src/pboy/ui/README.md)

## 테스트

```bash
.venv/bin/pytest -q
.venv/bin/pytest -m contract -v
.venv/bin/ruff check src tests
.venv/bin/mypy src

# 데몬 HTTP 경로 smoke (기본 DB 미사용)
./scripts/smoke_demo.sh
```

스키마 migration은 없습니다. 로컬 DB 리셋:

```bash
rm -f ~/.local/share/pboy/pboy.db
```

## 데몬 + CLI (수동)

CLI는 **`PB_DAEMON_URL`이 있을 때만** 데몬 HTTP를 쓰고, 없으면 별도 로컬 SQLite로 갑니다 (split-brain 주의).

```bash
export PB_AUTH_TOKEN=dev-secret
export PB_DAEMON_URL=http://127.0.0.1:8080

# 터미널 1
pboy daemon --host 127.0.0.1 --port 8080 --ui

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

## Telegram / Slack (CLI)

UI 대신 CLI로도 동일하게 설정할 수 있습니다.

```bash
# Telegram
pboy channel set-secret telegram --value '<BotFather token>'
pboy channel enable telegram

# Slack (둘 다 필요)
pboy channel set-secret slack --secret-name bot_token --value 'xoxb-...'
pboy channel set-secret slack --secret-name app_token --value 'xapp-...'
pboy channel enable slack

export PB_AUTH_TOKEN=dev-secret
pboy daemon --host 127.0.0.1 --port 8080 --ui

export PB_DAEMON_URL=http://127.0.0.1:8080
pboy hook register --name tg-echo --topic 'telegram:message' --exec 'echo hi' --timeout 30
pboy channel list
```

완료 후 텔레그램 답장:

```bash
pboy event complete "$PB_EVENT_ID" --summary 'Resolved' --sync telegram
pboy event get "$PB_EVENT_ID"
```

## 프로젝트 구조

```
pboy/
├── src/pboy/
│   ├── cli/         # Typer CLI
│   ├── core/        # Normalizer, Router, Lease, Engine, filters
│   ├── channels/    # Telegram long-poll, Slack Socket Mode, fake
│   ├── daemon/      # FastAPI app + auth
│   ├── store/       # SQLite + AES-GCM secrets
│   ├── harness/     # Subprocess executor
│   ├── outbound/    # Telegram completion sync
│   ├── ui/          # Embedded dashboard + settings (HTMX/Jinja)
│   └── models/      # CloudEvents / Hook / Execution
├── docs/architecture-channels.md
├── scripts/smoke_demo.sh
├── tests/
├── ROADMAP.md
└── README.md
```

## 필터 문법

지원: `.path.to.field == "value"` / `!=`  
지원하지 않는 표현식은 **매칭하지 않습니다** (fail-closed).

## License

Apache-2.0
