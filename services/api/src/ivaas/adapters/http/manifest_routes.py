"""Manifests, exceptions and balances over HTTP (proposal M5, T5.3-T5.4)."""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.uploads import upload_text
from ivaas.domain.audit import AuditAction
from ivaas.domain.manifests import (
    Balance,
    ExceptionKind,
    ExceptionStatus,
    LineStatus,
    ManifestError,
    ManifestException,
    ManifestLine,
)
from ivaas.domain.models import NotFoundError, SessionDirection
from ivaas.domain.plates import canonical
from ivaas.domain.rbac import Permission as P
from ivaas.ports.auth import Principal

Audit = Callable[..., Awaitable[None]]
MAX_IMPORT_BYTES = 5 * 1024 * 1024
DIRECTIONS = {
    "LOAD": SessionDirection.LOADING,
    "LOADING": SessionDirection.LOADING,
    "DISPATCH": SessionDirection.LOADING,
    "RETURN": SessionDirection.OFFLOADING,
    "OFFLOADING": SessionDirection.OFFLOADING,
}


class ManifestImportOut(BaseModel):
    added: int
    updated: int
    errors: list[str]
    exceptions_raised: int


class ManifestLineOut(BaseModel):
    id: UUID
    reference: str
    day: date
    plate: str
    route: str
    direction: SessionDirection
    expected: int
    status: LineStatus
    session_id: UUID | None


class ExceptionOut(BaseModel):
    id: UUID
    kind: ExceptionKind
    day: date
    plate: str | None
    route: str
    expected: int | None
    counted: int | None
    #: counted - expected: negative means crates the manifest says went that were not seen
    difference: int | None
    session_id: UUID | None
    status: ExceptionStatus
    raised_at: datetime
    resolved_by: str | None
    resolved_at: datetime | None
    resolution_note: str | None

    @staticmethod
    def of(e: ManifestException) -> ExceptionOut:
        return ExceptionOut(
            id=e.id,
            kind=e.kind,
            day=e.day,
            plate=e.plate,
            route=e.route,
            expected=e.expected,
            counted=e.counted,
            difference=e.difference,
            session_id=e.session_id,
            status=e.status,
            raised_at=e.raised_at,
            resolved_by=e.resolved_by,
            resolved_at=e.resolved_at,
            resolution_note=e.resolution_note,
        )


class ResolveIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


class BalanceOut(BaseModel):
    key: str
    dispatched: int
    returned: int
    outstanding: int
    loads_out: int
    loads_back: int
    in_progress: int
    corrected: int

    @staticmethod
    def of(b: Balance) -> BalanceOut:
        return BalanceOut(
            key=b.key,
            dispatched=b.dispatched,
            returned=b.returned,
            outstanding=b.outstanding,
            loads_out=b.loads_out,
            loads_back=b.loads_back,
            in_progress=b.in_progress,
            corrected=b.corrected,
        )


