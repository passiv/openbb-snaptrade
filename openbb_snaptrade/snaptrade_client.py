import asyncio
from hashlib import sha256

from fastapi.responses import JSONResponse
from snaptrade_client import SnapTrade

from .config import REDIS_URL, STATE_BACKEND, STORE_ENCRYPTION_KEY_B64
from .context import WorkspaceContext
from .user_store import MemoryUserMapStore, UserMapping, UserMapStore


if STATE_BACKEND == "memory":
    USER_STORE = MemoryUserMapStore(encryption_key_b64=STORE_ENCRYPTION_KEY_B64)
else:
    USER_STORE = UserMapStore(redis_url=REDIS_URL, encryption_key_b64=STORE_ENCRYPTION_KEY_B64)


def is_personal_client(context: WorkspaceContext) -> bool:
    return context.client_id.upper().startswith("PERS-")


def build_snaptrade_user_id(context: WorkspaceContext) -> str:
    digest = sha256(f"{context.client_id}:{context.openbb_user_id}".encode("utf-8")).hexdigest()
    return f"obb-{digest[:24]}"


def snaptrade_client(context: WorkspaceContext) -> SnapTrade:
    return SnapTrade(
        client_id=context.client_id,
        consumer_key=context.consumer_key,
    )


def snaptrade_credentials(context: WorkspaceContext, mapping: UserMapping) -> tuple[str, str]:
    if is_personal_client(context):
        return "", ""
    return mapping.snaptrade_user_id, mapping.snaptrade_user_secret


def missing_user_secret_response() -> JSONResponse:
    return JSONResponse(
        {
            "error": "missing_user_secret",
            "detail": "No stored SnapTrade userSecret for this Workspace client/user mapping.",
        },
        status_code=409,
    )


async def _register_or_reset_user_secret(context: WorkspaceContext, snaptrade_user_id: str) -> str:
    client = snaptrade_client(context)

    try:
        register_response = await asyncio.to_thread(
            client.authentication.register_snap_trade_user,
            user_id=snaptrade_user_id,
        )
        register_body = getattr(register_response, "body", {}) or {}
        if isinstance(register_body, dict):
            user_secret = register_body.get("userSecret", "")
            if user_secret:
                return user_secret
    except Exception:
        pass

    try:
        await asyncio.to_thread(
            client.authentication.delete_snap_trade_user,
            user_id=snaptrade_user_id,
        )
    except Exception:
        pass

    try:
        register_response = await asyncio.to_thread(
            client.authentication.register_snap_trade_user,
            user_id=snaptrade_user_id,
        )
        register_body = getattr(register_response, "body", {}) or {}
        if isinstance(register_body, dict):
            user_secret = register_body.get("userSecret", "")
            if user_secret:
                return user_secret
    except Exception:
        return ""

    return ""


async def ensure_mapping(context: WorkspaceContext) -> UserMapping:
    if is_personal_client(context):
        return UserMapping(
            client_id=context.client_id,
            openbb_user_id=context.openbb_user_id,
            snaptrade_user_id="",
            snaptrade_user_secret="",
        )

    existing = USER_STORE.get(context.client_id, context.openbb_user_id)
    if existing and existing.snaptrade_user_secret:
        return existing

    candidate_user_ids: list[str] = []
    if existing and existing.snaptrade_user_id:
        candidate_user_ids.append(existing.snaptrade_user_id)

    derived_user_id = build_snaptrade_user_id(context)
    if derived_user_id not in candidate_user_ids:
        candidate_user_ids.append(derived_user_id)

    for snaptrade_user_id in candidate_user_ids:
        user_secret = await _register_or_reset_user_secret(context, snaptrade_user_id)
        if not user_secret:
            continue
        mapping = UserMapping(
            client_id=context.client_id,
            openbb_user_id=context.openbb_user_id,
            snaptrade_user_id=snaptrade_user_id,
            snaptrade_user_secret=user_secret,
        )
        USER_STORE.upsert(mapping)
        return mapping

    return UserMapping(
        client_id=context.client_id,
        openbb_user_id=context.openbb_user_id,
        snaptrade_user_id=derived_user_id,
        snaptrade_user_secret="",
    )


async def fetch_connections(context: WorkspaceContext, mapping: UserMapping):
    if not is_personal_client(context) and not mapping.snaptrade_user_secret:
        return None, missing_user_secret_response()

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
    if not is_personal_client(context) and not mapping.snaptrade_user_secret:
        return None, missing_user_secret_response()

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
