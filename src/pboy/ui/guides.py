"""Layman-facing copy, links, and field tooltips for the settings UI."""

from __future__ import annotations

from typing import Any, Final, NotRequired, TypedDict


class GuideLink(TypedDict):
    label: str
    url: str


class GuideField(TypedDict):
    secret_name: str
    label: str
    placeholder: str
    tooltip: str
    help: str
    optional: NotRequired[bool]


class ChannelGuide(TypedDict):
    name: str
    title: str
    blurb: str
    what_it_does: str
    steps: list[str]
    links: list[GuideLink]
    fields: list[GuideField]


class InfoGuide(TypedDict):
    title: str
    blurb: str
    what_it_does: str
    steps: list[str]
    links: list[GuideLink]
    note: str


CHANNEL_GUIDES: Final[list[ChannelGuide]] = [
    {
        "name": "telegram",
        "title": "Telegram",
        "blurb": "스마트폰 Telegram에서 봇에게 메시지를 보내면 Pageboy가 깨어납니다.",
        "what_it_does": (
            "공개 웹사이트 주소 없이, Telegram 서버에 “새 메시지 있나요?”를 "
            "주기적으로 물어보는 방식입니다."
        ),
        "steps": [
            "Telegram 앱을 열고 검색창에 BotFather 를 찾습니다.",
            "대화에서 /newbot 을 입력하고, 안내에 따라 봇 이름·username을 만듭니다.",
            "BotFather가 보내 주는 긴 토큰(비밀번호처럼 생긴 한 줄)을 복사합니다.",
            "아래 입력란에 붙여넣고 저장한 뒤, “이 채널 켜기”를 누릅니다.",
        ],
        "links": [
            {
                "label": "BotFather 열기 (Telegram)",
                "url": "https://t.me/BotFather",
            },
            {
                "label": "Telegram 봇 소개 (공식)",
                "url": "https://core.telegram.org/bots#botfather",
            },
        ],
        "fields": [
            {
                "secret_name": "bot_token",
                "label": "봇 토큰",
                "placeholder": "123456789:AAH…",
                "tooltip": (
                    "BotFather가 /newbot 직후 보내 주는 한 줄입니다. "
                    "숫자:영문 형태이며, 다른 사람에게 공유하면 안 됩니다."
                ),
                "help": "예: 7123456789:AAHh… (전체를 그대로 붙여넣기)",
            },
            {
                "secret_name": "allowlist",
                "label": "허용할 채팅·사용자 ID (선택 · Allowlist)",
                "placeholder": "123456789, -100987654321",
                "tooltip": (
                    "여기에 숫자 ID를 쉼표(,)로 적어 저장하면, 해당 chat.id 또는 "
                    "발신자 user id(from.id)에서 온 메시지만 처리하고 나머지는 무시합니다. "
                    "주의: 그룹 chat.id(음수)를 허용하면 해당 채팅방의 모든 멤버가 허용됩니다."
                ),
                "help": (
                    "비운 채로 저장하면 해제(전체 허용) · 쉼표로 구분 "
                    "(주의: 그룹 chat.id 허용 시 그 방 전원 허용)"
                ),
                "optional": True,
            },
            {
                "secret_name": "api_id",
                "label": "App API ID (대화 분석용 · my.telegram.org)",
                "placeholder": "12345678",
                "tooltip": (
                    "봇 대화(wake-up)와 별개입니다. 내 계정이 속한 대화방 목록·메시지·파일 "
                    "내보내기를 쓸 때 필요합니다. https://my.telegram.org → API development tools."
                ),
                "help": "숫자만 · 봇 토큰과 다릅니다 · 분석/export용",
                "optional": True,
            },
            {
                "secret_name": "api_hash",
                "label": "App API Hash (대화 분석용)",
                "placeholder": "0123456789abcdef…",
                "tooltip": (
                    "my.telegram.org에서 api_id와 함께 발급됩니다. "
                    "아래에서 계정 연결(1회)을 마치면 대화방 전체 list가 가능합니다."
                ),
                "help": "영문+숫자 · 비우면 저장 해제",
                "optional": True,
            },
        ],
    },
    {
        "name": "slack",
        "title": "Slack",
        "blurb": "Slack 워크스페이스에서 앱/봇에게 메시지를 보내면 Pageboy가 깨어납니다.",
        "what_it_does": (
            "공개 URL 없이 Slack이 우리 PC로 소켓(Socket Mode)을 열어 이벤트를 밀어 줍니다. "
            "열쇠가 두 개 필요합니다."
        ),
        "steps": [
            "아래 “Slack 앱 만들기” 링크로 새 앱을 만듭니다 (워크스페이스 선택).",
            "앱 설정에서 Socket Mode 를 ON 으로 켭니다.",
            "App-level Token 을 만들고 scope connections:write 를 준 뒤 xapp-… 값을 복사합니다.",
            "OAuth로 봇을 워크스페이스에 설치한 뒤 Bot User OAuth Token (xoxb-…) 을 복사합니다.",
            "메시지 이벤트(예: message.channels / message.im)를 구독합니다.",
            "아래 두 칸에 붙여넣고 저장한 뒤 “이 채널 켜기”를 누릅니다.",
        ],
        "links": [
            {
                "label": "Slack 앱 만들기",
                "url": "https://api.slack.com/apps",
            },
            {
                "label": "Socket Mode 안내 (공식)",
                "url": "https://api.slack.com/apis/connections/socket",
            },
        ],
        "fields": [
            {
                "secret_name": "bot_token",
                "label": "봇 토큰 (xoxb-…)",
                "placeholder": "xoxb-…",
                "tooltip": (
                    "OAuth & Permissions 화면의 Bot User OAuth Token 입니다. "
                    "보통 xoxb- 로 시작합니다. 채팅 권한(chat:write)이 필요합니다."
                ),
                "help": "Bot User OAuth Token — xoxb- 로 시작",
            },
            {
                "secret_name": "app_token",
                "label": "앱 토큰 (xapp-…)",
                "placeholder": "xapp-…",
                "tooltip": (
                    "Basic Information → App-Level Tokens 에서 만듭니다. "
                    "Socket Mode용이며 connections:write 권한이 있어야 합니다. xapp- 로 시작."
                ),
                "help": "App-Level Token — xapp- 로 시작",
            },
        ],
    },
]


