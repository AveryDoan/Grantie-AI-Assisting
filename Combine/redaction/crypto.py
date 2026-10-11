"""Application-level encryption of token-map originals (AES-256-GCM).

Why application-level (not pgcrypto): the key never reaches the database, so
a database dump, a Supabase operator or a SQL injection cannot read originals.

- Key: 32 bytes, base64, from the environment variable REDACTION_KEY.
  Optional REDACTION_KEY_PREVIOUS lets old rows decrypt during key rotation.
- Each value gets a fresh 96-bit nonce.
- Associated data binds a ciphertext to its application and token, so a
  ciphertext copied onto another row will not decrypt.
- Stored form: "v1:<key-id>:<base64(nonce + ciphertext)>". The key id is a
  short hash of the key (not the key) so rotation can pick the right one.
- No key -> RedactionKeyMissing: originals are never stored unencrypted.
- Errors never include plaintext.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "v1"


class RedactionKeyMissing(RuntimeError):
    pass


class DecryptionFailed(RuntimeError):
    pass


def generate_key() -> str:
    """A new random key, base64 - for .env (python -m redaction.crypto)."""
    return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()


def _load(b64: str) -> bytes:
    key = base64.b64decode(b64.strip())
    if len(key) != 32:
        raise RedactionKeyMissing("REDACTION_KEY must be 32 bytes, base64-encoded (python -m redaction.crypto)")
    return key


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


class TokenCipher:
    def __init__(self, key_b64: str | None = None, previous_b64: str | None = None) -> None:
        key_b64 = key_b64 if key_b64 is not None else os.environ.get("REDACTION_KEY", "")
        previous_b64 = previous_b64 if previous_b64 is not None else os.environ.get("REDACTION_KEY_PREVIOUS", "")
        if not key_b64:
            raise RedactionKeyMissing("REDACTION_KEY is not set; refusing to store token maps unencrypted")
        current = _load(key_b64)
        self._current_id = _key_id(current)
        self._keys = {self._current_id: AESGCM(current)}
        if previous_b64:
            prev = _load(previous_b64)
            self._keys.setdefault(_key_id(prev), AESGCM(prev))

    @staticmethod
    def _aad(application_id: str, token: str) -> bytes:
        return f"{application_id}|{token}".encode()

    def encrypt(self, application_id: str, token: str, plaintext: str) -> str:
        nonce = os.urandom(12)
        ct = self._keys[self._current_id].encrypt(nonce, plaintext.encode("utf-8"), self._aad(application_id, token))
        return f"{PREFIX}:{self._current_id}:{base64.b64encode(nonce + ct).decode()}"

    def decrypt(self, application_id: str, token: str, blob: str) -> str:
        try:
            prefix, key_id, payload = blob.split(":", 2)
        except ValueError:
            raise DecryptionFailed("not a v1 ciphertext") from None
        if prefix != PREFIX or key_id not in self._keys:
            raise DecryptionFailed(f"unknown key or format ({prefix}:{key_id})")
        raw = base64.b64decode(payload)
        try:
            return self._keys[key_id].decrypt(raw[:12], raw[12:], self._aad(application_id, token)).decode("utf-8")
        except InvalidTag:
            raise DecryptionFailed("ciphertext does not match this application/token or was altered") from None


def cipher_from_settings(settings: object) -> TokenCipher:
    """Build the cipher from app settings (which read .env)."""
    key = settings.redaction_key.get_secret_value()  # type: ignore[attr-defined]
    prev = settings.redaction_key_previous.get_secret_value()  # type: ignore[attr-defined]
    return TokenCipher(key, prev)


if __name__ == "__main__":
    print(generate_key())
