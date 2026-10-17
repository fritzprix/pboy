"""Message catalogs for operator-facing text (dashboard, settings, bot replies).

One JSON catalog per language under ``locales/``; ``en`` is the source of truth
and the fallback for missing keys. Values are ``str.format`` templates or, for
step lists, lists of strings.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, Final

from markupsafe import Markup

SUPPORTED_LANGS: Final[tuple[str, ...]] = ("en", "ko", "ja", "zh", "fr", "es")
DEFAULT_LANG: Final[str] = "en"
# Native names for the language picker.
LANGUAGE_NAMES: Final[dict[str, str]] = {
    "en": "English",
    "ko": "한국어",
    "ja": "日本語",
    "zh": "简体中文",
    "fr": "Français",
    "es": "Español",
}
# Value for ``<html lang>``; Chinese catalog is Simplified.
HTML_LANG: Final[dict[str, str]] = {"zh": "zh-Hans"}

_LOCALES_DIR = Path(__file__).resolve().parent / "locales"


@cache
def _catalog(lang: str) -> dict[str, Any]:
    with (_LOCALES_DIR / f"{lang}.json").open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def normalize_lang(code: str | None) -> str | None:
    """``ko-KR`` → ``ko``, ``zh-Hant`` → ``zh``; ``None`` when unsupported."""
    if not code:
        return None
    base = code.strip().lower().replace("_", "-").split("-", 1)[0]
    return base if base in SUPPORTED_LANGS else None


def negotiate_lang(accept_language: str | None) -> str:
    """Pick the best supported language from an ``Accept-Language`` header."""
    if not accept_language:
        return DEFAULT_LANG
    ranked: list[tuple[float, int, str]] = []
    for idx, part in enumerate(accept_language.split(",")):
        tag, *params = (piece.strip() for piece in part.split(";"))
        quality = 1.0
        for param in params:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        lang = normalize_lang(tag)
        if lang and quality > 0:
            ranked.append((-quality, idx, lang))
    return min(ranked)[2] if ranked else DEFAULT_LANG


def _lookup(lang: str, key: str) -> Any:
    for candidate in (lang, DEFAULT_LANG):
        value = _catalog(candidate).get(key)
        if value is not None:
            return value
    return key


def translate(lang: str, key: str, **params: Any) -> str:
    """Catalog text for ``key`` in ``lang`` (English, then the key itself, as fallback)."""
    value = _lookup(lang if lang in SUPPORTED_LANGS else DEFAULT_LANG, key)
    text = str(value)
    return text.format(**params) if params else text


class Translator:
    """Per-request helper bound to one language; exposed to templates as ``t``."""

    def __init__(self, lang: str) -> None:
        self.lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG

    def __call__(self, key: str, **params: Any) -> str:
        return translate(self.lang, key, **params)

    def html(self, key: str, **params: Any) -> Markup:
        """Catalog text that carries trusted markup; params are escaped unless Markup."""
        return Markup(str(_lookup(self.lang, key))).format(**params)

    def list(self, key: str) -> list[str]:
        value = _lookup(self.lang, key)
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]


class LocalizedError(ValueError):
    """User-facing error whose text is rendered in the viewer's language.

    ``str(exc)`` is the English text, so logs and CLI output stay readable.
    """

    def __init__(self, key: str, **params: Any) -> None:
        self.key = key
        self.params = params
        self.i18n_key = key
        self.i18n_params = params
        super().__init__(translate(DEFAULT_LANG, key, **params))

    def localized(self, lang: str) -> str:
        return translate(lang, self.key, **self.params)


def localize_error(exc: BaseException, lang: str) -> str:
    """Text for ``exc`` in ``lang``.

    Any exception may opt in by setting ``i18n_key`` (and optional ``i18n_params``),
    e.g. errors that must keep a non-``ValueError`` base class; others fall back to
    ``str(exc)``.
    """
    key = getattr(exc, "i18n_key", None)
    if isinstance(key, str):
        params = getattr(exc, "i18n_params", None) or {}
        return translate(lang, key, **params)
    return str(exc)


__all__ = [
    "DEFAULT_LANG",
    "HTML_LANG",
    "LANGUAGE_NAMES",
    "SUPPORTED_LANGS",
    "LocalizedError",
    "Translator",
    "localize_error",
    "negotiate_lang",
    "normalize_lang",
    "translate",
]
