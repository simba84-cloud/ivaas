"""Sinks that deliver pipeline events to the core API through SpooledDelivery."""

from __future__ import annotations

from ivaas_pipeline.adapters.delivery import SpooledDelivery
from ivaas_pipeline.types import CrossDirection, Crossing, PlateEvent


class HttpCrossingSink:
    def __init__(
        self,
        delivery: SpooledDelivery,
        bay_id: str,
        camera_ids: dict[str, str],
        *,
        forward_means: str = "loading",
    ) -> None:
        self._delivery = delivery
        self._bay_id = bay_id
        self._camera_ids = camera_ids  # pipeline camera key -> API camera UUID
        self._forward = forward_means
        self._backward = "offloading" if forward_means == "loading" else "loading"

    def emit(self, crossing: Crossing) -> None:
        self._delivery.send(
            "/api/v1/ingest/crossings",
            {
                "bay_id": self._bay_id,
                "camera_id": self._camera_ids[crossing.camera_id],
                "track_id": crossing.track_id,
                "direction": (
                    self._forward
                    if crossing.direction is CrossDirection.FORWARD
                    else self._backward
                ),
                "crates": crossing.crates,
                "confidence": round(crossing.confidence, 4),
                "crossed_at": crossing.at.isoformat(),
            },
        )


class HttpPlateSink:
    def __init__(self, delivery: SpooledDelivery, bay_id: str, camera_ids: dict[str, str]) -> None:
        self._delivery = delivery
        self._bay_id = bay_id
        self._camera_ids = camera_ids

    def emit(self, event: PlateEvent) -> None:
        self._delivery.send(
            "/api/v1/ingest/plates",
            {
                "bay_id": self._bay_id,
                "camera_id": self._camera_ids[event.camera_id],
                "plate": event.plate,
                "confidence": round(event.confidence, 4),
                "read_at": event.at.isoformat(),
            },
        )
