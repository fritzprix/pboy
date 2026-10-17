"""Layman-facing copy, links, and field tooltips for the settings UI.

Text lives in the ``pboy.i18n`` catalogs (``guide.*`` keys); these builders
attach it to the static structure (names, URLs, placeholders) per request.
"""

from __future__ import annotations

from typing import Any, Final, NotRequired, TypedDict

from pboy.i18n import Translator


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
    # False for non-secret values (IDs) so the input is visible while typing.
    secret: NotRequired[bool]


class ChannelGuide(TypedDict):
    name: str
    title: str
    blurb: str
    what_it_does: str
    steps: list[str]
    links: list[GuideLink]
    fields: list[GuideField]


class AppApiGuide(TypedDict):
    title: str
    blurb: str
    links: list[GuideLink]
    fields: list[GuideField]


class InfoGuide(TypedDict):
    title: str
    blurb: str
    what_it_does: str
    steps: list[str]
    links: list[GuideLink]
    note: str


# Secret store keys accepted by the harness-secret form.
HARNESS_SECRET_KEYS: Final[tuple[str, ...]] = ("jira.api_token", "jira.email")


def _field(
    t: Translator,
    prefix: str,
    secret_name: str,
    placeholder: str,
    *,
    label: str | None = None,
    **extra: bool,
) -> GuideField:
    field: GuideField = {
        "secret_name": secret_name,
        "label": label if label is not None else t(f"{prefix}.{secret_name}.label"),
        "placeholder": placeholder,
        "tooltip": t(f"{prefix}.{secret_name}.tooltip"),
        "help": t(f"{prefix}.{secret_name}.help"),
    }
    if "optional" in extra:
        field["optional"] = extra["optional"]
    if "secret" in extra:
        field["secret"] = extra["secret"]
    return field


def channel_guides(t: Translator) -> list[ChannelGuide]:
    """Wake-up channel guides (Telegram, Slack) in the viewer's language."""
    return [
        {
            "name": "telegram",
            "title": "Telegram",
            "blurb": t("guide.telegram.blurb"),
            "what_it_does": t("guide.telegram.what"),
            "steps": t.list("guide.telegram.steps"),
            "links": [
                {"label": t("guide.telegram.link.botfather"), "url": "https://t.me/BotFather"},
                {
                    "label": t("guide.telegram.link.bots"),
                    "url": "https://core.telegram.org/bots#botfather",
                },
            ],
            "fields": [
                _field(t, "guide.telegram", "bot_token", "123456789:AAH…"),
                _field(
                    t,
                    "guide.telegram",
                    "allowlist",
                    "123456789, -100987654321",
                    optional=True,
                    secret=False,
                ),
            ],
        },
        {
            "name": "slack",
            "title": "Slack",
            "blurb": t("guide.slack.blurb"),
            "what_it_does": t("guide.slack.what"),
            "steps": t.list("guide.slack.steps"),
            "links": [
                {"label": t("guide.slack.link.create"), "url": "https://api.slack.com/apps"},
                {
                    "label": t("guide.slack.link.socket"),
                    "url": "https://api.slack.com/apis/connections/socket",
                },
            ],
            "fields": [
                _field(t, "guide.slack", "bot_token", "xoxb-…"),
                _field(t, "guide.slack", "app_token", "xapp-…"),
            ],
        },
    ]


def jira_info(t: Translator) -> InfoGuide:
    return {
        "title": t("guide.jira.title"),
        "blurb": t("guide.jira.blurb"),
        "what_it_does": t("guide.jira.what"),
        "steps": t.list("guide.jira.steps"),
        "links": [
            {
                "label": t("guide.jira.link.token"),
                "url": "https://id.atlassian.com/manage-profile/security/api-tokens",
            },
            {
                "label": t("guide.jira.link.rest"),
                "url": "https://developer.atlassian.com/cloud/jira/platform/rest/v3/",
            },
        ],
        "note": t("guide.jira.note"),
    }


def telegram_app_guide(t: Translator) -> AppApiGuide:
    return {
        "title": t("guide.app.title"),
        "blurb": t("guide.app.blurb"),
        "links": [{"label": t("guide.app.link.mytg"), "url": "https://my.telegram.org"}],
        "fields": [
            _field(
                t,
                "guide.app",
                "api_id",
                "12345678",
                label="App API ID",
                optional=True,
                secret=False,
            ),
            _field(
                t,
                "guide.app",
                "api_hash",
                "0123456789abcdef…",
                label="App API Hash",
                optional=True,
            ),
        ],
    }


def harness_secret_fields(t: Translator) -> list[dict[str, Any]]:
    placeholders = {"jira.api_token": "ATATT3…", "jira.email": "you@example.com"}
    catalog_prefix = {"jira.api_token": "jira_token", "jira.email": "jira_email"}
    out: list[dict[str, Any]] = []
    for key in HARNESS_SECRET_KEYS:
        prefix = f"guide.harness.{catalog_prefix[key]}"
        out.append(
            {
                "key": key,
                "label": t(f"{prefix}.label"),
                "placeholder": placeholders[key],
                "tooltip": t(f"{prefix}.tooltip"),
                "help": t(f"{prefix}.help"),
            }
        )
    return out


__all__ = [
    "HARNESS_SECRET_KEYS",
    "channel_guides",
    "harness_secret_fields",
    "jira_info",
    "telegram_app_guide",
]
