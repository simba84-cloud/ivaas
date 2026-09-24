"""Outbound ports. The application layer depends on these abstractions only;
concrete adapters (Postgres, NATS, MinIO, in-memory) live in `ivaas.adapters`.

Interfaces are kept narrow on purpose (interface segregation): a use case
that only reads sessions never sees a write method.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ivaas.domain.models import Bay, Camera, LoadingSession, SessionStatus, Site


class CameraReader(Protocol):
    async def get(self, camera_id: UUID) -> Camera | None: ...

    async def list_for_bay(self, bay_id: UUID) -> list[Camera]: ...

    async def get_by_stream_path(self, stream_path: str) -> Camera | None: ...


class CameraWriter(Protocol):
    async def save(self, camera: Camera) -> None: ...

    async def delete(self, camera_id: UUID) -> None: ...


class SiteReader(Protocol):
    async def get(self, site_id: UUID) -> Site | None: ...

    async def list_all(self) -> list[Site]: ...


class SiteWriter(Protocol):
    async def save(self, site: Site) -> None: ...


class BayReader(Protocol):
    async def get(self, bay_id: UUID) -> Bay | None: ...

    async def list_all(self) -> list[Bay]: ...

    async def list_for_site(self, site_id: UUID) -> list[Bay]: ...


class BayWriter(Protocol):
    async def save(self, bay: Bay) -> None: ...


class SessionReader(Protocol):
    async def get(self, session_id: UUID) -> LoadingSession | None: ...

    async def get_open_for_bay(self, bay_id: UUID) -> LoadingSession | None: ...

    async def list_recent(
        self,
        *,
        bay_id: UUID | None = None,
        status: SessionStatus | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[LoadingSession]: ...


class SessionWriter(Protocol):
    async def save(self, session: LoadingSession) -> None: ...


class EventPublisher(Protocol):
    async def publish(self, subject: str, payload: dict) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
