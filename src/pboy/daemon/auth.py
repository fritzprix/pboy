"""Daemon authentication helpers."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from pboy.core.config import get_settings

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "testclient", "test"})


def _extract_token(
    x_pb_token: str | None,
    authorization: str | None,
) -> str | None:
    if x_pb_token and x_pb_token.strip():
        return x_pb_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


async def require_daemon_auth(
    request: Request,
    x_pb_token: str | None = Header(default=None, alias="X-PB-Token"),
    authorization: str | None = Header(default=None),
) -> None:
    """Enforce auth on mutating routes.

    - If ``PB_AUTH_TOKEN`` is set: require matching ``X-PB-Token`` or Bearer token.
    - If unset: allow loopback / ASGI test clients only.
    """
    settings = get_settings()
    provided = _extract_token(x_pb_token, authorization)
    expected = settings.auth_token.strip()

    if expected:
        if provided != expected:
            raise HTTPException(status_code=401, detail="Invalid or missing daemon auth token")
        return

    client = request.client
    host = client.host if client is not None else ""
    if host in _LOOPBACK or host == "":
        return
    raise HTTPException(
        status_code=401,
        detail="Daemon auth token required for non-loopback access (set PB_AUTH_TOKEN)",
    )
