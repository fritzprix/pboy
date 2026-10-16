"""Local-only request guard (see ``docs/security-contract.md``).

Pageboy is a single-user local app. There is no login: instead *every* request
(API, UI, static, health) must come from this machine, addressed to a local
name, and — for state-changing methods — originate from the same origin.

Applied as ASGI middleware so no route can forget it.
"""

from __future__ import annotations

import ipaddress
from typing import Final
from urllib.parse import urlparse

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Peer name used by Starlette/httpx ASGI test clients (not a real socket peer).
_TEST_PEER_ALIASES: Final[frozenset[str]] = frozenset({"testclient"})
# Exact host *names* accepted in ``Host`` (IPs are checked via ``ipaddress``).
_LOCAL_HOST_NAMES: Final[frozenset[str]] = frozenset({"localhost"})
SAFE_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD", "OPTIONS"})
# Browser-supplied ``Sec-Fetch-Site`` values accepted on state-changing requests.
ALLOWED_FETCH_SITES: Final[frozenset[str]] = frozenset({"", "same-origin", "none"})


def _is_loopback_ip(value: str) -> bool:
    """True when ``value`` is a loopback IP literal (not a DNS name)."""
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def is_loopback_peer(host: str) -> bool:
    """True for loopback peers. Empty / missing host fails closed."""
    if not host:
        return False
    h = host.lower()
    if h in _TEST_PEER_ALIASES:
        return True
    return _is_loopback_ip(h)


def is_local_hostname(hostname: str) -> bool:
    """True for ``localhost`` or a loopback IP literal (anti DNS-rebinding).

    Names like ``127.evil.com`` must **not** match: only parseable loopback
    addresses and the exact name ``localhost`` are accepted.
    """
    if not hostname:
        return False
    h = hostname.lower().strip("[]")
    if h in _LOCAL_HOST_NAMES:
        return True
    return _is_loopback_ip(h)


def is_loopback_bind(host: str) -> bool:
    """True when a listen address is loopback-only."""
    h = host.strip().lower().strip("[]")
    if h in _LOCAL_HOST_NAMES:
        return True
    return _is_loopback_ip(h)


def _split_host(host_header: str) -> str:
    """Hostname part of a ``Host`` header (port stripped, IPv6 brackets removed)."""
    value = host_header.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def check_request(
    *,
    peer: str,
    host_header: str,
    method: str,
    origin: str | None,
    sec_fetch_site: str,
) -> str | None:
    """Return a rejection reason, or ``None`` when the request is allowed."""
    if not is_loopback_peer(peer):
        return "Pageboy only accepts connections from this machine"
    if not is_local_hostname(_split_host(host_header)):
        return "Host must be 127.0.0.1, localhost or ::1"

    method_u = method.upper()
    is_websocket = method_u == "WEBSOCKET"
    if not is_websocket and method_u in SAFE_METHODS:
        return None

    if not is_websocket and sec_fetch_site.lower() not in ALLOWED_FETCH_SITES:
        return "Cross-site request blocked"

    if origin is not None:
        parsed = urlparse(origin)
        if not parsed.netloc or parsed.netloc.lower() != host_header.strip().lower():
            return "Foreign Origin blocked"
    elif is_websocket:
        # Browsers always send Origin on WebSocket handshakes; require it so a
        # cross-site page cannot open a localhost socket without a matching Host.
        return "WebSocket Origin required"
    return None


class LocalOnlyGuard:
    """ASGI middleware enforcing the local-only contract on every request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
        }
        client = scope.get("client")
        # WebSocket scopes have no ``method``; treat them as WEBSOCKET so Origin
        # is required (safe methods would otherwise skip CSRF checks).
        method = (
            "WEBSOCKET"
            if scope["type"] == "websocket"
            else str(scope.get("method", "GET"))
        )
        reason = check_request(
            peer=client[0] if client else "",
            host_header=headers.get("host", ""),
            method=method,
            origin=headers.get("origin"),
            sec_fetch_site=headers.get("sec-fetch-site", ""),
        )
        if reason is None:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await JSONResponse({"detail": reason}, status_code=403)(scope, receive, send)


__all__ = [
    "LocalOnlyGuard",
    "check_request",
    "is_local_hostname",
    "is_loopback_bind",
    "is_loopback_peer",
]
