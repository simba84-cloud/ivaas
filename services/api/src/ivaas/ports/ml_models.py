"""Port for the model registry."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ivaas.domain.ml_models import ModelVersion


class ModelRegistry(Protocol):
    async def save(self, model: ModelVersion) -> None: ...

    async def get(self, model_id: UUID) -> ModelVersion | None: ...

    async def find(self, name: str, version: str) -> ModelVersion | None: ...

    async def list_all(self) -> list[ModelVersion]: ...
