"""The fleet register, which truck a load was, and people's corrections (M5).

Every change here touches a figure someone may dispute later, so each is audited
with what it was before and what it became.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.schemas import (
    AssignVehicleIn,
    FleetImportOut,
    OverrideIn,
    SessionOut,
    VehicleIn,
    VehicleOut,
)
from ivaas.adapters.http.scope import require_session
from ivaas.domain.audit import AuditAction
from ivaas.domain.fleet import FleetError, Vehicle
from ivaas.domain.models import NotFoundError
from ivaas.domain.plates import canonical
from ivaas.domain.rbac import Permission as P
from ivaas.ports.auth import Principal

Audit = Callable[..., Awaitable[None]]
#: a register of a few hundred trucks is a few KB; anything much bigger is not one
MAX_IMPORT_BYTES = 2 * 1024 * 1024


def add_fleet_routes(app: FastAPI, get_container: Callable[[Request], Any], audit: Audit) -> None:
    async def duplicate_of(c: Any, plate: str, exclude: UUID | None = None) -> Vehicle | None:
        key = canonical(plate)
        return next((v for v in await c.fleet.list_all() if v.key == key and v.id != exclude), None)

    # --- the register ---------------------------------------------------------------
    @app.get(
        "/api/v1/fleet",
        response_model=list[VehicleOut],
        dependencies=[Depends(require(P.TOPOLOGY_READ, scoped=True))],
    )
    async def list_fleet(c: Any = Depends(get_container)) -> list[VehicleOut]:
        return [VehicleOut.of(v) for v in await c.fleet.list_all()]

    @app.post(
        "/api/v1/fleet",
        response_model=VehicleOut,
        status_code=201,
        dependencies=[Depends(require(P.SITE_MANAGE))],
    )
    async def add_vehicle(
        body: VehicleIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> VehicleOut:
        try:
            vehicle = Vehicle(**body.model_dump(), created_at=c.clock.now())
        except FleetError as exc:
            raise HTTPException(422, str(exc)) from exc
        if (same := await duplicate_of(c, vehicle.plate)) is not None:
            raise HTTPException(409, f"{vehicle.plate} is already registered as {same.plate}")
        await c.fleet.save(vehicle)
        await audit(c, principal.name, AuditAction.VEHICLE_SAVED, vehicle.plate)
        return VehicleOut.of(vehicle)

    @app.put(
        "/api/v1/fleet/{vehicle_id}",
        response_model=VehicleOut,
        dependencies=[Depends(require(P.SITE_MANAGE))],
    )
    async def update_vehicle(
        vehicle_id: UUID,
        body: VehicleIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> VehicleOut:
        vehicle = await c.fleet.get(vehicle_id)
        if vehicle is None:
            raise NotFoundError(f"vehicle {vehicle_id} not found")
        before = VehicleOut.of(vehicle).model_dump(mode="json", exclude={"id", "created_at"})
        try:
            updated = Vehicle(**body.model_dump(), created_at=vehicle.created_at, id=vehicle.id)
        except FleetError as exc:
            raise HTTPException(422, str(exc)) from exc
        if (same := await duplicate_of(c, updated.plate, exclude=vehicle.id)) is not None:
            raise HTTPException(409, f"{updated.plate} is already registered as {same.plate}")
        await c.fleet.save(updated)
        await audit(
            c,
            principal.name,
            AuditAction.VEHICLE_SAVED,
            updated.plate,
            before=before,
            after=body.model_dump(mode="json"),
        )
        return VehicleOut.of(updated)

    @app.post(
        "/api/v1/fleet/import",
        response_model=FleetImportOut,
        dependencies=[Depends(require(P.SITE_MANAGE))],
    )
    async def import_fleet(
        file: UploadFile = File(...),
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> FleetImportOut:
        """A CSV with a `plate` column, and optionally fleet_number, operator, notes.

        A plate already registered (under any spelling) is updated, not duplicated.
        Rows that cannot be used are reported by line; the rest are saved.
        """
        raw = await file.read(MAX_IMPORT_BYTES + 1)
        if len(raw) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "a fleet register CSV is at most 2 MB")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "the file is not UTF-8 text") from exc
        rows = csv.DictReader(io.StringIO(text))
        headers = {(h or "").strip().lower() for h in rows.fieldnames or []}
        if "plate" not in headers:
            raise HTTPException(422, "the CSV needs a 'plate' column")
        existing = {v.key: v for v in await c.fleet.list_all()}
        added = updated = 0
        errors: list[str] = []
        for line, row in enumerate(rows, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            try:
                vehicle = Vehicle(
                    plate=row.get("plate", ""),
                    fleet_number=row.get("fleet_number", "")[:40],
                    operator=row.get("operator", "")[:120],
                    notes=row.get("notes", "")[:1000],
                    created_at=c.clock.now(),
                )
            except FleetError as exc:
                errors.append(f"line {line}: {exc}")
                continue
            if len(vehicle.plate) > 16:
                errors.append(f"line {line}: plate is longer than 16 characters")
                continue
            if (known := existing.get(vehicle.key)) is not None:
                vehicle.id, vehicle.created_at = known.id, known.created_at
                updated += 1
            else:
                added += 1
            existing[vehicle.key] = vehicle
            await c.fleet.save(vehicle)
        await audit(
            c,
            principal.name,
            AuditAction.FLEET_IMPORTED,
            file.filename or "fleet.csv",
            added=added,
            updated=updated,
            errors=len(errors),
        )
        return FleetImportOut(added=added, updated=updated, errors=errors[:100])

    # --- which truck a load was --------------------------------------------------------
    @app.post(
        "/api/v1/sessions/{session_id}/vehicle",
        response_model=SessionOut,
        dependencies=[Depends(require(P.SESSION_OPERATE, scoped=True))],
    )
    async def assign_vehicle(
        session_id: UUID,
        body: AssignVehicleIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> SessionOut:
        """An operator says which truck this was: for a load the camera could not read,
        or read wrongly. Audited, with what it said before."""
        await require_session(c, principal, P.SESSION_OPERATE, session_id)
        session = await c.sessions.get(session_id)
        if body.vehicle_id is not None:
            vehicle = await c.fleet.get(body.vehicle_id)
            if vehicle is None:
                raise NotFoundError(f"vehicle {body.vehicle_id} not found")
            plate, vehicle_id = vehicle.plate, vehicle.id
        elif body.plate:
            plate, vehicle_id = " ".join(body.plate.upper().split()), None
        else:
            raise HTTPException(422, "give a registered vehicle_id or a plate")
        before = {"plate": session.plate, "vehicle_id": str(session.vehicle_id or "") or None}
        session.identify(plate=plate, vehicle_id=vehicle_id, by="operator")
        await c.sessions.save(session)
        await audit(
            c,
            principal.name,
            AuditAction.SESSION_IDENTIFIED,
            plate,
            before=before,
            after={"plate": plate, "vehicle_id": str(vehicle_id) if vehicle_id else None},
            note=body.note,
            session_id=str(session.id),
        )
        return SessionOut.of(session, has_register=bool(await c.fleet.list_all()))

    # --- a person's correction -----------------------------------------------------------
    @app.post(
        "/api/v1/sessions/{session_id}/override",
        response_model=SessionOut,
        dependencies=[Depends(require(P.COUNT_OVERRIDE, scoped=True))],
    )
    async def override_count(
        session_id: UUID,
        body: OverrideIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> SessionOut:
        """Correct the count of record, with a reason. The AI count stays as it was:
        accuracy is measured on it, and a correction must not flatter that figure."""
        await require_session(c, principal, P.COUNT_OVERRIDE, session_id)
        session = await c.sessions.get(session_id)
        before = session.override_count
        try:
            session.override(
                body.count,
                reason=body.reason,
                by=principal.name,
                at=c.clock.now(),
                note=body.note,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await c.sessions.save(session)
        await audit(
            c,
            principal.name,
            AuditAction.COUNT_OVERRIDDEN,
            session.plate or "no plate",
            ai_count=session.ai_count,
            before=before,
            after=body.count,
            reason=body.reason.value,
            note=body.note,
            session_id=str(session.id),
        )
        return SessionOut.of(session, has_register=bool(await c.fleet.list_all()))
