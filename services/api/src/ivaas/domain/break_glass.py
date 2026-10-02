"""Break-glass support access (proposal §4.1, M8 T8.3).

Platform Support holds no standing access to a tenant's data. To look, a support
engineer asks one tenant, for a reason and a length of time; the tenant's owner
approves or refuses; and the access, once approved, ends on its own. It is
read-only: support looks, it does not change anything. Every request made with it is
written into the tenant's own audit log, where the owner can read it.

The clock starts at approval, not at the request: an owner who approves at 16:00 a
request made at 09:00 gives the hour they meant to give.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

#: the shortest and longest a grant may run
MIN_DURATION = timedelta(minutes=15)
MAX_DURATION = timedelta(hours=8)
#: a request nobody answers lapses rather than waiting to be approved next month
PENDING_FOR = timedelta(hours=24)


class BreakGlassError(ValueError):
    pass


class GrantState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DENIED = "denied"
    #: ended early, by the owner or by support
    ENDED = "ended"
    EXPIRED = "expired"
    #: nobody answered in time
    LAPSED = "lapsed"


@dataclass
class BreakGlassGrant:
    tenant_id: UUID
    requested_by: str
    reason: str
    duration: timedelta
    requested_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    approved: bool | None = None
    ended_by: str | None = None
    ended_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    @staticmethod
    def request(
        tenant_id: UUID, by: str, reason: str, duration: timedelta, now: datetime
    ) -> BreakGlassGrant:
        if not reason.strip():
            raise BreakGlassError("say why access is needed: the tenant decides on it")
        if not MIN_DURATION <= duration <= MAX_DURATION:
            raise BreakGlassError("access runs between 15 minutes and 8 hours")
        return BreakGlassGrant(tenant_id, by, reason.strip(), duration, now)

    @property
    def expires_at(self) -> datetime | None:
        """When approved access ends; None until it is approved."""
        if not self.approved or self.decided_at is None:
            return None
        return self.decided_at + self.duration

    def state(self, now: datetime) -> GrantState:
        if self.approved is False:
            return GrantState.DENIED
        if self.approved is None:
            if self.ended_at is not None:
                return GrantState.ENDED  # withdrawn before anyone answered
            if now >= self.requested_at + PENDING_FOR:
                return GrantState.LAPSED
            return GrantState.PENDING
        if self.ended_at is not None:
            return GrantState.ENDED
        assert self.expires_at is not None
        return GrantState.ACTIVE if now < self.expires_at else GrantState.EXPIRED

    def decide(self, approve: bool, by: str, now: datetime) -> None:
        if self.state(now) is not GrantState.PENDING:
            raise BreakGlassError(f"this request is {self.state(now).value}, not waiting")
        self.approved, self.decided_by, self.decided_at = approve, by, now

    def end(self, by: str, now: datetime) -> None:
        if self.state(now) not in (GrantState.PENDING, GrantState.ACTIVE):
            raise BreakGlassError(f"this access is already {self.state(now).value}")
        self.ended_by, self.ended_at = by, now
