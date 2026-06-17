from fastapi import Request
from fastapi.responses import JSONResponse
from pywry.models import HtmlContent, WindowConfig
from pywry.config import PyWrySettings, SecuritySettings
from pywry.templates import build_html

from .auth import looks_like_token


_SNAPTRADE_FRAME_SOURCES = "https://app.snaptrade.com https://*.snaptrade.com https://connect.snaptrade.com"

_SECURITY_SETTINGS = SecuritySettings(
    default_src=f"'self' 'unsafe-inline' 'unsafe-eval' data: blob: {_SNAPTRADE_FRAME_SOURCES}",
)

_PYWRY_SETTINGS = PyWrySettings(csp=_SECURITY_SETTINGS)


def render_html_content(content: HtmlContent) -> str:
    return build_html(
        content=content,
        config=WindowConfig(),
        window_label="http-route",
        settings=_PYWRY_SETTINGS,
    )


def not_found_response() -> JSONResponse:
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def extract_session_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        candidate = auth[7:].strip()
        if looks_like_token(candidate):
            return candidate
    direct = request.headers.get("x-snaptrade-session", "").strip()
    if looks_like_token(direct):
        return direct
    return ""
