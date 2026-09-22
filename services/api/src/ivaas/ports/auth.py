"""Authentication port. Who is calling, and what may they do?"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class Role(StrEnum):
    VIEWER = "viewer"  # dashboards, live view, assistant
    OPERATOR = "operator"  # + open/close/reconcile sessions
    ADMIN = "admin"  # + cameras, discovery
    SERVICE = "service"  # the AI pipeline: ingest only


# Each role includes everything below it, except SERVICE which is deliberately narrow.
_RANK = {Role.VIEWER: 1, Role.OPERATOR: 2, Role.ADMIN: 3}


@dataclass(frozen=True)
class Principal:
    subject: str
    name: str
    roles: frozenset[Role] = field(default_factory=frozenset)

    def allows(self, required: Role) -> bool:
        if required is Role.SERVICE:
            return Role.SERVICE in self.roles
        have = max((_RANK.get(r, 0) for r in self.roles), default=0)
        return have >= _RANK[required]


class AuthError(Exception):
    pass


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Principal: ...
