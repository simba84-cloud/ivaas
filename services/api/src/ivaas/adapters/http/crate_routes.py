"""Controlled bays over HTTP (ADR 0004): the driver register, bay standards, the bay
kiosk's check-in and close, supervisors' acceptances, and per-driver balances.

Every change is audited at the route after it has succeeded. A driver's PIN is
accepted here, hashed, and never returned: responses say only whether one is set.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.scope import require_bay
from ivaas.adapters.http.uploads import upload_text
from ivaas.application.crate_control import MAX_DAYS
from ivaas.domain.audit import AuditAction
from ivaas.domain.crate_control import (
    AcceptReason,
    BayStandard,
    ColumnVerdict,
    CrateControlError,
    CrateTransaction,
    DriverBalance,
    TransactionStatus,
    Validation,
    validate,
)
from ivaas.domain.drivers import Driver, DriverError, check_pin, normalize_badge
from ivaas.domain.models import NotFoundError, SessionDirection
from ivaas.domain.rbac import Permission as P
from ivaas.ports.auth import Principal

Audit = Callable[..., Awaitable[None]]
MAX_IMPORT_BYTES = 2 * 1024 * 1024


# --- schemas -------------------------------------------------------------------------


class DriverIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    badge_code: str = Field(min_length=3, max_length=40)
    employee_ref: str = Field(default="", max_length=40)
    phone: str = Field(default="", max_length=32)
    active: bool = True
    #: set on creation only; change it later with PUT /drivers/{id}/pin
    pin: str | None = Field(default=None, max_length=8)


class DriverOut(BaseModel):
    id: UUID
    name: str
    badge_code: str
    employee_ref: str
    phone: str
    active: bool
    has_pin: bool
    created_at: datetime | None

    @classmethod
    def of(cls, d: Driver) -> DriverOut:
        return cls(
            id=d.id,
            name=d.name,
            badge_code=d.badge_code,
            employee_ref=d.employee_ref,
            phone=d.phone,
            active=d.active,
            has_pin=d.has_pin,
            created_at=d.created_at,
        )


class PinIn(BaseModel):
    #: None removes the PIN
    pin: str | None = Field(default=None, max_length=8)


class ImportOut(BaseModel):
    added: int
    updated: int
    errors: list[str]


class StandardIn(BaseModel):
    columns: int = Field(ge=1, le=20)
    layers: int = Field(ge=1, le=40)
    controlled: bool = True


class StandardOut(BaseModel):
    bay_id: UUID
    columns: int
    layers: int
    crates_per_unit: int
    controlled: bool

    @classmethod
    def of(cls, s: BayStandard) -> StandardOut:
        return cls(
            bay_id=s.bay_id,
            columns=s.columns,
            layers=s.layers,
            crates_per_unit=s.crates_per_unit,
            controlled=s.controlled,
        )


class CheckInIn(BaseModel):
    badge: str = Field(min_length=3, max_length=40)
    pin: str | None = Field(default=None, max_length=8)
    direction: SessionDirection
    declared_units: int = Field(ge=1, le=50)


class ColumnOut(BaseModel):
    crates: int
    verdict: ColumnVerdict


class ValidationOut(BaseModel):
    status: TransactionStatus
    expected_columns: int
    expected_crates: int
    full_columns: int
    non_standard_columns: int
    unsure_columns: int
    counted_crates: int | None
    columns: list[ColumnOut]

    @classmethod
    def of(cls, v: Validation) -> ValidationOut:
        return cls(
            status=v.status,
            expected_columns=v.expected_columns,
            expected_crates=v.expected_crates,
            full_columns=v.full_columns,
            non_standard_columns=v.non_standard_columns,
            unsure_columns=v.unsure_columns,
            counted_crates=v.counted_crates,
            columns=[ColumnOut(crates=c, verdict=x) for c, x in v.columns],
        )


class TransactionOut(BaseModel):
    id: UUID
    bay_id: UUID
    bay_name: str | None
    driver_id: UUID
    driver_name: str | None
    badge_code: str | None
    session_id: UUID
    plate: str | None
    direction: SessionDirection
    declared_units: int
    standard_columns: int
    standard_layers: int
    expected_crates: int
    status: TransactionStatus
    validated_status: TransactionStatus | None
    opened_at: datetime
    closed_at: datetime | None
    checked_in_by: str
    full_columns: int | None
    non_standard_columns: int | None
    unsure_columns: int | None
    counted_crates: int | None
    accepted_by: str | None
    accepted_at: datetime | None
    accept_reason: AcceptReason | None
    accept_note: str | None
    #: while open: what the cameras have measured so far, judged against the standard
    progress: ValidationOut | None = None


class AcceptIn(BaseModel):
    reason: AcceptReason
    note: str | None = Field(default=None, max_length=280)


class DriverBalanceOut(BaseModel):
    driver_id: UUID
    driver_name: str | None
    badge_code: str | None
    issued: int
    returned: int
    outstanding: int
    dispatches: int
    returns: int
    in_progress: int
    exceptions: int
    #: transactions the cameras could not measure: not in the totals above
    unmeasured: int
    unmeasured_declared_out: int
    unmeasured_declared_back: int
    last_at: datetime | None

    @classmethod
    def of(cls, b: DriverBalance, driver: Driver | None) -> DriverBalanceOut:
        return cls(
            driver_id=b.driver_id,
            driver_name=driver.name if driver else None,
            badge_code=driver.badge_code if driver else None,
            issued=b.issued,
            returned=b.returned,
            outstanding=b.outstanding,
            dispatches=b.dispatches,
            returns=b.returns,
            in_progress=b.in_progress,
            exceptions=b.exceptions,
            unmeasured=b.unmeasured,
            unmeasured_declared_out=b.unmeasured_declared_out,
            unmeasured_declared_back=b.unmeasured_declared_back,
            last_at=b.last_at,
        )


# --- routes --------------------------------------------------------------------------


def add_crate_routes(app: FastAPI, get_container: Callable[[Request], Any], audit: Audit) -> None:
    async def describe(c: Any, t: CrateTransaction, *, live: bool = False) -> TransactionOut:
        driver = await c.crate_control.get_driver(t.driver_id)
        bay = await c.bays.get(t.bay_id)
        session = await c.sessions.get(t.session_id)
        progress = None
        if live and t.status is TransactionStatus.OPEN:
            obs = await c.crate_control.observations_for(t.session_id)
            progress = ValidationOut.of(validate(obs, t.standard, t.declared_units, t.direction))
        return TransactionOut(
            id=t.id,
            bay_id=t.bay_id,
            bay_name=bay.name if bay else None,
            driver_id=t.driver_id,
            driver_name=driver.name if driver else None,
            badge_code=driver.badge_code if driver else None,
            session_id=t.session_id,
            plate=session.plate if session else None,
            direction=t.direction,
            declared_units=t.declared_units,
            standard_columns=t.standard_columns,
            standard_layers=t.standard_layers,
            expected_crates=t.expected_crates,
            status=t.status,
            validated_status=t.validated_status,
            opened_at=t.opened_at,
            closed_at=t.closed_at,
            checked_in_by=t.checked_in_by,
            full_columns=t.full_columns,
            non_standard_columns=t.non_standard_columns,
            unsure_columns=t.unsure_columns,
            counted_crates=t.counted_crates,
            accepted_by=t.accepted_by,
            accepted_at=t.accepted_at,
            accept_reason=t.accept_reason,
            accept_note=t.accept_note,
            progress=progress,
        )

    async def badge_taken(c: Any, badge: str, exclude: UUID | None = None) -> Driver | None:
        key = normalize_badge(badge)
        return next(
            (
                d
                for d in await c.crate_control.list_drivers()
                if d.badge_code == key and d.id != exclude
            ),
            None,
        )

    def hashed_pin(c: Any, pin: str | None) -> str | None:
        if pin is None or pin == "":
            return None
        try:
            return c.hasher.hash(check_pin(pin))
        except DriverError as exc:
            raise HTTPException(422, str(exc)) from exc

    # --- the driver register -------------------------------------------------------------
    @app.get(
        "/api/v1/drivers",
        response_model=list[DriverOut],
        dependencies=[Depends(require(P.TOPOLOGY_READ, scoped=True))],
    )
    async def list_drivers(c: Any = Depends(get_container)) -> list[DriverOut]:
        return [DriverOut.of(d) for d in await c.crate_control.list_drivers()]

    @app.post(
        "/api/v1/drivers",
        response_model=DriverOut,
        status_code=201,
        dependencies=[Depends(require(P.SITE_MANAGE))],
    )
    async def add_driver(
        body: DriverIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> DriverOut:
        try:
            driver = Driver(
                **body.model_dump(exclude={"pin"}),
                pin_hash=hashed_pin(c, body.pin),
                created_at=c.clock.now(),
            )
        except DriverError as exc:
            raise HTTPException(422, str(exc)) from exc
        if (same := await badge_taken(c, driver.badge_code)) is not None:
            raise HTTPException(409, f"badge {driver.badge_code} already belongs to {same.name}")
        await c.crate_control.save_driver(driver)
        await audit(
            c,
            principal.name,
            AuditAction.DRIVER_SAVED,
            driver.name,
            badge=driver.badge_code,
            has_pin=driver.has_pin,
        )
        return DriverOut.of(driver)

    @app.put(
        "/api/v1/drivers/{driver_id}",
        response_model=DriverOut,
        dependencies=[Depends(require(P.SITE_MANAGE))],
    )
    async def update_driver(
        driver_id: UUID,
        body: DriverIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> DriverOut:
        driver = await c.crate_control.get_driver(driver_id)
        if driver is None:
            raise NotFoundError(f"driver {driver_id} not found")
        before = DriverOut.of(driver).model_dump(mode="json", exclude={"id", "created_at"})
        try:
            updated = Driver(
                **body.model_dump(exclude={"pin"}),
                pin_hash=driver.pin_hash,  # a PIN is changed only through /pin
                created_at=driver.created_at,
                id=driver.id,
            )
        except DriverError as exc:
            raise HTTPException(422, str(exc)) from exc
        if (same := await badge_taken(c, updated.badge_code, exclude=driver.id)) is not None:
            raise HTTPException(409, f"badge {updated.badge_code} already belongs to {same.name}")
        await c.crate_control.save_driver(updated)
        await audit(
            c,
            principal.name,
            AuditAction.DRIVER_SAVED,
            updated.name,
            before=before,
            after=DriverOut.of(updated).model_dump(mode="json", exclude={"id", "created_at"}),
        )
        return DriverOut.of(updated)

    @app.put(
        "/api/v1/drivers/{driver_id}/pin",
        response_model=DriverOut,
        dependencies=[Depends(require(P.SITE_MANAGE))],
    )
    async def set_pin(
        driver_id: UUID,
        body: PinIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> DriverOut:
        driver = await c.crate_control.get_driver(driver_id)
        if driver is None:
            raise NotFoundError(f"driver {driver_id} not found")
        driver.pin_hash = hashed_pin(c, body.pin)
        await c.crate_control.save_driver(driver)
        # the PIN itself is never recorded, only that it changed
        await audit(
            c, principal.name, AuditAction.DRIVER_PIN_SET, driver.name, has_pin=driver.has_pin
        )
        return DriverOut.of(driver)

    @app.post(
        "/api/v1/drivers/import",
        response_model=ImportOut,
        dependencies=[Depends(require(P.SITE_MANAGE))],
    )
    async def import_drivers(
        file: UploadFile = File(...),
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> ImportOut:
        """A CSV or Excel workbook (.xlsx) with `name` and `badge_code` columns, and
        optionally employee_ref and phone. A badge already registered is updated.
        PINs are never imported: each is set by hand, so none sits in a spreadsheet."""
        text = await upload_text(file, MAX_IMPORT_BYTES, needs={"name", "badge_code"})
        rows = csv.DictReader(io.StringIO(text))
        headers = {(h or "").strip().lower() for h in rows.fieldnames or []}
        if not {"name", "badge_code"} <= headers:
            raise HTTPException(422, "the file needs 'name' and 'badge_code' columns")
        existing = {d.badge_code: d for d in await c.crate_control.list_drivers()}
        added = updated = 0
        errors: list[str] = []
        for line, row in enumerate(rows, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            try:
                driver = Driver(
                    name=row.get("name", "")[:120],
                    badge_code=row.get("badge_code", ""),
                    employee_ref=row.get("employee_ref", "")[:40],
                    phone=row.get("phone", "")[:32],
                    created_at=c.clock.now(),
                )
            except DriverError as exc:
                errors.append(f"line {line}: {exc}")
                continue
            if (known := existing.get(driver.badge_code)) is not None:
                driver.id, driver.created_at = known.id, known.created_at
                driver.pin_hash, driver.active = known.pin_hash, known.active
                updated += 1
            else:
                added += 1
            existing[driver.badge_code] = driver
            await c.crate_control.save_driver(driver)
        await audit(
            c,
            principal.name,
            AuditAction.DRIVERS_IMPORTED,
            file.filename or "drivers.csv",
            added=added,
            updated=updated,
            errors=len(errors),
        )
        return ImportOut(added=added, updated=updated, errors=errors[:100])

    @app.get(
        "/api/v1/drivers/balances",
        response_model=list[DriverBalanceOut],
        dependencies=[Depends(require(P.COUNT_READ))],
    )
    async def balances(days: int = 30, c: Any = Depends(get_container)) -> list[DriverBalanceOut]:
        """Crates issued to and returned by each driver over the last `days` days
        (1-90), from what the cameras measured. Transactions that could not be
        measured are counted beside the totals with what was declared, never added in."""
        drivers = {d.id: d for d in await c.crate_control.list_drivers()}
        return [
            DriverBalanceOut.of(b, drivers.get(b.driver_id)) for b in await c.driver_balances(days)
        ]

    # --- bay standards ---------------------------------------------------------------------
    @app.get(
        "/api/v1/bay-standards",
        response_model=list[StandardOut],
        dependencies=[Depends(require(P.TOPOLOGY_READ, scoped=True))],
    )
    async def list_standards(c: Any = Depends(get_container)) -> list[StandardOut]:
        return [StandardOut.of(s) for s in await c.crate_control.list_standards()]

    @app.put(
        "/api/v1/bays/{bay_id}/standard",
        response_model=StandardOut,
        dependencies=[Depends(require(P.SITE_MANAGE))],
    )
    async def set_standard(
        bay_id: UUID,
        body: StandardIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> StandardOut:
        await require_bay(c, principal, P.SITE_MANAGE, bay_id)
        before = await c.crate_control.get_standard(bay_id)
        try:
            standard = BayStandard(bay_id, body.columns, body.layers, body.controlled)
        except CrateControlError as exc:
            raise HTTPException(422, str(exc)) from exc
        await c.crate_control.save_standard(standard)
        bay = await c.bays.get(bay_id)
        await audit(
            c,
            principal.name,
            AuditAction.BAY_STANDARD_SET,
            bay.name if bay else str(bay_id),
            before=StandardOut.of(before).model_dump(mode="json") if before else None,
            after=StandardOut.of(standard).model_dump(mode="json"),
        )
        return StandardOut.of(standard)

    # --- the bay kiosk ---------------------------------------------------------------------
    @app.post(
        "/api/v1/bays/{bay_id}/check-in",
        response_model=TransactionOut,
        status_code=201,
        dependencies=[Depends(require(P.SESSION_OPERATE, scoped=True))],
    )
    async def check_in(
        bay_id: UUID,
        body: CheckInIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> TransactionOut:
        await require_bay(c, principal, P.SESSION_OPERATE, bay_id)
        try:
            txn, driver, _ = await c.check_in(
                bay_id,
                badge=body.badge,
                pin=body.pin,
                direction=body.direction,
                declared_units=body.declared_units,
                by=principal.name,
            )
        except CrateControlError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            c,
            principal.name,
            AuditAction.CRATE_CHECK_IN,
            driver.name,
            badge=driver.badge_code,
            direction=txn.direction.value,
            declared_units=txn.declared_units,
            expected_crates=txn.expected_crates,
            transaction_id=str(txn.id),
            session_id=str(txn.session_id),
        )
        return await describe(c, txn, live=True)

    @app.get(
        "/api/v1/bays/{bay_id}/transaction",
        response_model=TransactionOut | None,
        dependencies=[Depends(require(P.SESSION_OPERATE, scoped=True))],
    )
    async def current_transaction(
        bay_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> TransactionOut | None:
        """The driver this bay is serving now, with the columns measured so far."""
        await require_bay(c, principal, P.SESSION_OPERATE, bay_id)
        txn = await c.crate_control.open_transaction_for_bay(bay_id)
        return await describe(c, txn, live=True) if txn else None

    async def owned(c: Any, principal: Principal, permission: P, txn_id: UUID) -> None:
        txn = await c.crate_control.get_transaction(txn_id)
        if txn is None:
            raise NotFoundError(f"transaction {txn_id} not found")
        await require_bay(c, principal, permission, txn.bay_id)

    @app.post(
        "/api/v1/crate-transactions/{txn_id}/close",
        response_model=TransactionOut,
        dependencies=[Depends(require(P.SESSION_OPERATE, scoped=True))],
    )
    async def close_transaction(
        txn_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> TransactionOut:
        await owned(c, principal, P.SESSION_OPERATE, txn_id)
        try:
            txn, result = await c.close_transaction(txn_id)
        except CrateControlError as exc:
            raise HTTPException(409, str(exc)) from exc
        driver = await c.crate_control.get_driver(txn.driver_id)
        await audit(
            c,
            principal.name,
            AuditAction.CRATE_TRANSACTION_CLOSED,
            driver.name if driver else str(txn.driver_id),
            status=txn.status.value,
            expected_crates=txn.expected_crates,
            counted_crates=txn.counted_crates,
            unsure_columns=txn.unsure_columns,
            transaction_id=str(txn.id),
        )
        out = await describe(c, txn)
        out.progress = ValidationOut.of(result)
        return out

    @app.post(
        "/api/v1/crate-transactions/{txn_id}/accept",
        response_model=TransactionOut,
        # a supervisor's sign-off, like approving a disputed load: never the operator
        # who ran the kiosk, so nobody accepts their own shortfall
        dependencies=[Depends(require(P.RECONCILIATION_RESOLVE, scoped=True))],
    )
    async def accept_transaction(
        txn_id: UUID,
        body: AcceptIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> TransactionOut:
        """A supervisor accepts an exception, with a reason. The measured count stays
        as it was: accepting says a person looked, not that the shortfall did not happen."""
        await owned(c, principal, P.RECONCILIATION_RESOLVE, txn_id)
        try:
            txn = await c.accept_transaction(
                txn_id, by=principal.name, reason=body.reason, note=body.note
            )
        except CrateControlError as exc:
            raise HTTPException(409, str(exc)) from exc
        driver = await c.crate_control.get_driver(txn.driver_id)
        await audit(
            c,
            principal.name,
            AuditAction.CRATE_TRANSACTION_ACCEPTED,
            driver.name if driver else str(txn.driver_id),
            validated_status=txn.validated_status.value if txn.validated_status else None,
            counted_crates=txn.counted_crates,
            expected_crates=txn.expected_crates,
            reason=body.reason.value,
            note=body.note,
            transaction_id=str(txn.id),
        )
        return await describe(c, txn)

    @app.get(
        "/api/v1/crate-transactions",
        response_model=list[TransactionOut],
        dependencies=[Depends(require(P.COUNT_READ))],
    )
    async def list_transactions(
        days: int = 7,
        status: TransactionStatus | None = None,
        exceptions_only: bool = False,
        c: Any = Depends(get_container),
    ) -> list[TransactionOut]:
        since = c.clock.now() - timedelta(days=max(1, min(days, MAX_DAYS)))
        found = await c.crate_control.list_transactions(since, limit=2000)
        if status is not None:
            found = [t for t in found if t.status is status]
        if exceptions_only:
            from ivaas.domain.crate_control import EXCEPTIONS

            found = [t for t in found if t.status in EXCEPTIONS]
        return [await describe(c, t) for t in found[:500]]
