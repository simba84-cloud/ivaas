"""Record each heartbeat into the availability history; report it over a window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ivaas.domain.availability import Availability, availability, observe
from ivaas.ports.availability import AvailabilityStore


@dataclass
class RecordAvailability:
    store: AvailabilityStore

    async def __call__(self, node_id: UUID, report: dict[str, Any], at: datetime) -> None:
        latest = await self.store.latest(node_id)
        spool = int(report.get("spool_pending") or 0)
        changed = [
            observe(latest.get(None), node_id=node_id, camera_id=None, up=True, at=at, spool=spool)
        ]
        for cam in report.get("cameras") or []:
            try:
                camera_id = UUID(str(cam["api_camera_id"]))
            except (KeyError, ValueError):
                continue  # a camera the node cannot name is not one we can report on
            changed.append(
                observe(
                    latest.get(camera_id),
                    node_id=node_id,
                    camera_id=camera_id,
                    up=bool(cam.get("connected")),
                    at=at,
                )
            )
        await self.store.save_all(changed)


@dataclass
class NodeAvailability:
    """A node's and its cameras' uptime and outages over [start, end)."""

    store: AvailabilityStore

    async def __call__(
        self, node_id: UUID, start: datetime, end: datetime
    ) -> tuple[Availability, dict[UUID, Availability]]:
        periods = await self.store.between([node_id], start, end)
        node = availability([p for p in periods if p.camera_id is None], start, end)
        cameras = {
            cid: availability([p for p in periods if p.camera_id == cid], start, end)
            for cid in {p.camera_id for p in periods if p.camera_id is not None}
        }
        return node, cameras
