"""Use cases for managing cameras of any make."""

from __future__ import annotations

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
from ivaas.ports.streaming import StreamGateway

SUBJECT_CAMERA_REGISTERED = "ivaas.camera.registered"
SUBJECT_CAMERA_REMOVED = "ivaas.camera.removed"


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
        await self.gateway.provision(stream_path, source)
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
class RefreshCameraStatus:
    """Mark cameras online/offline from what the media gateway is actually receiving.

    Works for every source type: a pulled RTSP camera that has gone dark and a
    push camera that has started sending both show up correctly, with no need
    for the camera to know about IVaaS at all. Runs on a timer.
    """

    bays: BayReader
    cameras: _CameraStore
    gateway: StreamGateway
    clock: Clock

    async def __call__(self) -> dict[str, int]:
        live = await self.gateway.live_paths()
        now = self.clock.now()
        changed = {"online": 0, "offline": 0}
        for bay in await self.bays.list_all():
            for camera in await self.cameras.list_for_bay(bay.id):
                is_live = camera.stream_path in live
                if is_live:
                    camera.mark_seen(now)
                    await self.cameras.save(camera)
                elif camera.status is not CameraStatus.OFFLINE:
                    camera.status = CameraStatus.OFFLINE
                    await self.cameras.save(camera)
                    changed["offline"] += 1
                if is_live and camera.last_seen_at == now:
                    changed["online"] += 1
        return changed
