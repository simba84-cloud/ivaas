"""Ports for the camera-agnostic video layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ivaas.domain.models import StreamSource


class StreamGateway(Protocol):
    """The media server that normalises every camera into one internal format.

    Whatever a camera speaks, the gateway re-serves it as RTSP (for the AI
    pipeline) and WebRTC (for the portal) at `stream_path`. Nothing downstream
    ever sees a vendor protocol.
    """

    async def provision(self, stream_path: str, source: StreamSource) -> None: ...

    async def remove(self, stream_path: str) -> None: ...

    async def live_paths(self) -> set[str]:
        """Stream paths currently receiving video, whatever their source."""
        ...


@dataclass(frozen=True)
class DiscoveredDevice:
    address: str  # ONVIF device service URL
    host: str
    name: str | None
    hardware: str | None


@dataclass(frozen=True)
class DiscoveredStream:
    profile: str
    resolution: tuple[int, int] | None
    encoding: str | None
    url: str


class CameraDiscovery(Protocol):
    async def discover(self, timeout_s: float = 3.0) -> list[DiscoveredDevice]: ...

    async def streams(
        self, address: str, username: str, password: str
    ) -> list[DiscoveredStream]: ...
