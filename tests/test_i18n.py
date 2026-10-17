"""Message catalogs, language negotiation, and the localized dashboard."""

from __future__ import annotations

import json
import re
import string
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from pboy.channels.telegram import TELEGRAM_SECRET_KEY, TelegramChannel
from pboy.core.config import Settings
from pboy.daemon.app import create_app
from pboy.i18n import (
    SUPPORTED_LANGS,
    LocalizedError,
    Translator,
    negotiate_lang,
    normalize_lang,
    translate,
)

_LOCALES = Path(__file__).resolve().parents[1] / "src" / "pboy" / "i18n" / "locales"
_HANGUL = re.compile(r"[가-힣]")


def _load(lang: str) -> dict[str, Any]:
    return json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def _placeholders(value: Any) -> set[str]:
    texts = value if isinstance(value, list) else [value]
    return {f[1] for text in texts for f in string.Formatter().parse(text) if f[1]}


@pytest.mark.parametrize("lang", [lang for lang in SUPPORTED_LANGS if lang != "en"])
def test_catalog_matches_english(lang: str) -> None:
    """Every language has the same keys, placeholders and step counts as English."""
    en = _load("en")
    other = _load(lang)
    assert set(other) == set(en)
    for key, value in en.items():
        assert _placeholders(other[key]) == _placeholders(value), key
        assert isinstance(other[key], list) == isinstance(value, list), key
        if isinstance(value, list):
            assert len(other[key]) == len(value), key


def test_negotiate_lang() -> None:
    assert negotiate_lang(None) == "en"
    assert negotiate_lang("ko-KR,ko;q=0.9,en-US;q=0.8") == "ko"
    assert negotiate_lang("de-DE,fr;q=0.7,ja;q=0.9") == "ja"
    assert negotiate_lang("zh-TW") == "zh"
    assert negotiate_lang("es;q=0, fr;q=0.1") == "fr"
    assert negotiate_lang("de, it") == "en"
    assert normalize_lang("pt_BR") is None
    assert normalize_lang("FR-ca") == "fr"


def test_translate_falls_back_and_formats() -> None:
    assert translate("ja", "rel.minutes", n=4) == "4 分前"
    assert translate("xx", "rel.just_now") == "just now"
    assert translate("ko", "no.such.key") == "no.such.key"
    t = Translator("es")
    assert len(t.list("guide.telegram.steps")) == 4
    # Markup params are escaped unless already Markup.
    assert "&lt;b&gt;" in str(t.html("app.code_sent", phone="<b>"))


def test_localized_error_is_english_by_default() -> None:
    exc = LocalizedError("err.login_failed", detail="boom")
    assert isinstance(exc, ValueError)
    assert str(exc) == "Telegram login failed: boom"
    assert exc.localized("fr") == "Échec de la connexion Telegram : boom"


def _app(tmp_path: Path, name: str) -> Any:
    return create_app(
        db_path=tmp_path / f"{name}.db",
        secret_key=f"{name}-key",
        enable_ui=True,
        settings=Settings(data_dir=tmp_path / f"{name}-data"),
    )


@pytest.mark.asyncio
async def test_ui_language_from_header_cookie_and_switcher(tmp_path: Path) -> None:
    application = _app(tmp_path, "i18n-switch")
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        default = await client.get("/ui")
        assert '<html lang="en">' in default.text
        assert "Recent events" in default.text

        ja = await client.get("/ui", headers={"Accept-Language": "ja-JP"})
        assert '<html lang="ja">' in ja.text
        assert "最近のイベント" in ja.text

        switch = await client.get(
            "/ui/lang", params={"lang": "zh", "next": "/ui/settings"}, follow_redirects=False
        )
        assert switch.status_code == 303
        assert switch.headers["location"] == "/ui/settings"
        assert "pboy_lang=zh" in switch.headers["set-cookie"]

        # The cookie beats Accept-Language, including on htmx partials.
        client.cookies.set("pboy_lang", "zh")
        settings = await client.get("/ui/settings", headers={"Accept-Language": "ja"})
        assert '<html lang="zh-Hans">' in settings.text
        assert "频道与密钥" in settings.text
        partial = await client.get("/ui/partials/hooks")
        assert "尚未注册钩子。" in partial.text


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/ui", "/v1/hooks"])
async def test_ui_lang_switch_rejects_offsite_next(tmp_path: Path, target: str) -> None:
    application = _app(tmp_path, "i18n-redirect")
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        res = await client.get(
            "/ui/lang", params={"lang": "fr", "next": target}, follow_redirects=False
        )
        assert res.headers["location"] == "/ui"


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
async def test_ui_pages_fully_translated(tmp_path: Path, lang: str) -> None:
    """No catalog key leaks into a page, and only Korean pages carry Hangul."""
    application = _app(tmp_path, f"i18n-pages-{lang}")
    keys = set(_load("en"))
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        client.cookies.set("pboy_lang", lang)
        for path in ("/ui", "/ui/settings", "/ui/events/evt_missing"):
            body = (await client.get(path)).text
            leaked = [key for key in keys if f">{key}<" in body or f'"{key}"' in body]
            assert not leaked, (path, leaked)
            # The picker lists "한국어" on every page; ignore it.
            text = body.replace("한국어", "")
            assert bool(_HANGUL.search(text)) == (lang == "ko"), path


@pytest.mark.asyncio
async def test_pairing_message_uses_requested_language(tmp_path: Path) -> None:
    application = _app(tmp_path, "i18n-pair")
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(200, json={"ok": True, "result": []})

    async with application.router.lifespan_context(application):
        eng = application.state.engine
        await eng.store.put_secret(TELEGRAM_SECRET_KEY, "pair-token")
        channel = TelegramChannel(
            eng.process_event,
            eng.store,
            Settings(),
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        channel._last_seen_peer = ("4242", "4242")
        info = await channel.start_pairing(lang="es")
        assert info["sent_immediately"] is True
        assert sent[-1]["text"].startswith("Código de verificación de Pageboy:")
        await channel._client.aclose()
