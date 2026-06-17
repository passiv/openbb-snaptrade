import sys

from .config import APP_DIR, debug_headers_enabled  # noqa: I001 — must precede pywry import

from fastapi.staticfiles import StaticFiles
from pywry.inline import deploy, get_server_app

from .mcp_server import install as install_mcp
from .widgets import register_widget_modules


_DISABLED_BUILTIN_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def build_app():
    fastapi_app = get_server_app()
    fastapi_app.docs_url = None
    fastapi_app.redoc_url = None
    fastapi_app.openapi_url = None
    fastapi_app.swagger_ui_oauth2_redirect_url = None
    fastapi_app.router.routes = [
        route for route in fastapi_app.router.routes if getattr(route, "path", None) not in _DISABLED_BUILTIN_PATHS
    ]
    fastapi_app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    @fastapi_app.middleware("http")
    async def _allow_private_network(request, call_next):
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    if debug_headers_enabled():

        @fastapi_app.middleware("http")
        async def _log_request_headers(request, call_next):
            header_dump = {k: v for k, v in request.headers.items()}
            print(
                f"[SNAPTRADE_DEBUG] {request.method} {request.url.path}?{request.url.query} headers={header_dump}",
                file=sys.stderr,
                flush=True,
            )
            return await call_next(request)

    register_widget_modules(fastapi_app)
    install_mcp(fastapi_app)

    @fastapi_app.get("/health")
    async def health():
        return {"status": "ok"}

    return fastapi_app


app = build_app()


if __name__ == "__main__":
    deploy()
