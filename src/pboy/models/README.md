# Models — `pboy.models`

`pboy.models` — CloudEvents v1.0 호환 Pydantic 도메인 모델

## 모듈 구조

| 파일 | 설명 |
|------|------|
| `__init__.py` | 전체 모델 re-export |
| `cloudevent.py` | CloudEvents v1.0 호환 이벤트 스펙 |
| `hook.py` | 훅 메타데이터 모델 |
| `execution.py` | 하네스 실행 이력 모델 |

## CloudEvents v1.0 스펙

서버가 하네스 `stdin`으로 밀어넣는 정규화 데이터:

```json
{
  "specversion": "1.0",
  "id": "evt_20261009_a8f9c1",
  "source": "jira",
  "type": "com.atlassian.jira.issue.created",
  "time": "2026-10-09T04:02:19Z",
  "datacontenttype": "application/json",
  "data": {
    "entity_key": "INFRA-4029",
    "channel_id": "proj-infra",
    "thread_id": null,
    "actor": { "id": "usr_david_01", "display_name": "David Lee" },
    "summary": "B200 NVLink training error",
    "description": "Node 04 GPU drop out detected in cluster B.",
    "priority": "High",
    "labels": ["gpu", "infra", "urgent"],
    "raw_ref": "/home/user/.config/pboy/events/raw/evt_20261009_a8f9c1.json"
  }
}
```

## 토픽 매핑

| 플랫폼 | 토픽 패턴 | CloudEvents type |
|--------|----------|------------------|
| Jira | `jira:issue.created` | `com.atlassian.jira.issue.created` |
| Jira | `jira:issue.updated` | `com.atlassian.jira.issue.updated` |
| Confluence | `confluence:page.updated` | `com.atlassian.confluence.page.updated` |
| Telegram | `telegram:message` | `com.telegram.message.received` |
| Slack | `slack:message` | `com.slack.message.received` |
