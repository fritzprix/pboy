# UI — `pboy.ui`

`pboy.ui` — 임베디드 로컬 웹 대시보드

## 모듈 구조

| 파일 | 설명 |
|------|------|
| `app.py` | FastAPI 앱 (로컬 대시보드) |
| `routes.py` | REST API 라우트 (훅, 이벤트, 실행 이력) |
| `console.py` | Live Console (stdout/stderr 실시간 스트리밍) |

## 기능

### 대시보드 (`localhost:8080`)

- **Integrations 탭**: Jira/Confluence 고정 웹훅 URL 발급, Slack/TG 토큰 입력
- **Hooks 탭**: 이벤트 필터 (JQ 문법), 트리거 대상 하네스 경로, 동시 실행 수, 타임아웃
- **Live Console**: 현재 실행 중인 하네스의 CPU/메모리/stdout/stderr 스트리밍

### REST API

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/hooks` | 훅 목록 |
| POST | `/api/hooks` | 훅 등록 |
| PUT | `/api/hooks/{name}` | 훅 수정 |
| DELETE | `/api/hooks/{name}` | 훅 삭제 |
| GET | `/api/events` | 이벤트 로그 |
| GET | `/api/events/{id}` | 이벤트 상세 |
| GET | `/api/executions` | 실행 이력 |
| GET | `/api/executions/{id}/log` | stdout/stderr 스트리밍 |
