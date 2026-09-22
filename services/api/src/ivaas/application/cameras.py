"""Use cases for managing cameras of any make."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from ivaas.domain.models import (
    Camera,
    CameraRole,
    DuplicateStreamPathError,
    NotFoundError,
    StreamSource,
)
from ivaas.ports.repositories import BayReader, CameraReader, CameraWriter, EventPublisher
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
