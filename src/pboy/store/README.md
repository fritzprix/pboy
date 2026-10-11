# Store — `pboy.store`

`pboy.store` — SQLite 기반 암호화 저장소

## 모듈 구조

| 파일 | 설명 |
|------|------|
| `base.py` | 추상 스토리지 인터페이스 |
| `sqlite.py` | SQLite 백엔드 + 스키마 DDL (`_execute_schema`) |

스키마는 **단일 DDL** (`CREATE TABLE IF NOT EXISTS` + indexes)로만 관리합니다.
아직 설치된 사용자 DB가 없으므로 migration 모듈은 두지 않습니다.
깨진/낡은 로컬 DB는 파일을 지우고 다시 띄우면 됩니다:

```bash
rm -f ~/.local/share/pboy/pboy.db
# 또는 데몬/CLI에 --db / PB_DATABASE_URL 로 별도 경로 사용
```

## 테이블 스펙

| 테이블 | 설명 |
|------|------|
| `hooks` | 훅 메타데이터 (이름, 토픽, 필터, 실행명령어, 동시수, 타임아웃) |
| `events` | CloudEvents 로그 |
| `executions` | 하네스 실행 이력 (PID, stdout/stderr, 종료코드) |
| `state` | 이벤트별 키-값 상태 |
| `secrets` | AES-GCM 암호문 |

## 암호화

- `secrets` 테이블의 `secret_value`는 **AES-256-GCM**으로 암호화
- 키는 환경변수 `PB_SECRET_KEY` 또는 `~/.config/pboy/secret.key` (mode `0600`)에서 로드
- `secrets` API로 저장한 값만 암호화됨 (훅 `exec`는 평문)

## 인터페이스

```python
class StoreBase(ABC):
    # hooks
    async def create_hook(data: HookCreate) -> Hook
    async def get_hook(name: str) -> Hook | None
    async def list_hooks() -> list[Hook]
    async def delete_hook(name: str) -> bool

    # events
    async def create_event(event: CloudEvent) -> None
    async def get_event(event_id: str) -> CloudEvent | None
    async def list_events(limit: int, offset: int) -> list[CloudEvent]

    # executions
    async def record_execution(exec_data: ExecutionData) -> None
    async def get_execution(exec_id: str) -> ExecutionData | None

    # state
    async def put_state(event_id: str, key: str, value: dict) -> None
    async def get_state(event_id: str, key: str) -> dict | None

    # secrets
    async def put_secret(key: str, value: str) -> None
    async def get_secret(key: str) -> str | None
```
