"""In-memory adapters. Used by unit tests and by `IVAAS_STORAGE=memory` dev mode,
and they double as executable documentation of what each port must do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from ivaas.domain.models import Bay, Camera, LoadingSession, SessionStatus, Site


class InMemorySiteRepository:
    def __init__(self, sites: list[Site] | None = None) -> None:
        self._sites = {s.id: s for s in sites or []}

    async def get(self, site_id: UUID) -> Site | None:
        return self._sites.get(site_id)

    async def list_all(self) -> list[Site]:
        return sorted(self._sites.values(), key=lambda s: s.name)

    async def save(self, site: Site) -> None:
        self._sites[site.id] = site


class InMemoryBayRepository:
    def __init__(self, bays: list[Bay] | None = None) -> None:
        self._bays = {b.id: b for b in bays or []}

    async def get(self, bay_id: UUID) -> Bay | None:
        return self._bays.get(bay_id)

    async def list_all(self) -> list[Bay]:
        return sorted(self._bays.values(), key=lambda b: b.name)

    async def list_for_site(self, site_id: UUID) -> list[Bay]:
        return [b for b in await self.list_all() if b.site_id == site_id]

    async def save(self, bay: Bay) -> None:
        self._bays[bay.id] = bay


class InMemoryCameraRepository:
    def __init__(self, cameras: list[Camera] | None = None) -> None:
        self._cameras = {c.id: c for c in cameras or []}

    async def get(self, camera_id: UUID) -> Camera | None:
        return self._cameras.get(camera_id)

    async def list_for_bay(self, bay_id: UUID) -> list[Camera]:
        return [c for c in self._cameras.values() if c.bay_id == bay_id]

    async def get_by_stream_path(self, stream_path: str) -> Camera | None:
        return next((c for c in self._cameras.values() if c.stream_path == stream_path), None)

    async def save(self, camera: Camera) -> None:
        self._cameras[camera.id] = camera

    async def delete(self, camera_id: UUID) -> None:
        self._cameras.pop(camera_id, None)


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[UUID, LoadingSession] = {}

    async def get(self, session_id: UUID) -> LoadingSession | None:
        return self._sessions.get(session_id)

    async def get_open_for_bay(self, bay_id: UUID) -> LoadingSession | None:
        for s in self._sessions.values():
            if s.bay_id == bay_id and s.status is SessionStatus.OPEN:
                return s
        return None

    async def list_recent(
        self,
        *,
        bay_id: UUID | None = None,
        status: SessionStatus | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[LoadingSession]:
        rows = [
            s
            for s in self._sessions.values()
            if (bay_id is None or s.bay_id == bay_id)
            and (status is None or s.status is status)
            and (since is None or s.opened_at >= since)
        ]
        rows.sort(key=lambda s: s.opened_at, reverse=True)
        return rows[:limit]

    async def save(self, session: LoadingSession) -> None:
        self._sessions[session.id] = session


class InMemoryEventPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, subject: str, payload: dict) -> None:
        self.published.append((subject, payload))


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
