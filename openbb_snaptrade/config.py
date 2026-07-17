import base64
import os
from hashlib import sha256
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"


def _load_env_files() -> None:
    """Load .env files if enabled."""
    if os.environ.get("OPENBB_SNAPTRADE_LOAD_DOTENV", "").strip().lower() in {"1", "true", "yes", "on"}:
        load_dotenv(PROJECT_ROOT / ".env")
        load_dotenv(APP_DIR / ".env")


def _configure_pywry_defaults() -> None:
    """Configure PyWry defaults via environment variables."""
    # Server configuration
    os.environ.setdefault("PYWRY_SERVER__HOST", "0.0.0.0")
    os.environ.setdefault("PYWRY_SERVER__PORT", "8069")
    os.environ.setdefault("PYWRY_HEADLESS", "1")

    # Deploy configuration
    os.environ.setdefault("PYWRY_DEPLOY__STATE_BACKEND", "redis")

    # CORS configuration
    cors_origins = os.environ.get("PYWRY_SERVER__CORS_ORIGINS")
    if not cors_origins:
        os.environ["PYWRY_SERVER__CORS_ORIGINS"] = ",".join(
            [
                "https://pro.openbb.co",
                "https://pro.openbb.dev",
                "https://excel.openbb.co",
                "https://excel.openbb.dev",
                "http://localhost:1420",
                "http://localhost:5050",
                "https://localhost:8443",
            ]
        )

    os.environ.setdefault("PYWRY_SERVER__CORS_ALLOW_CREDENTIALS", "true")
    os.environ.setdefault("PYWRY_SERVER__CORS_ALLOW_METHODS", "GET,POST,PUT,PATCH,DELETE,OPTIONS,HEAD")


_load_env_files()
_configure_pywry_defaults()

SNAPTRADE_API_BASE = "https://api.snaptrade.com"

REDIS_URL = os.environ.get("PYWRY_DEPLOY__REDIS_URL", "redis://localhost:6379/0")

# "redis" (default, matches upstream) or "memory" (single-instance deployments;
# no Redis anywhere — sessions and user mappings live in process memory and are
# lost on restart, which just means users reload the dashboard).
STATE_BACKEND = os.environ.get("SNAPTRADE_STATE_BACKEND", "redis").strip().lower()

STORE_ENCRYPTION_KEY_B64 = os.environ.get("SNAPTRADE_STORE_ENCRYPTION_KEY_B64", "").strip()
if not STORE_ENCRYPTION_KEY_B64:
    derived_key = sha256(str(PROJECT_ROOT).encode("utf-8")).digest()
    STORE_ENCRYPTION_KEY_B64 = base64.b64encode(derived_key).decode("utf-8")


BROKER_INSTRUMENTS_CACHE_PREFIX = "snaptrade:broker_instruments:"
BROKER_INSTRUMENTS_CACHE_TTL_SECONDS = 60 * 60 * 24


IFRAME_HEADERS = {
    "Content-Security-Policy": "frame-ancestors *;",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Cross-Origin-Embedder-Policy": "unsafe-none",
    "Referrer-Policy": "no-referrer",
}


CRYPTO_BROKERAGE_SLUGS = frozenset(
    {
        "KRAKEN",
        "COINBASE",
        "COINBASEPRO",
        "GEMINI",
        "BINANCE",
        "BINANCEUS",
        "BITBUY",
        "BITGET",
        "KUCOIN",
        "OKX",
    }
)


OPTIONS_BROKERAGE_SLUGS = frozenset(
    {
        "TASTYTRADE",
        "TRADIER",
        "INTERACTIVEBROKERS",
        "TDAMERITRADE",
        "SCHWAB",
        "ETRADE",
        "FIDELITY",
        "WEBULL",
        "ROBINHOOD",
    }
)


def debug_headers_enabled() -> bool:
    return os.environ.get("SNAPTRADE_DEBUG_HEADERS", "").strip().lower() in {"1", "true", "yes", "on"}
