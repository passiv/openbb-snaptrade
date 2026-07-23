import asyncio

from fastapi.responses import JSONResponse
from snaptrade_client import SnapTrade

from .config import REDIS_URL, STATE_BACKEND, STORE_ENCRYPTION_KEY_B64
from .context import WorkspaceContext
from .user_store import MemoryUserMapStore, UserMapping, UserMapStore


# USER_STORE is retained purely as a Redis-backed cache/cipher (broker-instrument
# caching in reference_data, session encryption in auth). Per-user SnapTrade
# mappings are no longer stored: this integration only supports personal keys,
# which authenticate with the client_id/consumer_key directly and need no
# per-user registration.
if STATE_BACKEND == "memory":
    USER_STORE = MemoryUserMapStore(encryption_key_b64=STORE_ENCRYPTION_KEY_B64)
else:
    USER_STORE = UserMapStore(redis_url=REDIS_URL, encryption_key_b64=STORE_ENCRYPTION_KEY_B64)


PERSONAL_CLIENT_PREFIX = "PERS-"

NON_PERSONAL_CLIENT_ERROR = {
    "error": "personal_key_required",
    "detail": (
        "This integration only supports personal SnapTrade keys. Generate a "
        "personal client ID and consumer key from the SnapTrade dashboard, then "
        "reconfigure the integration with those credentials."
    ),
}


class NonPersonalClientError(Exception):
    """Raised when a non-personal SnapTrade client_id is used.

    Personal keys are the only supported configuration; every other client_id is
    rejected with a clear error (see NON_PERSONAL_CLIENT_ERROR).
    """


def is_personal_client(context: WorkspaceContext) -> bool:
    return context.client_id.upper().startswith(PERSONAL_CLIENT_PREFIX)


def require_personal_client(context: WorkspaceContext) -> None:
    """Raise NonPersonalClientError unless the client_id is a personal key."""
    if not is_personal_client(context):
        raise NonPersonalClientError()


def non_personal_client_response() -> JSONResponse:
    return JSONResponse(NON_PERSONAL_CLIENT_ERROR, status_code=403)


def snaptrade_client(context: WorkspaceContext) -> SnapTrade:
    return SnapTrade(
        client_id=context.client_id,
        consumer_key=context.consumer_key,
    )


def snaptrade_credentials(context: WorkspaceContext, mapping: UserMapping) -> tuple[str, str]:
    # Personal keys authenticate with the client_id/consumer_key alone; the SDK
    # is called with empty user credentials.
    return "", ""


async def ensure_mapping(context: WorkspaceContext) -> UserMapping:
    """Validate the client is a personal key and return an empty mapping.

    Personal keys need no per-user SnapTrade registration, so there is nothing to
    persist. Non-personal keys are rejected with NonPersonalClientError.
    """
    require_personal_client(context)
    return UserMapping(
        client_id=context.client_id,
        openbb_user_id=context.openbb_user_id,
        snaptrade_user_id="",
        snaptrade_user_secret="",
    )


async def fetch_connections(context: WorkspaceContext, mapping: UserMapping):
    require_personal_client(context)
    user_id, user_secret = snaptrade_credentials(context, mapping)
    client = snaptrade_client(context)
    try:
        response = await asyncio.to_thread(
            client.connections.list_brokerage_authorizations,
            user_id=user_id,
            user_secret=user_secret,
        )
    except Exception as exc:
        return None, JSONResponse(
            {"error": "fetch_failed", "detail": getattr(exc, "body", str(exc))},
            status_code=502,
        )

    data = getattr(response, "body", [])
    return data if isinstance(data, list) else [], None


async def fetch_accounts(context: WorkspaceContext, mapping: UserMapping):
    require_personal_client(context)
    user_id, user_secret = snaptrade_credentials(context, mapping)
    client = snaptrade_client(context)
    try:
        response = await asyncio.to_thread(
            client.account_information.list_user_accounts,
            user_id=user_id,
            user_secret=user_secret,
        )
    except Exception as exc:
        return None, JSONResponse(
            {"error": "accounts_fetch_failed", "detail": getattr(exc, "body", str(exc))},
            status_code=502,
        )

    data = getattr(response, "body", [])
    return data if isinstance(data, list) else [], None
