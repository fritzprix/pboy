# Tests — `pboy.tests`

pytest 기반 테스트 스위트. 공개 계약은 `tests/contracts/spec.py`가 기준입니다.

## 구조

```
tests/
├── conftest.py                 # fixtures (store, engine, sample payloads)
├── contracts/spec.py           # Alpha 공개 계약 상수
├── fixtures/harness_probe.py   # harness stdin/env 프로브
├── test_contracts.py           # 계약 검증 (pytest -m contract)
├── test_smoke.py               # 스모크 / 회귀
├── test_cli.py                 # CLI 명령
├── test_store.py               # SQLite store
├── test_executor.py            # harness executor
├── test_engine.py              # engine dispatch / lease
└── test_filters_and_models.py  # filters / hook / config / normalizer
```

## 실행

```bash
# 전체
pytest

# 계약만
pytest -m contract -v

# 커버리지
pytest --cov=pboy --cov-report=term-missing
```

## 계약 범위

| 계약 | 검증 대상 |
|------|-----------|
| CloudEvents v1.0 | 필수 필드, id/time 포맷, topic↔type 전단사 |
| Normalizer | jira/slack/telegram/confluence → EventData 키 |
| Filter | 지원 문법 / fail-closed |
| StoreBase | 추상 메서드 표면 + CRUD 의미 |
| Harness | stdin CloudEvent JSON + `PB_*` env |
| Engine | `process_event` → `(event, hooks, executions)` |
| CLI | 구현 그룹 vs 미구현 명령 exit 1 |
