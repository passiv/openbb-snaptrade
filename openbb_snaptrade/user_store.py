import base64
import json
import os
from hashlib import sha256
from dataclasses import dataclass
from datetime import datetime, timezone

from redis import Redis

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError as exc:
    raise RuntimeError("cryptography package is required for AES-256-CBC encrypted user store") from exc


@dataclass
class UserMapping:
    client_id: str
    openbb_user_id: str
    snaptrade_user_id: str
    snaptrade_user_secret: str


class UserMapStore:
    def __init__(self, redis_url: str, encryption_key_b64: str, key_prefix: str = "snaptrade:user_map") -> None:
        self.redis = Redis.from_url(redis_url, decode_responses=True)
        self.key_prefix = key_prefix
        self.key = self._decode_key(encryption_key_b64)

    @staticmethod
    def _decode_key(encryption_key_b64: str) -> bytes:
        raw = base64.b64decode(encryption_key_b64)
        if len(raw) != 32:
            raise ValueError("SNAPTRADE_STORE_ENCRYPTION_KEY_B64 must decode to exactly 32 bytes for AES-256-CBC")
        return raw

    @staticmethod
    def _pkcs7_pad(data: bytes) -> bytes:
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        return padder.update(data) + padder.finalize()

    @staticmethod
    def _pkcs7_unpad(data: bytes) -> bytes:
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        return unpadder.update(data) + unpadder.finalize()

    def _encrypt(self, plaintext: str) -> str:
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        padded = self._pkcs7_pad(plaintext.encode("utf-8"))
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        payload = {
            "v": 1,
            "alg": "AES-256-CBC",
            "iv": base64.b64encode(iv).decode("utf-8"),
            "ct": base64.b64encode(ciphertext).decode("utf-8"),
        }
        return json.dumps(payload, separators=(",", ":"))

    def encrypt(self, plaintext: str) -> str:
        return self._encrypt(plaintext)

    def decrypt(self, encrypted_payload: str) -> str:
        return self._decrypt(encrypted_payload)

    def _decrypt(self, encrypted_payload: str) -> str:
        payload = json.loads(encrypted_payload)
        if payload.get("v") != 1 or payload.get("alg") != "AES-256-CBC":
            raise ValueError("Unsupported encrypted payload format")
        iv = base64.b64decode(payload["iv"])
        ciphertext = base64.b64decode(payload["ct"])
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        plaintext = self._pkcs7_unpad(padded)
        return plaintext.decode("utf-8")

    def _key(self, client_id: str, openbb_user_id: str) -> str:
        digest = sha256(f"{client_id}:{openbb_user_id}".encode("utf-8")).hexdigest()
        return f"{self.key_prefix}:{digest}"

    def get(self, client_id: str, openbb_user_id: str) -> UserMapping | None:
        key = self._key(client_id, openbb_user_id)
        encrypted_payload = self.redis.get(key)
        if not encrypted_payload:
            return None
        row = json.loads(self._decrypt(encrypted_payload))
        return UserMapping(
            client_id=row.get("client_id", ""),
            openbb_user_id=row.get("openbb_user_id", ""),
            snaptrade_user_id=row.get("snaptrade_user_id", ""),
            snaptrade_user_secret=row.get("snaptrade_user_secret", ""),
        )

    def upsert(self, mapping: UserMapping) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        key = self._key(mapping.client_id, mapping.openbb_user_id)
        created_at = now
        existing_payload = self.redis.get(key)
        if existing_payload:
            try:
                existing_data = json.loads(self._decrypt(existing_payload))
                created_at = existing_data.get("created_at") or now
            except Exception:
                created_at = now

        payload = {
            "client_id": mapping.client_id,
            "openbb_user_id": mapping.openbb_user_id,
            "snaptrade_user_id": mapping.snaptrade_user_id,
            "snaptrade_user_secret": mapping.snaptrade_user_secret,
            "created_at": created_at,
            "updated_at": now,
        }
        self.redis.set(key, self._encrypt(json.dumps(payload, separators=(",", ":"))))


class _DictKV:
    """Process-local stand-in for the Redis client (only get/set are used)."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value


class MemoryUserMapStore(UserMapStore):
    """UserMapStore backed by process memory instead of Redis.

    For SNAPTRADE_STATE_BACKEND=memory single-instance deployments. Mappings
    are lost on restart — acceptable for personal-tier client IDs, which skip
    per-user SnapTrade registration and store nothing durable.
    """

    def __init__(self, encryption_key_b64: str, key_prefix: str = "snaptrade:user_map") -> None:
        self.redis = _DictKV()
        self.key_prefix = key_prefix
        self.key = self._decode_key(encryption_key_b64)
