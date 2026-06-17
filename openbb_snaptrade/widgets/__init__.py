import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..auth import SESSION_MANAGER
from ..config import STATIC_DIR
from ..context import (
    CLIENT_ID_HEADERS,
    CONSUMER_KEY_HEADERS,
    OPENBB_USER_HEADERS,
    header_value,
)
from . import (
    connection_portal,
    portfolio_overview,
    reference_data,
    sdk_passthrough,
    snaptrade_trade,
)


IFRAME_ENDPOINTS = ("widget", "portfolio-overview", "trade")


def register_widget_modules(app: FastAPI) -> None:
    connection_portal.register(app)
    portfolio_overview.register(app)
    reference_data.register(app)
    snaptrade_trade.register(app)
    sdk_passthrough.register(app)
    _register_root_config(app)


def _load_root_json(filename: str):
    target = STATIC_DIR / filename
    if not target.exists():
        return None, JSONResponse({"error": "config_not_found", "file": filename}, status_code=404)
    try:
        with target.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data, None
    except json.JSONDecodeError as exc:
        return None, JSONResponse(
            {"error": "invalid_config_json", "file": filename, "detail": str(exc)},
            status_code=500,
        )


def _public_base_url(request: Request) -> str:
    proto = (
        request.headers.get("x-forwarded-proto")
        or request.url.scheme
        or "https"
    ).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    ).split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/")


def _register_root_config(app: FastAPI) -> None:
    @app.get("/widgets.json")
    async def widgets_config(request: Request):
        data, error_response = _load_root_json("widgets.json")
        if error_response:
            return error_response

        client_id = header_value(request, CLIENT_ID_HEADERS)
        consumer_key = header_value(request, CONSUMER_KEY_HEADERS)
        openbb_user = header_value(request, OPENBB_USER_HEADERS)
        if client_id and consumer_key and openbb_user and isinstance(data, dict):
            token = await SESSION_MANAGER.mint(client_id, consumer_key, openbb_user)
            base = _public_base_url(request)
            endpoint_map = {name: f"{base}/{name}/s/{token}" for name in IFRAME_ENDPOINTS}
            mcp_url = f"{base}/mcp/u/{token}"
            for widget in data.values():
                if not isinstance(widget, dict):
                    continue
                if widget.get("type") != "iframe":
                    continue
                current = widget.get("endpoint")
                if isinstance(current, str) and current in endpoint_map:
                    widget["endpoint"] = endpoint_map[current]
                storage = widget.get("storage")
                if isinstance(storage, dict) and storage.get("mcpUrl") == "mcp":
                    storage["mcpUrl"] = mcp_url

        return JSONResponse(content=data)

    @app.get("/apps.json")
    async def apps_config():
        data, error_response = _load_root_json("apps.json")
        if error_response:
            return error_response
        return JSONResponse(content=data)
