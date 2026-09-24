"""Use cases for the loading-session lifecycle.

Each use case is a small class with one reason to change. Dependencies are
injected as ports, so every one of these is unit-testable with in-memory fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from ivaas.domain.models import (
    ApprovalReason,
    CrateCrossing,
    LoadingSession,
    NotFoundError,
    PlateRead,
    SessionDirection,
    SessionStatus,
)
from ivaas.ports.repositories import (
    BayReader,
    Clock,
    EventPublisher,
    SessionReader,
    SessionWriter,
)

SUBJECT_SESSION_OPENED = "ivaas.session.opened"
SUBJECT_SESSION_UPDATED = "ivaas.session.updated"
SUBJECT_SESSION_CLOSED = "ivaas.session.closed"
SUBJECT_SESSION_RECONCILED = "ivaas.session.reconciled"
SUBJECT_SESSION_APPROVED = "ivaas.session.approved"


def session_payload(session: LoadingSession) -> dict:
    return {
        "id": str(session.id),
        "bay_id": str(session.bay_id),
        "direction": session.direction.value,
        "status": session.status.value,
        "plate": session.plate,
        "ai_count": session.ai_count,
        "manual_count": session.manual_count,
        "variance": session.variance,
        "accuracy": session.accuracy,
        "opened_at": session.opened_at.isoformat(),
        "closed_at": session.closed_at.isoformat() if session.closed_at else None,
    }


class _SessionStore(SessionReader, SessionWriter, Protocol):
    """Read+write view, for the use cases that genuinely need both."""


@dataclass
class OpenSession:
    bays: BayReader
    sessions: _SessionStore
    events: EventPublisher
    clock: Clock

    async def __call__(self, bay_id: UUID, direction: SessionDirection) -> LoadingSession:
        if await self.bays.get(bay_id) is None:
            raise NotFoundError(f"bay {bay_id} not found")
        existing = await self.sessions.get_open_for_bay(bay_id)
        if existing is not None:
            return existing  # idempotent: one open session per bay
        session = LoadingSession(bay_id=bay_id, direction=direction, opened_at=self.clock.now())
        await self.sessions.save(session)
        await self.events.publish(SUBJECT_SESSION_OPENED, session_payload(session))
        return session


@dataclass
class RecordPlateRead:
    """A confirmed plate arrives. Attach it to the open session, or open one.

    With `auto_open` set, a truck arriving at an idle bay starts its own session in
    `auto_open_direction`; the operator only intervenes to change direction or to
    end it. A second, different plate while a session is open is *not* applied: the
    LPR camera can see a truck waiting behind the one being loaded.
    """

    sessions: _SessionStore
    events: EventPublisher
    auto_open: OpenSession | None = None
    auto_open_direction: SessionDirection = SessionDirection.LOADING

    async def __call__(self, bay_id: UUID, read: PlateRead) -> LoadingSession | None:
        session = await self.sessions.get_open_for_bay(bay_id)
        if session is None:
            if self.auto_open is None:
                return None
            session = await self.auto_open(bay_id, self.auto_open_direction)
        elif session.plate is not None and session.plate != read.plate:
            return session  # keep the truck being loaded; the newcomer is queued behind it
        session.attach_plate(read)  # also refreshes plate_last_seen_at
        await self.sessions.save(session)
        await self.events.publish(SUBJECT_SESSION_UPDATED, session_payload(session))
        return session


@dataclass
class RecordCrateCrossing:
    sessions: _SessionStore
    events: EventPublisher

    async def __call__(self, bay_id: UUID, crossing: CrateCrossing) -> LoadingSession | None:
        session = await self.sessions.get_open_for_bay(bay_id)
        if session is None:
            return None  # crossings outside a session are dropped, not guessed at
        session.record_crossing(crossing)
        await self.sessions.save(session)
        await self.events.publish(SUBJECT_SESSION_UPDATED, session_payload(session))
        return session


@dataclass
class CloseSession:
    sessions: _SessionStore
    events: EventPublisher
    clock: Clock

    async def __call__(self, session_id: UUID) -> LoadingSession:
        session = await self.sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"session {session_id} not found")
        session.close(self.clock.now())
        await self.sessions.save(session)
        await self.events.publish(SUBJECT_SESSION_CLOSED, session_payload(session))
        return session


@dataclass
class ReconcileSession:
    sessions: _SessionStore
    events: EventPublisher
    tolerance: float = 0.95  # POC success criterion: >95% vs manual verification

    async def __call__(self, session_id: UUID, manual_count: int) -> LoadingSession:
        session = await self.sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"session {session_id} not found")
        session.reconcile(manual_count, self.tolerance)
        await self.sessions.save(session)
        await self.events.publish(SUBJECT_SESSION_RECONCILED, session_payload(session))
        return session


@dataclass
class ApproveSession:
    """Sign off a disputed load: a person accepted the discrepancy, and why.

    Deliberately does not touch the counts, so accuracy reporting keeps counting
    the variance that actually occurred.
    """

    sessions: _SessionStore
    events: EventPublisher
    clock: Clock

    async def __call__(
        self,
        session_id: UUID,
        *,
        by: str,
        reason: ApprovalReason,
        note: str | None = None,
    ) -> LoadingSession:
        session = await self.sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"session {session_id} not found")
        session.approve(by=by, reason=reason, at=self.clock.now(), note=note)
        await self.sessions.save(session)
        await self.events.publish(SUBJECT_SESSION_APPROVED, session_payload(session))
        return session


@dataclass
class CloseIdleSessions:
    """Close sessions whose truck has not been seen for `idle_after`.

    The LPR camera re-reads a parked truck every couple of minutes (the voter's
    cooldown), so a gap much longer than that means it has driven off. Runs on a
    timer from the composition root; also safe to call from a request.
    """

    sessions: _SessionStore
    events: EventPublisher
    clock: Clock
    idle_after: timedelta = timedelta(minutes=10)

    async def __call__(self) -> list[LoadingSession]:
        now = self.clock.now()
        closed = []
        for session in await self.sessions.list_recent(status=SessionStatus.OPEN, limit=500):
            if session.plate is None:
                continue  # opened by hand and never read: leave it to the operator
            if session.idle_since(now) >= self.idle_after:
                session.close(now)
                await self.sessions.save(session)
                await self.events.publish(SUBJECT_SESSION_CLOSED, session_payload(session))
                closed.append(session)
        return closed
