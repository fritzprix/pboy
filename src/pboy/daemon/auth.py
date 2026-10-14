"""Daemon authentication helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from urllib.parse import urlparse

from fastapi import Header, HTTPException, Query, Request

from pboy.core.config import get_settings

# Peer IPs treated as loopback (ASGI test clients included).
_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "localhost", "testclient", "test"})
# Host header values accepted in unauthenticated local mode (anti DNS-rebinding).
_LOCAL_REQUEST_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testclient", "test"})

# HttpOnly cookie set by ``POST /ui/login`` (HMAC session, not the raw token).
UI_SESSION_COOKIE = "pb_ui_session"
_UI_SESSION_MAX_AGE = 60 * 60 * 24 * 14


def _extract_token(
    x_pb_token: str | None,
    authorization: str | None,
) -> str | None:
    if x_pb_token and x_pb_token.strip():
        return x_pb_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def _client_host(request: Request) -> str:
    client = request.client
    return client.host if client is not None else ""


def _is_loopback(host: str) -> bool:
    """True for known loopback peers. Empty / missing host fails closed."""
    if not host:
        return False
    h = host.lower()
    if h in _LOOPBACK_PEERS:
        return True
    # 127.0.0.0/8 (e.g. Debian 127.0.1.1)
    return h.startswith("127.")


def _is_local_request_host(hostname: str) -> bool:
    if not hostname:
        return False
    h = hostname.lower()
    if h in _LOCAL_REQUEST_HOSTS:
        return True
    return h.startswith("127.")


def assert_local_request_host(request: Request) -> None:
    """Reject non-local ``Host`` headers (DNS rebinding) in unauthenticated local mode."""
    hostname = (request.url.hostname or "").lower()
    if _is_local_request_host(hostname):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "Unauthenticated local mode only accepts Host 127.0.0.1 / localhost / ::1. "
            "Set PB_AUTH_TOKEN for reverse-proxy or remote access."
        ),
    )


def tokens_match(provided: str | None, expected: str) -> bool:
    """Constant-time compare for auth secrets (length mismatch → False)."""
    if not provided or not expected:
        return False
    left = provided.encode("utf-8")
    right = expected.encode("utf-8")
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def mint_ui_session(auth_token: str, *, max_age: int = _UI_SESSION_MAX_AGE) -> str:
    """Return a signed opaque session value bound to ``auth_token``."""
    exp = str(int(time.time()) + max_age).encode("ascii")
    sig = hmac.new(auth_token.encode("utf-8"), exp, hashlib.sha256).digest()
    return (
        urlsafe_b64encode(exp).decode("ascii").rstrip("=")
        + "."
        + urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    )


def verify_ui_session(cookie: str, auth_token: str) -> bool:
    """Validate HMAC session cookie for ``auth_token`` (not expired)."""
    if not cookie or not auth_token or "." not in cookie:
        return False
    payload_b64, sig_b64 = cookie.split(".", 1)
    try:
        exp_raw = urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        sig = urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        exp = int(exp_raw.decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return False
    if exp < int(time.time()):
        return False
    expected = hmac.new(auth_token.encode("utf-8"), exp_raw, hashlib.sha256).digest()
    return hmac.compare_digest(sig, expected)


def token_from_ui_request(
    request: Request,
    *,
    x_pb_token: str | None = None,
    authorization: str | None = None,
    token: str | None = None,
) -> str | None:
    """Resolve a *raw* UI token from header, Bearer, or ``?token=``.

    Signed session cookies are checked separately via :func:`ui_request_authorized`.
    """
    provided = _extract_token(x_pb_token, authorization)
    if token and token.strip():
        provided = provided or token.strip()
    return provided


def ui_request_authorized(
    request: Request,
    *,
    expected: str,
    x_pb_token: str | None = None,
    authorization: str | None = None,
    token: str | None = None,
) -> bool:
    """True when header/query token matches or a valid session cookie is present."""
    if not expected:
        return False
    provided = token_from_ui_request(
        request,
        x_pb_token=x_pb_token,
        authorization=authorization,
        token=token,
    )
    if tokens_match(provided, expected):
        return True
    cookie = request.cookies.get(UI_SESSION_COOKIE)
    return bool(cookie and verify_ui_session(cookie, expected))


def assert_ui_mutation_csrf(request: Request) -> None:
    """Block obvious cross-site browser form POSTs to UI mutation endpoints.

    Modern browsers send ``Sec-Fetch-Site: cross-site`` for evil.com → localhost.
    When that header is absent (curl/httpx/tests), fall back to Origin host checks.
    Matching Origin==Host alone is not enough (DNS rebinding); the request Host must
    also be a local name unless the operator has set ``PB_AUTH_TOKEN``.
    """
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if site == "cross-site":
        raise HTTPException(
            status_code=403,
            detail="Blocked cross-site UI form submission",
        )

    origin = request.headers.get("origin")
    if not origin:
        return
    host = (urlparse(origin).hostname or "").lower()
    if not host:
        return
    req_host = (request.url.hostname or "").lower()
    if _is_loopback(host) or (_is_local_request_host(req_host) and host == req_host):
        return
    raise HTTPException(
        status_code=403,
        detail="Blocked UI mutation from foreign Origin",
    )


async def require_daemon_auth(
    request: Request,
    x_pb_token: str | None = Header(default=None, alias="X-PB-Token"),
    authorization: str | None = Header(default=None),
) -> None:
    """Enforce auth on mutating API routes.

    - If ``PB_AUTH_TOKEN`` is set: require matching token for *all* clients
      (including loopback — safe behind reverse proxies on the same host).
    - If unset: allow loopback peers only, and only with a local ``Host`` header.
    """
    settings = get_settings()
    expected = settings.auth_token.strip()
    provided = _extract_token(x_pb_token, authorization)

    if expected:
        if not tokens_match(provided, expected):
            raise HTTPException(status_code=401, detail="Invalid or missing daemon auth token")
        return

    if _is_loopback(_client_host(request)):
        assert_local_request_host(request)
        return
    raise HTTPException(
        status_code=401,
        detail="Daemon auth token required for non-loopback access (set PB_AUTH_TOKEN)",
    )


async def require_ui_access(
    request: Request,
    x_pb_token: str | None = Header(default=None, alias="X-PB-Token"),
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Gate dashboard pages.

    - If ``PB_AUTH_TOKEN`` is set: require token / session for all clients.
    - If unset: loopback + local ``Host`` only.
    """
    settings = get_settings()
    expected = settings.auth_token.strip()

    if expected:
        if ui_request_authorized(
            request,
            expected=expected,
            x_pb_token=x_pb_token,
            authorization=authorization,
            token=token,
        ):
            return
        raise HTTPException(
            status_code=401,
            detail=(
                "UI requires a valid password/token. "
                "Open /ui/login or pass ?token= / X-PB-Token."
            ),
        )

    if _is_loopback(_client_host(request)):
        assert_local_request_host(request)
        return
    raise HTTPException(
        status_code=401,
        detail=(
            "UI requires loopback access or a valid password/token. "
            "Open /ui/login or set PB_AUTH_TOKEN for remote access."
        ),
    )


async def require_ui_mutation(
    request: Request,
    x_pb_token: str | None = Header(default=None, alias="X-PB-Token"),
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Gate UI form POSTs that change secrets / channel enablement.

    - Always reject ``Sec-Fetch-Site: cross-site`` (CSRF).
    - If ``PB_AUTH_TOKEN`` is set: require token / session for all clients
      (including loopback — safe behind reverse proxies).
    - If unset: loopback + local ``Host`` only.
    """
    assert_ui_mutation_csrf(request)

    settings = get_settings()
    expected = settings.auth_token.strip()

    if expected:
        if ui_request_authorized(
            request,
            expected=expected,
            x_pb_token=x_pb_token,
            authorization=authorization,
            token=token,
        ):
            return
        raise HTTPException(
            status_code=401,
            detail="Login required to save settings. Open /ui/login and enter PB_AUTH_TOKEN.",
        )

    if _is_loopback(_client_host(request)):
        assert_local_request_host(request)
        return
    raise HTTPException(
        status_code=401,
        detail="Set PB_AUTH_TOKEN before mutating the UI from a non-loopback client",
    )