def add_manifest_routes(
    app: FastAPI, get_container: Callable[[Request], Any], audit: Audit
) -> None:
    @app.post(
        "/api/v1/manifests/import",
        response_model=ManifestImportOut,
        dependencies=[Depends(require(P.GROUNDTRUTH_ENTER))],
    )
    async def import_manifests(
        file: UploadFile = File(...),
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> ManifestImportOut:
        """A CSV or Excel workbook (.xlsx): date, plate (or fleet_number), direction
        (LOAD/RETURN), expected, reference, and optionally route and site. A reference
        already imported is updated. Matching and exceptions run straight after."""
        text = await upload_text(
            file, MAX_IMPORT_BYTES, needs={"date", "direction", "expected", "reference"}
        )
        rows = csv.DictReader(io.StringIO(text))
        headers = {(h or "").strip().lower() for h in rows.fieldnames or []}
        missing = {"date", "direction", "expected", "reference"} - headers
        if missing or not headers & {"plate", "fleet_number"}:
            raise HTTPException(
                422,
                "the file needs date, direction, expected, reference, and plate or fleet_number",
            )
        sites = await c.sites.list_all()
        by_name = {s.name.strip().lower(): s for s in sites}
        fleet_no = {
            v.fleet_number.strip().upper(): v for v in await c.fleet.list_all() if v.fleet_number
        }
        added = updated = 0
        errors: list[str] = []
        for line_no, row in enumerate(rows, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            try:
                if row.get("site"):
                    site = by_name.get(row["site"].lower())
                    if site is None:
                        raise ManifestError(f"no site named {row['site']!r}")
                elif len(sites) == 1:
                    site = sites[0]
                else:
                    raise ManifestError("say which site: this tenant has more than one")
                plate = row.get("plate", "")
                if not plate and row.get("fleet_number"):
                    vehicle = fleet_no.get(row["fleet_number"].upper())
                    if vehicle is None:
                        raise ManifestError(f"fleet number {row['fleet_number']} is not registered")
                    plate = vehicle.plate
                direction = DIRECTIONS.get(row.get("direction", "").upper())
                if direction is None:
                    raise ManifestError("direction must be LOAD or RETURN")
                try:
                    day = date.fromisoformat(row.get("date", ""))
                    expected = int(row.get("expected", ""))
                except ValueError as exc:
                    raise ManifestError(
                        "date must be YYYY-MM-DD and expected a whole number"
                    ) from exc
                line = ManifestLine(
                    reference=row.get("reference", "")[:80],
                    day=day,
                    plate=plate[:16],
                    direction=direction,
                    expected=expected,
                    site_id=site.id,
                    route=row.get("route", "")[:80],
                    imported_by=principal.name,
                    imported_at=c.clock.now(),
                )
            except ManifestError as exc:
                errors.append(f"line {line_no}: {exc}")
                continue
            known = await c.manifests.find(line.reference, line.direction)
            if known is not None:
                if canonical(known.plate) == canonical(line.plate) and known.day == line.day:
                    line.session_id, line.status = known.session_id, known.status
                line.id = known.id
                updated += 1
            else:
                added += 1
            await c.manifests.save(line)
        raised = await (await c.reconcile_manifests_uc())()
        await audit(
            c,
            principal.name,
            AuditAction.MANIFEST_IMPORTED,
            file.filename or "manifest.csv",
            added=added,
            updated=updated,
            errors=len(errors),
        )
        return ManifestImportOut(
            added=added, updated=updated, errors=errors[:100], exceptions_raised=len(raised)
        )

    @app.get(
        "/api/v1/manifests",
        response_model=list[ManifestLineOut],
        dependencies=[Depends(require(P.COUNT_READ))],
    )
    async def list_manifests(
        days: int = 7, c: Any = Depends(get_container)
    ) -> list[ManifestLineOut]:
        since = (c.clock.now() - timedelta(days=max(1, min(days, 90)))).date()
        lines = sorted(
            await c.manifests.since(since), key=lambda x: (x.day, x.reference), reverse=True
        )
        return [
            ManifestLineOut(
                id=x.id,
                reference=x.reference,
                day=x.day,
                plate=x.plate,
                route=x.route,
                direction=x.direction,
                expected=x.expected,
                status=x.status,
                session_id=x.session_id,
            )
            for x in lines
        ]

    @app.get(
        "/api/v1/exceptions",
        response_model=list[ExceptionOut],
        dependencies=[Depends(require(P.COUNT_READ))],
    )
    async def list_exceptions(
        status: ExceptionStatus | None = ExceptionStatus.OPEN,
        days: int = 30,
        c: Any = Depends(get_container),
    ) -> list[ExceptionOut]:
        since = (c.clock.now() - timedelta(days=max(1, min(days, 365)))).date()
        return [ExceptionOut.of(e) for e in await c.exceptions.list(status=status, since=since)]

    @app.post(
        "/api/v1/exceptions/{exception_id}/resolve",
        response_model=ExceptionOut,
        dependencies=[Depends(require(P.RECONCILIATION_RESOLVE))],
    )
    async def resolve_exception(
        exception_id: UUID,
        body: ResolveIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> ExceptionOut:
        """Close an exception with what was found. The counts are not changed here: a
        correction to the count is its own, separately audited action."""
        e = await c.exceptions.get(exception_id)
        if e is None:
            raise NotFoundError(f"exception {exception_id} not found")
        try:
            e.resolve(principal.name, c.clock.now(), body.note)
        except ManifestError as exc:
            raise HTTPException(409, str(exc)) from exc
        await c.exceptions.save(e)
        await audit(
            c,
            principal.name,
            AuditAction.EXCEPTION_RESOLVED,
            e.plate or e.kind.value,
            kind=e.kind.value,
            expected=e.expected,
            counted=e.counted,
            note=body.note,
        )
        return ExceptionOut.of(e)

    @app.get(
        "/api/v1/balances",
        response_model=list[BalanceOut],
        dependencies=[Depends(require(P.COUNT_READ))],
    )
    async def get_balances(
        days: int = 7,
        by: Literal["truck", "route", "day"] = "truck",
        c: Any = Depends(get_container),
    ) -> list[BalanceOut]:
        """Crates out, crates back, still outstanding, over the last `days` days, per
        truck, route or day, from each load's count of record. Open loads are counted
        as in progress, not added in."""
        rows = await c.balance_query()(days, by)
        return [BalanceOut.of(b) for b in rows]
