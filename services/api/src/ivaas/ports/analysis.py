from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID

from ivaas.domain.analysis import AnalysisJob


class AnalysisJobStore(Protocol):
    async def get(self, job_id: UUID) -> AnalysisJob | None: ...

    async def list_recent(self, limit: int = 50) -> list[AnalysisJob]: ...

    async def save(self, job: AnalysisJob) -> None: ...

    async def next_queued(self) -> AnalysisJob | None: ...


class ObjectStore(Protocol):
    """Blob storage for uploaded videos and captured frames (MinIO / S3)."""

    async def put(
        self, key: str, data: AsyncIterator[bytes] | bytes, content_type: str
    ) -> None: ...

    async def get_url(self, key: str, expires_s: int = 3600) -> str: ...

    async def download_to(self, key: str, path: str) -> None: ...

    async def delete(self, key: str) -> None: ...


class VideoAnalyser(Protocol):
    """Runs the counting pipeline over a local video file. Implemented by the pipeline
    service; the API talks to it through this port so the two stay separable."""

    async def analyse(
        self, job: AnalysisJob, local_path: str, save_frame
    ) -> tuple[list, list, float]: ...
