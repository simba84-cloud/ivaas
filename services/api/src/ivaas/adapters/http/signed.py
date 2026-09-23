"""Short-lived signed links for objects that must load in <img>/<video> tags.

Browsers cannot attach a bearer token to an <img> or <video> request, so report
pages get URLs carrying an HMAC signature over (key, expiry). The object route
accepts either a valid bearer token or a valid, unexpired signature. Signed
links are minted only for callers who have already passed the viewer check.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote


class ObjectLinkSigner:
    def __init__(self, secret: str, ttl_s: int = 6 * 3600) -> None:
        if len(secret) < 16:
            raise ValueError("object link secret must be at least 16 characters")
        self._secret, self._ttl = secret.encode(), ttl_s

    def _mac(self, key: str, exp: int) -> str:
        return hmac.new(self._secret, f"{key}\n{exp}".encode(), hashlib.sha256).hexdigest()[:32]

    def sign(self, key: str) -> str:
        exp = int(time.time()) + self._ttl
        return f"/api/v1/objects/{quote(key, safe='/')}?exp={exp}&sig={self._mac(key, exp)}"

    def verify(self, key: str, exp: str | None, sig: str | None) -> bool:
        try:
            exp_i = int(exp or "")
        except ValueError:
            return False
        if exp_i < time.time() or not sig:
            return False
        return hmac.compare_digest(self._mac(key, exp_i), sig)
