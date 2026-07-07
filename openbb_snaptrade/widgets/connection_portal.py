import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pywry.models import HtmlContent

from ..auth import SESSION_MANAGER
from ..config import IFRAME_HEADERS, STATIC_DIR
from ..context import resolve_workspace_context
from ..iframe import (
    not_found_response,
    render_html_content,
)
from ..snaptrade_client import (
    ensure_mapping,
    fetch_connections,
    is_personal_client,
    missing_user_secret_response,
    snaptrade_client,
    snaptrade_credentials,
)


_TEMPLATE = STATIC_DIR / "snaptrade_connection_portal.html"


def _trim_connection(conn: dict) -> dict:
    """Extract minimal connection data for wire transmission."""
    if not isinstance(conn, dict):
        return {}
    brokerage = conn.get("brokerage", {})
    brokerage_display = conn.get("brokerage_display_name") or (
        brokerage.get("display_name") if isinstance(brokerage, dict) else None
    )
    brokerage_name = conn.get("brokerage_name") or (brokerage.get("name") if isinstance(brokerage, dict) else None)

    display_name = conn.get("display_name") or conn.get("name")
    if not display_name and conn.get("institution_name"):
        display_name = conn.get("institution_name")
    if not display_name and brokerage_display:
        display_name = brokerage_display

    return {
        "id": conn.get("id"),
        "name": display_name,
        "display_name": display_name,
        "brokerage_name": brokerage_name,
        "brokerage_display_name": brokerage_display,
        "description": conn.get("description"),
        "status": conn.get("status"),
        "institution_name": conn.get("institution_name"),
        "account_type": conn.get("account_type") or conn.get("type"),
        "connection_type": conn.get("connection_type"),
        "authorization_type": conn.get("authorization_type"),
        "mode": conn.get("mode"),
    }


def build_widget_content() -> HtmlContent:
    return HtmlContent(
        html=_TEMPLATE.read_text(encoding="utf-8"),
        css_files=[STATIC_DIR / "snaptrade.css"],
        script_files=[
            STATIC_DIR / "openbb_iframe_bridge.js",
            STATIC_DIR / "snaptrade_init.js",
            STATIC_DIR / "snaptrade.js",
        ],
    )


WIDGET_CONTENT = build_widget_content()


def register(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()
        return HTMLResponse(render_html_content(WIDGET_CONTENT), headers=IFRAME_HEADERS)

    @app.get("/widget", response_class=HTMLResponse)
    async def widget_route(request: Request):
        return HTMLResponse(
            render_html_content(WIDGET_CONTENT),
            headers=IFRAME_HEADERS,
        )

    @app.get("/widget/s/{token}", response_class=HTMLResponse)
    async def widget_session_route(token: str):
        ctx = await SESSION_MANAGER.resolve(token)
        if not ctx:
            return not_found_response()
        return HTMLResponse(
            render_html_content(WIDGET_CONTENT),
            headers=IFRAME_HEADERS,
        )

    @app.get("/snaptrade/context")
    async def snaptrade_context(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        if not mapping.snaptrade_user_secret:
            if is_personal_client(context):
                connections, error_response = await fetch_connections(context, mapping)
                if error_response:
                    return error_response
                return JSONResponse([_trim_connection(c) for c in (connections or [])])
            return JSONResponse(
                {
                    "error": "user_registration_failed",
                    "detail": "Could not register/load SnapTrade user for this client_id/openbb_user_id mapping.",
                },
                status_code=502,
            )

        connections, error_response = await fetch_connections(context, mapping)
        if error_response:
            return error_response

        return JSONResponse([_trim_connection(c) for c in (connections or [])])

    @app.get("/snaptrade/connections")
    async def list_connections(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        connections, error_response = await fetch_connections(context, mapping)
        if error_response:
            return error_response
        return JSONResponse([_trim_connection(c) for c in (connections or [])])

    @app.delete("/snaptrade/connections/{connection_id}")
    async def delete_connection(connection_id: str, request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        if not is_personal_client(context) and not mapping.snaptrade_user_secret:
            return missing_user_secret_response()

        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)
        try:
            await asyncio.to_thread(
                client.connections.remove_brokerage_authorization,
                authorization_id=connection_id,
                user_id=user_id,
                user_secret=user_secret,
            )
        except Exception as exc:
            return JSONResponse(
                {"error": "delete_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        return JSONResponse({"success": True})

    @app.post("/snaptrade/logout")
    async def snaptrade_logout(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()
        return JSONResponse({"success": True})

    @app.post("/snaptrade/portal")
    async def snaptrade_portal(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        reconnect_id = payload.get("reconnect") if isinstance(payload, dict) else None
        broker = payload.get("broker") if isinstance(payload, dict) else None

        mapping = await ensure_mapping(context)
        if not is_personal_client(context) and not mapping.snaptrade_user_secret:
            return missing_user_secret_response()

        user_id, user_secret = snaptrade_credentials(context, mapping)

        configured_redirect = os.environ.get("SNAPTRADE_CONNECTION_REDIRECT", "").strip()
        runtime_redirect = f"{str(request.base_url).rstrip('/')}/widget"
        custom_redirect = configured_redirect or runtime_redirect

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(
                client.authentication.login_snap_trade_user,
                user_id=user_id,
                user_secret=user_secret,
                broker=broker,
                immediate_redirect=True,
                custom_redirect=custom_redirect,
                reconnect=reconnect_id,
                connection_type="read",
                connection_portal_version="v4",
            )
        except Exception as exc:
            return JSONResponse(
                {"error": "portal_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        data = getattr(response, "body", {})
        redirect_uri = ""
        if isinstance(data, dict):
            redirect_uri = data.get("redirectURI") or data.get("loginLink", "")
        elif isinstance(data, str):
            redirect_uri = data
        return JSONResponse({"redirect_uri": redirect_uri})
