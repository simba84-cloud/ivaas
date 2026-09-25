"""Password hashing with Argon2id.

OWASP's first choice for new systems, and the winner of the Password Hashing
Competition. The parameters below are OWASP's recommended minimum; argon2-cffi
records them inside the hash, so raising them later is safe and old hashes are
upgraded transparently the next time their owner signs in.
"""

from __future__ import annotations

import logging

from argon2 import PasswordHasher as Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

log = logging.getLogger(__name__)


class Argon2PasswordHasher:
    def __init__(self) -> None:
        # OWASP minimum for Argon2id: 19 MiB, 2 iterations, 1 degree of parallelism
        self._hasher = Argon2(memory_cost=19456, time_cost=2, parallelism=1)

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        except Exception:  # a malformed stored hash must not become a 500
            log.exception("password verification failed unexpectedly")
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except Exception:
            return False
