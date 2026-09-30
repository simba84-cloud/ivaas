"""HTTP routes for site security: zones, incidents, the badge feed, enrolled people.

Who may do what:
    viewer    see zones, incidents and what the security features can do
    operator  acknowledge and resolve incidents; read the badge log (it names people)
    admin     draw zones, enrol and remove people
    service   the edge pipeline reports incidents and fetches zones; the access-control
              system posts badge swipes
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.schemas import (
    BadgeIn,
    BadgeOut,
    EdgeCapabilities,
    GalleryEntry,
    IncidentIn,
    IncidentOut,
    PersonOut,
    PipelineSecurityOut,
    PipelineZone,
    ResolveIn,
    SecurityStatusOut,
    ZoneIn,
    ZoneOut,
)
from ivaas.adapters.http.scope import require_bay, require_camera
from ivaas.domain.audit import AuditAction
from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import Permission as P
from ivaas.domain.security import (
    BadgeEvent,
    Incident,
    IncidentKind,
    IncidentStatus,
    SecurityError,
    Window,
    Zone,
)
from ivaas.ports.auth import Principal

if TYPE_CHECKING:
    from ivaas.config.container import Container

Audit = Callable[..., Awaitable[None]]
MAX_PHOTO_BYTES = 8 * 1024 * 1024


def _zone_from(body: ZoneIn, camera_id: UUID, zone_id: UUID | None = None) -> Zone:
    kw: dict[str, Any] = {} if zone_id is None else {"id": zone_id}
    return Zone(
        camera_id=camera_id,
        name=body.name.strip(),
        polygon=tuple((float(x), float(y)) for x, y in body.polygon),
        rules=frozenset(body.rules),
        schedule=tuple(Window(tuple(w.days), w.start, w.end) for w in body.schedule),
        min_dwell_s=body.min_dwell_s,
        exclude=body.exclude,
        badge_door=(body.badge_door or "").strip() or None,
        **kw,
    )


def add_security_routes(
    app: FastAPI, get_container: Callable[[Request], Container], audit: Audit
) -> None:
    @app.exception_handler(SecurityError)
    async def _security_error(_: Request, exc: SecurityError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    async def site_zone(c: Container, bay_id: UUID) -> ZoneInfo:
        bay = await c.bays.get(bay_id)
        site = await c.sites.get(bay.site_id) if bay else None
        try:
            return ZoneInfo(site.timezone if site else "UTC")
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    async def local_now(c: Container, camera_id: UUID) -> datetime:
        camera = await c.cameras.get(camera_id)
        tz = await site_zone(c, camera.bay_id) if camera else ZoneInfo("UTC")
        return c.clock.now().astimezone(tz)

    def signed(c: Container, i: Incident) -> IncidentOut:
        return IncidentOut.of(i, c.signer.sign(i.snapshot_key) if i.snapshot_key else None)

    # --- zones ---------------------------------------------------------------------
    @app.get(
        "/api/v1/bays/{bay_id}/zones",
        response_model=list[ZoneOut],
        dependencies=[Depends(require(P.TOPOLOGY_READ, scoped=True))],
    )
    async def bay_zones(
        bay_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> list[ZoneOut]:
        await require_bay(c, principal, P.TOPOLOGY_READ, bay_id)
        cameras = await c.cameras.list_for_bay(bay_id)
        now = c.clock.now().astimezone(await site_zone(c, bay_id))
        zones = await c.zones.for_cameras([cam.id for cam in cameras])
        return [ZoneOut.of(z, z.armed(now)) for z in zones]

    @app.post(
        "/api/v1/cameras/{camera_id}/zones",
        response_model=ZoneOut,
        status_code=201,
        dependencies=[Depends(require(P.DEVICE_CALIBRATE))],
    )
    async def create_zone(
        camera_id: UUID,
        body: ZoneIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> ZoneOut:
        zone = await c.save_zone(_zone_from(body, camera_id))
        await audit(
            c, principal.name, AuditAction.ZONE_SAVED, zone.name, rules=",".join(sorted(zone.rules))
        )
        return ZoneOut.of(zone, zone.armed(await local_now(c, camera_id)))

    @app.put(
        "/api/v1/zones/{zone_id}",
        response_model=ZoneOut,
        dependencies=[Depends(require(P.DEVICE_CALIBRATE))],
    )
    async def update_zone(
        zone_id: UUID,
        body: ZoneIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> ZoneOut:
        existing = await c.zones.get(zone_id)
        if existing is None:
            raise NotFoundError(f"zone {zone_id} not found")
        zone = await c.save_zone(_zone_from(body, existing.camera_id, zone_id))
        await audit(
            c, principal.name, AuditAction.ZONE_SAVED, zone.name, rules=",".join(sorted(zone.rules))
        )
        return ZoneOut.of(zone, zone.armed(await local_now(c, zone.camera_id)))

    @app.delete(
        "/api/v1/zones/{zone_id}",
        status_code=204,
        dependencies=[Depends(require(P.DEVICE_CALIBRATE))],
    )
    async def delete_zone(
        zone_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> Response:
        zone = await c.zones.get(zone_id)
        if zone is None:
            raise NotFoundError(f"zone {zone_id} not found")
        await c.zones.delete(zone_id)
        await audit(c, principal.name, AuditAction.ZONE_DELETED, zone.name)
        return Response(status_code=204)

    @app.get(
        "/api/v1/cameras/{camera_id}/snapshot",
        dependencies=[Depends(require(P.VIDEO_LIVE_VIEW, scoped=True))],
    )
    async def camera_snapshot(
        camera_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> Response:
        """One still frame from the camera, to draw zones on. A camera that is not
        streaming has no frame, and says so rather than returning a stale one."""
        camera = await require_camera(c, principal, P.VIDEO_LIVE_VIEW, camera_id)
        url = f"{c.settings.media_rtsp_url.rstrip('/')}/{camera.stream_path}"

        def grab() -> bytes | None:
            import cv2

            cap = cv2.VideoCapture(
                url,
                cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000],
            )
            try:
                ok, image = cap.read() if cap.isOpened() else (False, None)
                if not ok:
                    return None
                ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                return jpeg.tobytes() if ok else None
            finally:
                cap.release()

        try:
            frame = await asyncio.wait_for(asyncio.to_thread(grab), timeout=10)
        except TimeoutError:
            frame = None
        if frame is None:
            raise HTTPException(404, "the camera is not streaming, so there is no frame to show")
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # --- incidents -----------------------------------------------------------------
    @app.get(
        "/api/v1/incidents",
        response_model=list[IncidentOut],
        dependencies=[Depends(require(P.COUNT_READ))],
    )
    async def list_incidents(
        bay_id: UUID | None = None,
        status: IncidentStatus | None = None,
        kind: IncidentKind | None = None,
        days: int = 7,
        c: Container = Depends(get_container),
    ) -> list[IncidentOut]:
        since = c.clock.now() - timedelta(days=max(1, min(days, 365)))
        rows = await c.incidents.list(bay_id=bay_id, since=since, status=status, kind=kind)
        return [signed(c, i) for i in rows]

    @app.post(
        "/api/v1/incidents/{incident_id}/acknowledge",
        response_model=IncidentOut,
        dependencies=[Depends(require(P.SESSION_OPERATE))],
    )
    async def acknowledge_incident(
        incident_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> IncidentOut:
        i = await c.update_incident.acknowledge(incident_id, principal.name)
        await audit(
            c,
            principal.name,
            AuditAction.INCIDENT_ACKNOWLEDGED,
            f"{i.kind.value} · {i.zone_name or 'no zone'}",
            incident_id=str(i.id),
        )
        return signed(c, i)

    @app.post(
        "/api/v1/incidents/{incident_id}/resolve",
        response_model=IncidentOut,
        dependencies=[Depends(require(P.SESSION_OPERATE))],
    )
    async def resolve_incident(
        incident_id: UUID,
        body: ResolveIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> IncidentOut:
        i = await c.update_incident.resolve(incident_id, principal.name, body.note)
        await audit(
            c,
            principal.name,
            AuditAction.INCIDENT_RESOLVED,
            f"{i.kind.value} · {i.zone_name or 'no zone'}",
            note=i.resolution_note,
            incident_id=str(i.id),
        )
        return signed(c, i)

    @app.post(
        "/api/v1/ingest/incidents",
        response_model=IncidentOut | None,
        dependencies=[Depends(require(P.INGEST_WRITE))],
    )
    async def ingest_incident(
        body: IncidentIn, c: Container = Depends(get_container)
    ) -> IncidentOut | None:
        snapshot = None
        if body.snapshot_jpeg_b64:
            try:
                snapshot = base64.b64decode(body.snapshot_jpeg_b64, validate=True)
            except binascii.Error as exc:
                raise HTTPException(422, "snapshot is not valid base64") from exc
            if snapshot[:2] != b"\xff\xd8":
                raise HTTPException(422, "snapshot must be a JPEG")
        report = await c.report_incident_uc()
        incident = await report(
            bay_id=body.bay_id,
            camera_id=body.camera_id,
            zone_id=body.zone_id,
            kind=body.kind,
            confidence=body.confidence,
            detected_at=body.detected_at,
            snapshot=snapshot,
            detail=body.detail,
        )
        return signed(c, incident) if incident else None

    # --- badges ----------------------------------------------------------------------
    @app.post(
        "/api/v1/ingest/badges",
        response_model=BadgeOut,
        dependencies=[Depends(require(P.INGEST_WRITE))],
    )
    async def ingest_badge(body: BadgeIn, c: Container = Depends(get_container)) -> BadgeOut:
        event = await c.record_badge(
            BadgeEvent(body.badge_id, body.door.strip(), body.at, body.granted, body.holder)
        )
        return BadgeOut.of(event)

    @app.get(
        "/api/v1/badges",
        response_model=list[BadgeOut],
        dependencies=[Depends(require(P.SESSION_OPERATE))],
    )
    async def badge_log(hours: int = 24, c: Container = Depends(get_container)) -> list[BadgeOut]:
        now = c.clock.now()
        events = await c.badges.between(now - timedelta(hours=max(1, min(hours, 24 * 31))), now)
        return [BadgeOut.of(e) for e in events[:500]]

    # --- enrolled people (face recognition) -------------------------------------------
    @app.get(
        "/api/v1/people",
        response_model=list[PersonOut],
        dependencies=[Depends(require(P.SECURITY_MANAGE))],
    )
    async def list_people(c: Container = Depends(get_container)) -> list[PersonOut]:
        return [PersonOut.of(p) for p in await c.people.list()]

    @app.post(
        "/api/v1/people",
        response_model=PersonOut,
        status_code=201,
        dependencies=[Depends(require(P.SECURITY_MANAGE))],
    )
    async def enrol_person(
        name: str = Form(..., min_length=1, max_length=160),
        employee_ref: str = Form(..., min_length=1, max_length=80),
        consent_reference: str = Form(..., min_length=1, max_length=300),
        photo: UploadFile = File(...),
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> PersonOut:
        data = await photo.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "photo is larger than 8 MB")
        enrol = await c.enrol_person_uc()
        person = await enrol(
            name=name,
            employee_ref=employee_ref,
            consent_reference=consent_reference,
            photo=data,
            by=principal.name,
        )
        await audit(
            c,
            principal.name,
            AuditAction.PERSON_ENROLLED,
            person.name,
            employee_ref=person.employee_ref,
            consent_reference=person.consent_reference,
        )
        return PersonOut.of(person)

    @app.delete(
        "/api/v1/people/{person_id}",
        status_code=204,
        dependencies=[Depends(require(P.SECURITY_MANAGE))],
    )
    async def remove_person(
        person_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> Response:
        gone = await c.people.delete(person_id)
        if gone is None:
            raise NotFoundError(f"person {person_id} not found")
        await audit(
            c, principal.name, AuditAction.PERSON_REMOVED, gone.name, employee_ref=gone.employee_ref
        )
        return Response(status_code=204)

    # --- status, and the edge pipeline's view ------------------------------------------
    @app.get(
        "/api/v1/security/status",
        response_model=SecurityStatusOut,
        dependencies=[Depends(require(P.COUNT_READ))],
    )
    async def security_status(c: Container = Depends(get_container)) -> SecurityStatusOut:
        now = c.clock.now()
        recent = await c.badges.between(now - timedelta(hours=24), now)
        edge = c.edge_security
        return SecurityStatusOut(
            face_recognition=await c.face_recognition_on(),
            face_models_installed=c.face_encoder is not None,
            enrolled_people=len(await c.people.list()),
            badge_events_24h=len(recent),
            last_badge_at=recent[0].at if recent else None,
            edge=EdgeCapabilities(**edge) if edge else None,
        )

    @app.get(
        "/api/v1/pipeline/security",
        response_model=PipelineSecurityOut,
        dependencies=[Depends(require(P.INGEST_WRITE))],
    )
    async def pipeline_security(
        bay_id: UUID, capabilities: str = "", c: Container = Depends(get_container)
    ) -> PipelineSecurityOut:
        """Zones for the bay's cameras with whether each is armed now, and the face
        gallery only while face recognition is switched on. The edge node also says
        what it can detect, which the portal shows rather than assumes."""
        c.edge_security = {
            "reported_at": c.clock.now(),
            "detectors": sorted({d for d in capabilities.split(",") if d}),
        }
        cameras = await c.cameras.list_for_bay(bay_id)
        now = c.clock.now().astimezone(await site_zone(c, bay_id))
        zones = await c.zones.for_cameras([cam.id for cam in cameras])
        gallery: list[GalleryEntry] = []
        if await c.face_recognition_on():
            gallery = [
                GalleryEntry(name=p.name, embedding=list(p.embedding))
                for p in await c.people.list()
            ]
        return PipelineSecurityOut(
            zones=[
                PipelineZone(
                    id=z.id,
                    camera_id=z.camera_id,
                    name=z.name,
                    polygon=list(z.polygon),
                    rules=sorted(z.rules),
                    armed=z.armed(now),
                    min_dwell_s=z.min_dwell_s,
                    exclude=z.exclude,
                )
                for z in zones
            ],
            gallery=gallery,
        )
