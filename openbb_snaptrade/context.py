import re
from hashlib import sha256
from typing import NamedTuple

from fastapi import Request


CLIENT_ID_HEADERS = (
    "x-openbb-snaptrade-client-id",
    "x-snaptrade-client-id",
    "snaptrade-client-id",
)

CONSUMER_KEY_HEADERS = (
    "x-openbb-snaptrade-consumer-key",
    "x-snaptrade-consumer-key",
    "snaptrade-consumer-key",
)

OPENBB_USER_HEADERS = ("x-openbb-user",)


class WorkspaceContext(NamedTuple):
    client_id: str
    consumer_key: str
    openbb_user_id: str


def header_value(request: Request, names: tuple[str, ...]) -> str:
    for name in names:
        value = request.headers.get(name)
        if value:
            return value.strip()
    return ""


def email_hash(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        return ""
    return sha256(normalized.encode("utf-8")).hexdigest()


def sanitize(value: str, limit: int = 64) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "-", value).strip("-")
    if not normalized:
        return ""
    return normalized[:limit]


async def resolve_workspace_context(request: Request) -> WorkspaceContext | None:
    # Preferred path: signed session token issued by the auth manager. The token
    # is HMAC-signed and resolves server-side via the pywry RedisSessionStore,
    # so the iframe never carries the actual SnapTrade client_id/consumer_key.
    from .auth import SESSION_MANAGER
    from .iframe import extract_session_token

    token = extract_session_token(request)
    if token:
        ctx = await SESSION_MANAGER.resolve(token)
        if ctx:
            return ctx

    # Fallback: direct headers (used by /widgets.json and the initial /apps.json hit
    # where Workspace forwards the configured SnapTrade headers).
    client_id = header_value(request, CLIENT_ID_HEADERS)
    consumer_key = header_value(request, CONSUMER_KEY_HEADERS)
    if not client_id or not consumer_key:
        return None

    openbb_user_email = header_value(request, OPENBB_USER_HEADERS) or "local.user@example.com"
    openbb_user_id = email_hash(openbb_user_email)
    if not openbb_user_id:
        openbb_user_id = sha256(b"local-user").hexdigest()

    return WorkspaceContext(
        client_id=client_id,
        consumer_key=consumer_key,
        openbb_user_id=openbb_user_id,
    )
