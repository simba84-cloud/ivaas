"""Evidence clips: uploaded by the edge node, kept with the load, deleted on schedule.

The node records a few seconds around each count and uploads it here. The clip is
filed under the load (session) that covered that moment at that bay, kept for the
tenant's retention period, and served to the portal over short-lived signed links.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.scope import first_time, require_bay_camera, require_session
from ivaas.domain.evidence import (
    MAX_CLIP_BYTES,
    EvidenceClip,
    EvidenceError,
    EvidenceKind,
)
from ivaas.domain.models import LoadingSession
from ivaas.domain.platform_settings import EVIDENCE_RETENTION_DAYS
from ivaas.domain.rbac import Permission as P
from ivaas.ports.auth import Principal
from ivaas.tenancy import object_key

log = logging.getLogger(__name__)
Audit = Callable[..., Awaitable[None]]
#: a truck is still "this load" for a moment after the session closes
CLOSE_GRACE = timedelta(seconds=60)


class EvidenceOut(BaseModel):
    id: UUID
    session_id: UUID | None
    camera_id: UUID
    kind: EvidenceKind
    started_at: datetime
    ended_at: datetime
    seconds: float
    size_bytes: int
    sha256: str
    expires_at: datetime
    #: signed and short-lived: a <video> tag cannot send a bearer token
    url: str

    @staticmethod
    def of(c: EvidenceClip, url: str) -> EvidenceOut:
        return EvidenceOut(
            id=c.id,
            session_id=c.session_id,
            camera_id=c.camera_id,
            kind=c.kind,
            started_at=c.started_at,
            ended_at=c.ended_at,
            seconds=c.seconds,
            size_bytes=c.size_bytes,
            sha256=c.sha256,
            expires_at=c.expires_at,
            url=url,
        )


class EvidenceReceivedOut(BaseModel):
    id: UUID | None
    session_id: UUID | None
    #: false when this upload repeats one already stored (a retry after a timeout)
    stored: bool


def session_at(sessions: list[LoadingSession], moment: datetime) -> LoadingSession | None:
    """The load that was at the bay at that moment, if any."""
    for s in sessions:
        ended = s.closed_at + CLOSE_GRACE if s.closed_at else None
        if s.opened_at <= moment and (ended is None or moment <= ended):
            return s
    return None


async def sweep_expired(c: Any) -> int:
    """Delete clips past their retention: the video first, then the record of it."""
    removed = 0
    for clip in await c.evidence.expired(c.clock.now()):
        try:
            await c.objects.delete(clip.object_key)
        except Exception:  # already gone is fine; anything else is retried next sweep
            log.exception("could not delete evidence object %s", clip.object_key)
            continue
        await c.evidence.delete(clip.id)
        removed += 1
    return removed


def add_evidence_routes(
    app: FastAPI, get_container: Callable[[Request], Any], audit: Audit
) -> None:
    @app.exception_handler(EvidenceError)
    async def _evidence_error(_: Request, exc: EvidenceError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.post(
        "/api/v1/ingest/evidence",
        response_model=EvidenceReceivedOut,
        status_code=201,
        dependencies=[Depends(require(P.INGEST_WRITE, scoped=True))],
    )
    async def ingest_evidence(
        bay_id: UUID = Form(...),
        camera_id: UUID = Form(...),
        kind: EvidenceKind = Form(...),
        started_at: datetime = Form(...),
        ended_at: datetime = Form(...),
        event_id: UUID | None = Form(default=None),
        file: UploadFile = File(...),
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> EvidenceReceivedOut:
        """A clip from the edge node. The bay must be the node's and the camera the bay's."""
        await require_bay_camera(c, principal, P.INGEST_WRITE, bay_id, camera_id)
        clip_id = uuid4()
        key = object_key(f"evidence/{started_at:%Y-%m-%d}/{clip_id}.mp4")
        digest, size = hashlib.sha256(), 0

        async def chunks() -> AsyncIterator[bytes]:
            nonlocal size
            head = True
            while chunk := await file.read(1024 * 1024):
                if head and chunk[4:8] != b"ftyp":
                    raise EvidenceError("an evidence clip must be an MP4 file")
                head = False
                size += len(chunk)
                if size > MAX_CLIP_BYTES:
                    raise HTTPException(413, "evidence clip is larger than 256 MB")
                digest.update(chunk)
                yield chunk

        # validate the time span before storing anything
        now = c.clock.now()
        days = float(await c.effective(EVIDENCE_RETENTION_DAYS, 90))
        EvidenceClip(
            bay_id=bay_id,
            camera_id=camera_id,
            kind=kind,
            started_at=started_at,
            ended_at=ended_at,
            object_key=key,
            size_bytes=0,
            sha256="",
            created_at=now,
            expires_at=now,
        )
        if not await first_time(c, event_id, "evidence"):
            return EvidenceReceivedOut(id=None, session_id=None, stored=False)
        try:
            await c.objects.put(key, chunks(), "video/mp4")
            if size == 0:
                raise EvidenceError("the evidence clip is empty")
            middle = started_at + (ended_at - started_at) / 2
            covering = session_at(await c.sessions.list_recent(bay_id=bay_id, limit=100), middle)
            clip = EvidenceClip(
                bay_id=bay_id,
                camera_id=camera_id,
                kind=kind,
                started_at=started_at,
                ended_at=ended_at,
                object_key=key,
                size_bytes=size,
                sha256=digest.hexdigest(),
                created_at=now,
                expires_at=now + timedelta(days=days),
                session_id=covering.id if covering else None,
                id=clip_id,
            )
            await c.evidence.save(clip)
        except Exception:
            if event_id is not None:  # not stored: a retry must not be refused
                await c.ingest.release(event_id)
            raise
        return EvidenceReceivedOut(id=clip.id, session_id=clip.session_id, stored=True)

    @app.get(
        "/api/v1/sessions/{session_id}/evidence",
        response_model=list[EvidenceOut],
        dependencies=[Depends(require(P.COUNT_READ, scoped=True))],
    )
    async def session_evidence(
        session_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> list[EvidenceOut]:
        """The load's clips, in order, each with a link that expires."""
        await require_session(c, principal, P.COUNT_READ, session_id)
        clips = await c.evidence.for_session(session_id)
        return [EvidenceOut.of(clip, c.signer.sign(clip.object_key)) for clip in clips]
