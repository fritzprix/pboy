# CLI — `pboy` 명령어

`pboy.cli` — Typer 기반 CLI 진입점

## 모듈 구조

| 파일 | 설명 |
|------|------|
| `main.py` | CLI 진입점 (`pboy`/`pb` 명령어), 그룹 정의 |
| `daemon.py` | 데몬 실행 (`pboy daemon --ui`) |
| `hook.py` | 훅 관리 (`pboy hook register/list/pause/delete`) |
| `state.py` | 상태 보고 (`pboy state put/get`) |
| `event.py` | 이벤트 완료 (`pboy event complete/get/list`) |
| `ui.py` | 로컬 웹 UI (`pboy ui`) |

## 명령어 계층

```
pboy
├── daemon
│   ├── --ui               # 웹 UI 내장 실행
│   ├── --port 8080
│   └── --db sqlite:///...
├── hook
│   ├── register           # 훅 등록 (--name, --topic, --filter, --exec)
│   ├── list               # 훅 목록
│   ├── pause <name>       # 일시정지
│   ├── resume <name>      # 재개
│   └── delete <name>      # 삭제
├── state
│   ├── put                # 상태 보고 (--event-id, --status, --progress, --broadcast)
│   └── get                # 상태 조회
├── event
│   ├── complete           # 이벤트 완료 (--summary, --artifact, --sync)
│   ├── get                # 이벤트 상세
│   └── list               # 이벤트 로그
└── ui                     # 웹 UI 단일 명령어
```

## 사용 패턴

```bash
# 1. 데몬 시작 (웹 UI 내장)
uvx pboy daemon --ui

# 2. 훅 등록
pboy hook register \
  --name "incident-resolver" \
  --topic "jira:issue.created" \
  --filter '.data.priority == "High"' \
  --exec "python3 /workspace/agents/harness.py" \
  --concurrency 2 \
  --timeout 180s

# 3. 하네스 내부에서 상태 보고
pboy state put --event-id "$PB_EVENT_ID" --status running --progress 50 --broadcast

# 4. 완료 처리 및 멀티 채널 동시 회신
pboy event complete "$PB_EVENT_ID" --summary "Resolved" --sync "jira:comment,slack:thread"
```
