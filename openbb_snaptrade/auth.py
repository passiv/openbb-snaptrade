import json
import os
import re
import time
from hashlib import sha256

from pywry.state.auth import generate_session_token, validate_session_token
from pywry.state.redis import RedisSessionStore

from .config import REDIS_URL, STORE_ENCRYPTION_KEY_B64
from .context import WorkspaceContext, email_hash
from .user_store import UserMapStore


SESSION_TTL_SECONDS = 15 * 60
SESSION_PREFIX = "snaptrade_session"

_AUTH_SECRET = (
    os.environ.get("SNAPTRADE_AUTH_SECRET", "").strip()
    or sha256(("snaptrade-auth:" + STORE_ENCRYPTION_KEY_B64).encode("utf-8")).hexdigest()
)

_TOKEN_RE = re.compile(r"^[0-9a-f]+:[0-9]+:[0-9]+:[0-9a-f]+$")


def looks_like_token(value: str) -> bool:
    return bool(_TOKEN_RE.match(value or ""))


class WorkspaceSessionManager:
    def __init__(self) -> None:
        self._store = RedisSessionStore(
            redis_url=REDIS_URL,
            prefix=SESSION_PREFIX,
            default_ttl=SESSION_TTL_SECONDS,
        )
        self._cipher = UserMapStore(
            redis_url=REDIS_URL,
            encryption_key_b64=STORE_ENCRYPTION_KEY_B64,
            key_prefix=SESSION_PREFIX + ":cipher",
        )

    @staticmethod
    def _user_id_for(email: str) -> str:
        return email_hash(email) or sha256(b"local-user").hexdigest()

    async def mint(self, client_id: str, consumer_key: str, email: str) -> str:
        client_id = (client_id or "").strip()
        consumer_key = (consumer_key or "").strip()
        email = (email or "").strip()
        if not client_id or not consumer_key or not email:
            return ""

        user_id = self._user_id_for(email)
        encrypted = self._cipher._encrypt(
            json.dumps({"clientId": client_id, "consumerKey": consumer_key, "email": email})
        )
        await self._store.create_session(
            session_id=user_id,
            user_id=user_id,
            ttl=SESSION_TTL_SECONDS,
            metadata={"enc": encrypted},
        )
        return generate_session_token(
            user_id,
            _AUTH_SECRET,
            expires_at=time.time() + SESSION_TTL_SECONDS,
        )

    async def resolve(self, token: str) -> WorkspaceContext | None:
        if not looks_like_token(token):
            return None
        valid, user_id, _err = validate_session_token(token, _AUTH_SECRET)
        if not valid or not user_id:
            return None
        session = await self._store.get_session(user_id)
        if not session:
            return None
        enc = (session.metadata or {}).get("enc")
        if not enc:
            return None
        try:
            payload = json.loads(self._cipher._decrypt(enc))
        except Exception:
            return None
        client_id = (payload.get("clientId") or "").strip()
        consumer_key = (payload.get("consumerKey") or "").strip()
        if not client_id or not consumer_key:
            return None
        await self._store.refresh_session(user_id, extend_ttl=SESSION_TTL_SECONDS)
        return WorkspaceContext(
            client_id=client_id,
            consumer_key=consumer_key,
            openbb_user_id=user_id,
        )


SESSION_MANAGER = WorkspaceSessionManager()
