"""Use cases for managing cameras of any make."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from ivaas.domain.models import (
    Camera,
    CameraRole,
    CameraStatus,
    DuplicateStreamPathError,
    NotFoundError,
    StreamSource,
)
from ivaas.ports.repositories import BayReader, CameraReader, CameraWriter, Clock, EventPublisher
from ivaas.ports.streaming import SourceProbe, StreamGateway

SUBJECT_CAMERA_REGISTERED = "ivaas.camera.registered"
SUBJECT_CAMERA_REMOVED = "ivaas.camera.removed"
SUBJECT_CAMERA_OFFLINE = "ivaas.camera.offline"
SUBJECT_CAMERA_ONLINE = "ivaas.camera.online"


class _CameraStore(CameraReader, CameraWriter, Protocol):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "camera"


@dataclass
class RegisterCamera:
    bays: BayReader
    cameras: _CameraStore
    gateway: StreamGateway
    events: EventPublisher
    #: roles whose video is evidence: recorded at the edge so clips can be cut from it
    evidence_roles: frozenset[CameraRole] = frozenset()

    async def __call__(
        self, bay_id: UUID, name: str, role: CameraRole, source_url: str | None
    ) -> Camera:
        bay = await self.bays.get(bay_id)
        if bay is None:
            raise NotFoundError(f"bay {bay_id} not found")
        source = StreamSource(source_url)  # validates before anything is written
        stream_path = f"{slug(bay.name)}/{slug(name)}"
        if await self.cameras.get_by_stream_path(stream_path) is not None:
            raise DuplicateStreamPathError(stream_path)

        camera = Camera(uuid4(), bay_id, name, role, stream_path, source=source)
        # Gateway first: if the media server rejects it we have stored nothing.
        await self.gateway.provision(stream_path, source, record=role in self.evidence_roles)
        await self.cameras.save(camera)
        await self.events.publish(
            SUBJECT_CAMERA_REGISTERED,
            {"id": str(camera.id), "stream_path": stream_path, "protocol": source.protocol},
        )
        return camera


@dataclass
class RemoveCamera:
    cameras: _CameraStore
    gateway: StreamGateway
    events: EventPublisher

    async def __call__(self, camera_id: UUID) -> None:
        camera = await self.cameras.get(camera_id)
        if camera is None:
            raise NotFoundError(f"camera {camera_id} not found")
        await self.gateway.remove(camera.stream_path)
        await self.cameras.delete(camera_id)
        await self.events.publish(SUBJECT_CAMERA_REMOVED, {"id": str(camera_id)})


@dataclass
class EnsureStreamPaths:
    """Make the media gateway hold every camera's path, recorded if it is evidence.

    Paths added through the gateway's API live in its memory: a restart of the media
    server forgets them all, and every camera would stay dark until someone
    registered it again. This puts back whatever is missing, and corrects the
    recording flag, from the cameras IVaaS knows. Runs at startup and on a timer.
    """

    bays: BayReader
    cameras: _CameraStore
    gateway: StreamGateway
    evidence_roles: frozenset[CameraRole] = frozenset()

    async def __call__(self) -> int:
        configured = await self.gateway.configured_paths()
        restored = 0
        for bay in await self.bays.list_all():
            for camera in await self.cameras.list_for_bay(bay.id):
                record = camera.role in self.evidence_roles
                if configured.get(camera.stream_path) != record:
                    await self.gateway.provision(camera.stream_path, camera.source, record=record)
                    restored += 1
        return restored


@dataclass
class RefreshCameraStatus:
    """Mark cameras online/offline from what the media gateway is actually receiving.

    Works for every source type: a pulled RTSP camera that has gone dark and a
    push camera that has started sending both show up correctly, with no need
    for the camera to know about IVaaS at all. Runs on a timer.

    The gateway pulls a camera only while someone watches it, and the portal plays
    only cameras that are online, so a pulled camera that is not live is asked
    directly before it is called offline. Without that, a working camera nobody
    was watching stayed offline and could never be opened.
    """

    bays: BayReader
    cameras: _CameraStore
    gateway: StreamGateway
    clock: Clock
    probe: SourceProbe | None = None
    #: told when a camera that was streaming stops, and when it comes back (alerts)
    events: EventPublisher | None = None

    async def __call__(self) -> dict[str, int]:
        live = await self.gateway.live_paths()
        now = self.clock.now()
        cameras = [
            c for bay in await self.bays.list_all() for c in await self.cameras.list_for_bay(bay.id)
        ]
        idle = [c for c in cameras if c.stream_path not in live and not c.source.is_push]
        answered = await asyncio.gather(*(self._answers(c) for c in idle))
        answering = {c.id for c, ok in zip(idle, answered, strict=True) if ok}
        changed = {"online": 0, "offline": 0}
        for camera in cameras:
            if camera.stream_path in live or camera.id in answering:
                was_offline = camera.status is CameraStatus.OFFLINE
                camera.mark_seen(now)
                await self.cameras.save(camera)
                changed["online"] += 1
                if was_offline:
                    await self._tell(SUBJECT_CAMERA_ONLINE, camera)
            elif camera.status is not CameraStatus.OFFLINE:
                camera.status = CameraStatus.OFFLINE
                await self.cameras.save(camera)
                changed["offline"] += 1
                if camera.last_seen_at is not None:  # one never seen has not "stopped"
                    await self._tell(SUBJECT_CAMERA_OFFLINE, camera)
        return changed

    async def _tell(self, subject: str, camera: Camera) -> None:
        if self.events is None:
            return
        await self.events.publish(
            subject,
            {
                "id": str(camera.id),
                "bay_id": str(camera.bay_id),
                "name": camera.name,
                "role": camera.role.value,
                "status": camera.status.value,
                "last_seen_at": camera.last_seen_at.isoformat() if camera.last_seen_at else None,
            },
        )

    async def _answers(self, camera: Camera) -> bool:
        return self.probe is not None and await self.probe.answers(camera.source)
