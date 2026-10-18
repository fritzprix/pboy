# CLI — `pboy` (`pb`) 명령어

`pboy.cli` — Typer 기반 CLI 진입점 (데몬 HTTP API 자동 연결 및 로컬 SQLite 폴백)

## 모듈 구조

| 파일 | 설명 |
|------|------|
| `main.py` | CLI 진입점 (`pboy`/`pb` 명령어), `daemon` / `ui` / `status` / `hook` / `state` / `event` / `channel` 그룹 |
| `daemon_client.py` | 실행 중인 데몬(`http://127.0.0.1:8080` 자동 감지 또는 `--daemon-url`)의 HTTP API(`/healthz`, `/v1/*`)를 호출하는 동기 클라이언트 |

## 데몬 자동 연결 (`--daemon-url`) 및 하네스 환경변수

- **데몬 자동 감지**: 데몬(`pboy ui` 또는 `pboy daemon`)이 기본 주소(`http://127.0.0.1:8080`)에서 실행 중이면, 별도 옵션이나 환경변수 없이도 CLI 명령(`hook`, `channel`, `event`, `state`, `status`)이 **자동으로 데몬 HTTP API에 연결**됩니다.
  - 데몬을 다른 포트로 띄웠다면 `--daemon-url http://127.0.0.1:9000` (단축 `-u`) 옵션을 전달합니다.
  - 데몬이 실행 중이지 않거나 `PB_DATABASE_URL`로 별도 로컬 DB를 지정한 경우에만 로컬 SQLite 파일에 직접 접근합니다.
- **Allowlist 적용 범위 (`telegram.allowlist` vs `event inject`)**:
  - `telegram.allowlist`는 **Telegram 인바운드 폴링 어댑터(`TelegramChannel`)에서만** 발신자(`chat.id` / `from.id`)를 필터링합니다.
  - `pboy event inject` 및 `POST /v1/events`는 로컬 운영자/에이전트가 엔진(`Engine.process_event`)에 직접 주입하는 경로이므로 **채널 allowlist 검사를 거치지 않고 우회(bypass)**합니다.
- **훅 서브프로세스 (하네스 내부)**:
  - `stdin`: 정규화된 CloudEvent v1.0 JSON 전체가 전달됨
  - 환경변수 자동 주입: `PB_EVENT_ID`, `PB_SOURCE`, `PB_HOOK_NAME` (및 리스 발급 시 `PB_LEASE_TOKEN`)
  - 따라서 훅 내부에서 `pboy state put`, `pboy event get`, `pboy event complete` 호출 시 `event_id` 인자를 생략하면 자동으로 `$PB_EVENT_ID`를 사용합니다.

## 명령어 계층

```text
pboy [--daemon-url / -u <url>]
├── daemon                 # 로컬 데몬 HTTP API 실행 (--ui, --host, --port, --db)
├── ui                     # 데몬 + 웹 대시보드(/ui, /ui/settings) 실행
├── status                 # 데몬(/healthz) 또는 로컬 스토어/채널 상태 JSON 출력
├── hook
│   ├── register           # 훅 등록 (--name, --topic, --exec, --filter, --concurrency, --timeout)
│   ├── template           # 핸들러 스캐폴드 출력 (--lang python|bash, [--topic])
│   ├── list               # 훅 목록 조회
│   └── delete <name>      # 훅 삭제
├── state
│   ├── put                # 진행 상태 보고 (--status, [--event-id], --progress)
│   └── get [<event_id>]   # 상태 JSON 조회 (생략 시 $PB_EVENT_ID)
├── event
│   ├── inject             # 플랫폼 이벤트 수동 주입/테스트 (--source, --payload, --dispatch/--no-dispatch)
│   ├── list               # 최근 이벤트 목록 (--limit)
│   ├── get [<event_id>]   # 이벤트 상세 + 완료/실행 기록 JSON 조회 (--verbose 시 stdout/stderr 포함)
│   └── complete [<id>]    # 이벤트 완료 및 아웃바운드 회신 (--summary, --sync, --file-path, --file-id, --file-type)
└── channel
    ├── list               # 채널 목록 및 활성화/실행/시크릿 상태
    ├── enable <name>      # 채널 활성화 (telegram, slack 등)
    ├── disable <name>     # 채널 비활성화
    └── set-secret <name>  # 채널 시크릿 저장 (--value, --secret-name: bot_token | app_token | allowlist)
```

## 에이전트 / 운영자 사용 패턴

```bash
# 1. 데몬 + UI 시작 (기본 127.0.0.1:8080)
uvx pboy ui

# 2. 다른 셸에서 상태 확인 (127.0.0.1:8080 데몬에 자동 연결)
uvx pboy status

# 3. 텔레그램 토큰 + 인바운드 allowlist 설정 및 채널 활성화
uvx pboy channel set-secret telegram --value '<bot-token>'
uvx pboy channel set-secret telegram --secret-name allowlist --value '123456789'
uvx pboy channel enable telegram

# 4. 훅 등록 (--timeout 은 정수 초 단위)
uvx pboy hook register \
  --name "tg-responder" \
  --topic "telegram:message" \
  --filter '.data.summary != "ignore"' \
  --exec "python3 /workspace/agents/harness.py" \
  --concurrency 2 \
  --timeout 180

# 5. 수동 이벤트 주입 (Dry-run 매칭 확인 시 --no-dispatch 권장; 생략 시 기본값 --dispatch 로 실제 훅이 실행됨)
#    주의: event inject 는 Telegram 인바운드 allowlist 를 거치지 않고 엔진으로 직접 주입됩니다.
uvx pboy event inject \
  --source telegram \
  --payload '{"message":{"message_id":1,"chat":{"id":123456789},"from":{"id":123456789,"username":"alice"},"text":"ping"}}' \
  --no-dispatch

# 6. 훅(하네스) 내부에서 상태 보고 및 완료 회신 ($PB_EVENT_ID 자동 사용, 기본 데몬 자동 연결)
pboy state put --status running --progress 50
pboy event complete --summary "Resolved" --sync telegram --file-path ./report.txt --file-type document
```
