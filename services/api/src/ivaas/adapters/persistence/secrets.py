"""Encryption at rest for camera credentials (Fernet: AES-128-CBC + HMAC-SHA256).

The key comes from IVAAS_SECRETS_KEY. Rotation: set the new key first in the list and
keep the old one after it; values are re-encrypted with the newest key whenever they are
saved. Losing every key means the stored URLs are gone and cameras must be re-registered.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

PREFIX = "enc:"


class SecretBox:
    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("at least one secrets key is required")
        self._fernet = MultiFernet([Fernet(k) for k in keys])

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def seal(self, value: str | None) -> str | None:
        if value is None:
            return None
        return PREFIX + self._fernet.encrypt(value.encode()).decode()

    def open(self, stored: str | None) -> str | None:
        if stored is None:
            return None
        if not stored.startswith(PREFIX):
            return stored  # a value written before encryption was enabled; re-sealed on next save
        try:
            return self._fernet.decrypt(stored[len(PREFIX) :].encode()).decode()
        except InvalidToken as exc:
            raise ValueError(
                "stored credential cannot be decrypted with the configured keys"
            ) from exc
