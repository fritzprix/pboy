"""Telegram Bot API file download helpers."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from loguru import logger

from pboy.channels.base import redact_secret
from pboy.models.cloudevent import TelegramFileType

# Bot API file download practical limit.
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str | None, *, fallback: str) -> str:
    """Sanitize a filename for local storage."""
    raw = (name or fallback).strip() or fallback
    cleaned = _SAFE_NAME.sub("_", raw).strip("._") or fallback
    return cleaned[:180]


async def download_telegram_file(
    client: httpx.AsyncClient,
    *,
    token: str,
    api_base: str,
    file_id: str,
    dest_dir: Path,
    file_type: TelegramFileType,
    file_name: str | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> Path:
    """Call ``getFile`` and download into ``dest_dir``. Raises on failure / oversize."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    meta_url = f"{api_base.rstrip('/')}/bot{token}/getFile"
    try:
        meta_res = await client.get(meta_url, params={"file_id": file_id})
        meta_res.raise_for_status()
        body = meta_res.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(redact_secret(str(exc), token)) from exc

    if not isinstance(body, dict) or not body.get("ok"):
        raise RuntimeError(f"Telegram getFile not ok: {body!r}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Telegram getFile missing result")

    remote_path = result.get("file_path")
    if not isinstance(remote_path, str) or not remote_path.strip():
        raise RuntimeError("Telegram getFile missing file_path")
    remote_path = remote_path.strip()

    declared = result.get("file_size")
    if isinstance(declared, int) and declared > max_bytes:
        raise RuntimeError(f"Telegram file too large ({declared} > {max_bytes} bytes)")

    suffix = Path(remote_path).suffix
    if not suffix and file_type == "photo":
        suffix = ".jpg"
    base = safe_filename(file_name or Path(remote_path).name, fallback=f"{file_type}{suffix}")
    if suffix and not base.endswith(suffix):
        base = f"{base}{suffix}"
    dest = dest_dir / base

    file_url = f"{api_base.rstrip('/')}/file/bot{token}/{remote_path}"
    try:
        async with client.stream("GET", file_url) as res:
            res.raise_for_status()
            total = 0
            with dest.open("wb") as handle:
                async for chunk in res.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        handle.close()
                        dest.unlink(missing_ok=True)
                        raise RuntimeError(
                            f"Telegram file download exceeded {max_bytes} bytes"
                        )
                    handle.write(chunk)
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        raise RuntimeError(redact_secret(str(exc), token)) from exc

    logger.debug("channel.telegram downloaded file_id={} path={}", file_id[:12], dest)
    return dest.resolve()


__all__ = [
    "MAX_DOWNLOAD_BYTES",
    "download_telegram_file",
    "safe_filename",
]