JIRA_INFO: Final[InfoGuide] = {
    "title": "Jira / Confluence (참고)",
    "blurb": (
        "여기는 “깨우는 채널”이 아닙니다. "
        "훅이 실행하는 프로그램(명령)이 필요할 때 REST로 이슈를 읽고 쓰는 작업면입니다."
    ),
    "what_it_does": (
        "Telegram/Slack처럼 Pageboy가 Jira를 상시 폴링하지 않습니다. "
        "대신 훅에 적어 둔 스크립트·에이전트 CLI가 Atlassian API를 호출합니다."
    ),
    "steps": [
        "Atlassian 계정에 로그인합니다.",
        "Security → API tokens 에서 Create API token 으로 토큰을 만듭니다.",
        "그 토큰은 Pageboy 채널이 아니라, 훅 exec 프로그램이 쓰도록 환경변수나 "
        "아래 “하네스용 비밀”에 넣습니다.",
        "공개 웹훅 URL은 이 Alpha 경로에 필요하지 않습니다.",
    ],
    "links": [
        {
            "label": "Atlassian API 토큰 만들기",
            "url": "https://id.atlassian.com/manage-profile/security/api-tokens",
        },
        {
            "label": "Jira Cloud REST 문서",
            "url": "https://developer.atlassian.com/cloud/jira/platform/rest/v3/",
        },
    ],
    "note": (
        "오해 방지: “훅이 띄운 프로그램” = 훅에 등록한 exec 명령의 서브프로세스입니다. "
        "Pageboy가 별도의 Jira 전용 봇을 내장한다는 뜻이 아닙니다."
    ),
}


HARNESS_SECRET_FIELDS: Final[list[dict[str, Any]]] = [
    {
        "key": "jira.api_token",
        "label": "Jira API 토큰 (선택)",
        "placeholder": "ATATT3…",
        "tooltip": (
            "Atlassian API token 값입니다. 저장만 하며 화면에 다시 보여 주지 않습니다. "
            "훅 프로그램이 읽도록 연결하는 것은 별도 설정일 수 있습니다."
        ),
        "help": "채널 enable과 무관 — 하네스/스크립트용 보관함",
    },
    {
        "key": "jira.email",
        "label": "Jira 계정 이메일 (선택)",
        "placeholder": "you@example.com",
        "tooltip": (
            "Cloud REST Basic 인증에 쓰는 Atlassian 계정 이메일입니다. "
            "API 토큰과 한 쌍으로 쓰는 경우가 많습니다."
        ),
        "help": "API 토큰과 함께 쓰는 로그인 이메일",
    },
]


__all__ = [
    "CHANNEL_GUIDES",
    "HARNESS_SECRET_FIELDS",
    "JIRA_INFO",
]
