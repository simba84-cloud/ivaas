"""StreamGateway backed by the MediaMTX control API (v3)."""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from ivaas.domain.models import StreamSource

log = logging.getLogger(__name__)


class StreamGatewayError(RuntimeError):
    pass


class MediaMtxGateway:
    def __init__(self, api_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=api_url, timeout=5.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _config(source: StreamSource) -> dict:
        if source.is_push:
            return {"source": "publisher"}
        # Pull only while someone is watching or analysing: a 16-camera bay of 4K
        # streams should not saturate the uplink when nobody is looking.
        return {"source": source.url, "sourceOnDemand": True}

    async def provision(self, stream_path: str, source: StreamSource) -> None:
        name = quote(stream_path, safe="/")
        body = self._config(source)
        try:
            r = await self._client.post(f"/v3/config/paths/add/{name}", json=body)
            if r.status_code == 400 and "already exists" in r.text:
                r = await self._client.patch(f"/v3/config/paths/patch/{name}", json=body)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            # never echo `body`: it carries the camera credentials
            raise StreamGatewayError(f"media gateway rejected path '{stream_path}': {exc}") from exc
        log.info("provisioned %s (%s)", stream_path, source.protocol)

    async def live_paths(self) -> set[str]:
        try:
            r = await self._client.get("/v3/paths/list", params={"itemsPerPage": 1000})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise StreamGatewayError(f"media gateway unreachable: {exc}") from exc
        return {p["name"] for p in r.json().get("items", []) if p.get("ready")}

    async def remove(self, stream_path: str) -> None:
        name = quote(stream_path, safe="/")
        try:
            r = await self._client.delete(f"/v3/config/paths/delete/{name}")
            if r.status_code != 404:  # already gone is fine
                r.raise_for_status()
        except httpx.HTTPError as exc:
            raise StreamGatewayError(f"could not remove path '{stream_path}': {exc}") from exc


class NullStreamGateway:
    """Dev/test gateway: records calls, talks to nothing."""

    def __init__(self) -> None:
        self.paths: dict[str, StreamSource] = {}
        self.live: set[str] = set()

    async def provision(self, stream_path: str, source: StreamSource) -> None:
        self.paths[stream_path] = source

    async def remove(self, stream_path: str) -> None:
        self.paths.pop(stream_path, None)

    async def live_paths(self) -> set[str]:
        return set(self.live)  # tests set this directly
