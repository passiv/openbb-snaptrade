import contextlib
import os
import re
from contextvars import ContextVar
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .auth import SESSION_MANAGER
from .context import WorkspaceContext
from .snaptrade_client import (
    ensure_mapping,
    fetch_accounts,
    fetch_connections,
)
from .widgets.portfolio_overview import (
    _fetch_account_summaries,
    _fetch_portfolio_exposure,
)


_CONTEXT: ContextVar[WorkspaceContext | None] = ContextVar("snaptrade_mcp_context", default=None)


_OPENBB_HOST_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.openbb\.[a-z]{2,}$", re.IGNORECASE)
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
_ALLOW_LOOPBACK_MCP = os.environ.get("SNAPTRADE_MCP_ALLOW_LOOPBACK", "1") != "0"
_PATH_TOKEN_RE = re.compile(r"^/mcp/u/([0-9a-f]+:[0-9]+:[0-9]+:[0-9a-f]+)(/.*)?$")


def _origin_is_allowed(origin: str) -> bool:
    if not origin:
        return False
    try:
        parsed = urlparse(origin)
    except Exception:
        return False
    if parsed.scheme not in ("https", "http"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if parsed.scheme == "https" and _OPENBB_HOST_RE.match(host):
        return True
    if _ALLOW_LOOPBACK_MCP and host in _LOOPBACK_HOSTS:
        return True
    return False


def _get_context() -> WorkspaceContext | None:
    return _CONTEXT.get()


def _no_session_error() -> dict:
    return {
        "error": "no_active_session",
        "detail": (
            "No active OpenBB Workspace session for this user. "
            "Open the SnapTrade-backed dashboard in OpenBB Workspace; "
            "the MCP session is refreshed each time widgets.json is loaded."
        ),
    }


def _trim_account(account: dict) -> dict:
    """Extract minimal account data for MCP responses."""
    if not isinstance(account, dict):
        return {}
    balance = account.get("balance", {})
    total = balance.get("total", {}) if isinstance(balance, dict) else {}
    meta = account.get("meta", {})
    return {
        "id": account.get("id"),
        "name": account.get("name"),
        "institution_name": account.get("institution_name"),
        "account_type": meta.get("type") or account.get("account_category"),
        "is_paper": account.get("is_paper"),
        "status": account.get("status"),
        "total_value": total.get("amount"),
        "currency": total.get("currency", "USD"),
    }


mcp = FastMCP("snaptrade", stateless_http=True, json_response=True)
mcp.settings.streamable_http_path = "/"

mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        "app:*",
        "pro.openbb.dev",
        "pro.openbb.co",
    ],
    allowed_origins=[
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
        "https://127.0.0.1:*",
        "https://localhost:*",
        "https://[::1]:*",
        "https://pro.openbb.dev",
        "https://pro.openbb.co",
    ],
)


@mcp.tool()
async def list_connections() -> list[dict] | dict:
    """Return the user's connected brokerage authorizations (one row per linked broker)."""
    ctx = _get_context()
    if not ctx:
        return _no_session_error()
    mapping = await ensure_mapping(ctx)
    data, err = await fetch_connections(ctx, mapping)
    if err:
        return {"error": "fetch_failed", "status": err.status_code}
    return data or []


@mcp.tool()
async def list_accounts() -> list[dict] | dict:
    """Return every brokerage account the user has connected through SnapTrade."""
    ctx = _get_context()
    if not ctx:
        return _no_session_error()
    mapping = await ensure_mapping(ctx)
    data, err = await fetch_accounts(ctx, mapping)
    if err:
        return {"error": "fetch_failed", "status": err.status_code}
    return [_trim_account(account) for account in (data or [])]


@mcp.tool()
async def get_account_summaries() -> list[dict] | dict:
    """Return a per-account financial rollup: total value, cash, buying power, market value, cost basis, open P/L, and position count."""
    ctx = _get_context()
    if not ctx:
        return _no_session_error()
    mapping = await ensure_mapping(ctx)
    accounts, err = await fetch_accounts(ctx, mapping)
    if err:
        return {"error": "fetch_failed", "status": err.status_code}
    summaries = await _fetch_account_summaries(ctx, mapping, accounts or [])
    return list(summaries)


@mcp.tool()
async def get_portfolio_exposure() -> dict:
    """Return aggregate portfolio exposure: `totals`, `exposures_by_kind` (per asset class with market value and weight), `top_positions`, and the full flat `positions` list across every connected account."""
    ctx = _get_context()
    if not ctx:
        return _no_session_error()
    mapping = await ensure_mapping(ctx)
    accounts, err = await fetch_accounts(ctx, mapping)
    if err:
        return {"error": "fetch_failed", "status": err.status_code}
    return await _fetch_portfolio_exposure(ctx, mapping, accounts or [])


def _forbidden_response(detail: str):
    body = b'{"error":"forbidden","detail":"' + detail.encode("ascii", "replace") + b'"}'

    async def _send(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    return _send


def gated_mcp_app(inner_asgi):
    async def wrapped(scope, receive, send):
        if scope.get("type") != "http":
            await inner_asgi(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        method = scope.get("method", "GET").upper()

        if method != "OPTIONS":
            origin = headers.get("origin", "").strip()
            if not _origin_is_allowed(origin):
                responder = _forbidden_response("origin_not_allowed")
                await responder(scope, receive, send)
                return

        path_token = scope.get("_snaptrade_mcp_token", "") or ""
        ctx = await SESSION_MANAGER.resolve(path_token) if path_token else None
        ctx_reset = _CONTEXT.set(ctx)
        try:
            await inner_asgi(scope, receive, send)
        finally:
            _CONTEXT.reset(ctx_reset)

    return wrapped


@contextlib.asynccontextmanager
async def mcp_lifespan():
    async with mcp.session_manager.run():
        yield


class _NormalizeMcpPath:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            match = _PATH_TOKEN_RE.match(path)
            if match:
                token = match.group(1)
                rest = match.group(2) or "/"
                new_path = "/mcp" + rest
                if not new_path.endswith("/"):
                    new_path = new_path + "/"
                scope = dict(scope)
                scope["path"] = new_path
                raw = scope.get("raw_path")
                if isinstance(raw, (bytes, bytearray)):
                    scope["raw_path"] = new_path.encode("latin-1")
                scope["_snaptrade_mcp_token"] = token
            elif path == "/mcp":
                scope = dict(scope)
                scope["path"] = "/mcp/"
                raw = scope.get("raw_path")
                if isinstance(raw, (bytes, bytearray)) and not raw.endswith(b"/"):
                    scope["raw_path"] = bytes(raw) + b"/"
        await self.app(scope, receive, send)


def install(fastapi_app) -> None:
    fastapi_app.mount("/mcp", gated_mcp_app(mcp.streamable_http_app()))
    fastapi_app.add_middleware(_NormalizeMcpPath)

    original_lifespan = fastapi_app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def combined(app):
        async with original_lifespan(app):
            async with mcp_lifespan():
                yield

    fastapi_app.router.lifespan_context = combined
