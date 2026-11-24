"""Use cases for controlled bays (ADR 0004): check a driver in, keep each counted
column, validate against the bay's standard, accept exceptions, and balance per
driver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from ivaas.domain.crate_control import (
    AcceptReason,
    CrateControlError,
    CrateTransaction,
    DriverBalance,
    StackObservation,
    TransactionStatus,
    driver_balances,
    validate,
)
from ivaas.domain.drivers import PIN_PATTERN, Driver, match_badge
from ivaas.domain.models import (
    CrateCrossing,
    DomainError,
    LoadingSession,
    NotFoundError,
    SessionDirection,
    SessionStatus,
)

SUBJECT_CRATE_CHECKED_IN = "ivaas.crate.checked_in"
SUBJECT_CRATE_VALIDATED = "ivaas.crate.validated"
SUBJECT_CRATE_ACCEPTED = "ivaas.crate.accepted"
MAX_DAYS = 90


class BayBusyError(DomainError):
    """The bay is already serving a driver, or a load the other way."""


class BadgeRefusedError(DomainError):
    """No active driver holds that badge, or the PIN was wrong. One message for
    both, so a kiosk cannot be used to find out which badges exist."""

    def __init__(self) -> None:
        super().__init__("badge or PIN not recognised")


def transaction_payload(t: CrateTransaction) -> dict:
    return {
        "id": str(t.id),
        "bay_id": str(t.bay_id),
        "driver_id": str(t.driver_id),
        "session_id": str(t.session_id),
        "direction": t.direction.value,
        "status": t.status.value,
        "declared_units": t.declared_units,
        "expected_crates": t.expected_crates,
        "counted_crates": t.counted_crates,
    }


@dataclass
class ObserveColumn:
    """Keeps every column counted at a controlled bay, so it can be judged against
    the standard. Other bays are left exactly as they were."""

    store: Any

    async def __call__(self, bay_id: UUID, session: LoadingSession, c: CrateCrossing) -> None:
        standard = await self.store.get_standard(bay_id)
        if standard is None or not standard.controlled:
            return
        await self.store.add_observation(
            StackObservation(
                session_id=session.id,
                track_id=c.track_id,
                camera_id=c.camera_id,
                direction=c.direction,
                crates=c.crates,
                confidence=c.confidence,
                agreement=c.agreement,
                at=c.crossed_at,
            )
        )


@dataclass
class CheckIn:
    store: Any
    sessions: Any
    open_session: Any
    close_session: Any
    hasher: Any
    events: Any
    clock: Any

    async def __call__(
        self,
        bay_id: UUID,
        *,
        badge: str,
        pin: str | None,
        direction: SessionDirection,
        declared_units: int,
        by: str,
    ) -> tuple[CrateTransaction, Driver, LoadingSession]:
        standard = await self.store.get_standard(bay_id)
        if standard is None or not standard.controlled:
            raise CrateControlError("this bay is not a controlled bay; set its standard first")
        driver = match_badge(badge, await self.store.list_drivers())
        if driver is None:
            raise BadgeRefusedError()
        if driver.has_pin and not (
            pin and PIN_PATTERN.match(pin) and self.hasher.verify(pin, driver.pin_hash)
        ):
            raise BadgeRefusedError()
        if (busy := await self.store.open_transaction_for_bay(bay_id)) is not None:
            raise BayBusyError(
                f"this bay is still serving a driver (checked in {busy.opened_at:%H:%M})"
            )

        session = await self.sessions.get_open_for_bay(bay_id)
        if session is not None and session.direction is not direction:
            if session.ai_count:
                raise BayBusyError(
                    f"a {session.direction.value} load with {session.ai_count} crates is still "
                    "open at this bay; close it first"
                )
            # opened by a plate read before the driver said which way: nothing counted yet
            await self.close_session(session.id)
            session = None
        if session is not None and await self.store.transaction_for_session(session.id):
            raise BayBusyError("the open load at this bay already belongs to a transaction")
        if session is None:
            session = await self.open_session(bay_id, direction)

        txn = CrateTransaction(
            bay_id=bay_id,
            driver_id=driver.id,
            session_id=session.id,
            direction=direction,
            declared_units=declared_units,
            standard_columns=standard.columns,
            standard_layers=standard.layers,
            opened_at=self.clock.now(),
            checked_in_by=by,
        )
        await self.store.save_transaction(txn)
        await self.events.publish(SUBJECT_CRATE_CHECKED_IN, transaction_payload(txn))
        return txn, driver, session


@dataclass
class CloseTransaction:
    """The driver is done: judge every column against the standard and record it."""

    store: Any
    sessions: Any
    close_session: Any
    events: Any
    clock: Any

    async def __call__(self, txn_id: UUID) -> tuple[CrateTransaction, Any]:
        txn = await self.store.get_transaction(txn_id)
        if txn is None:
            raise NotFoundError(f"transaction {txn_id} not found")
        if txn.status is not TransactionStatus.OPEN:
            raise CrateControlError("this transaction is already closed")
        result = validate(
            await self.store.observations_for(txn.session_id),
            txn.standard,
            txn.declared_units,
            txn.direction,
        )
        txn.close(result, self.clock.now())
        await self.store.save_transaction(txn)
        session = await self.sessions.get(txn.session_id)
        if session is not None and session.status is SessionStatus.OPEN:
            await self.close_session(session.id)
        await self.events.publish(SUBJECT_CRATE_VALIDATED, transaction_payload(txn))
        return txn, result


@dataclass
class AcceptTransaction:
    store: Any
    events: Any
    clock: Any

    async def __call__(
        self, txn_id: UUID, *, by: str, reason: AcceptReason, note: str | None
    ) -> CrateTransaction:
        txn = await self.store.get_transaction(txn_id)
        if txn is None:
            raise NotFoundError(f"transaction {txn_id} not found")
        txn.accept(by=by, at=self.clock.now(), reason=reason, note=note)
        await self.store.save_transaction(txn)
        await self.events.publish(SUBJECT_CRATE_ACCEPTED, transaction_payload(txn))
        return txn


@dataclass
class DriverBalanceQuery:
    store: Any
    clock: Any

    async def __call__(self, days: int = 30) -> list[DriverBalance]:
        since = self.clock.now() - timedelta(days=max(1, min(days, MAX_DAYS)))
        return driver_balances(await self.store.list_transactions(since, limit=20000))
