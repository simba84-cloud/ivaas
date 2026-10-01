"""Sending a webhook over HTTP(S), refusing to be pointed at the platform's own network.

A tenant chooses the URL, so it is a way to make the platform send requests. The
name is resolved here and every address it resolves to must be public (unless the
deployment allows receivers on its own network); redirects are not followed, since a
public receiver could redirect to an internal one.

A name that resolves to a public address when checked and a private one a moment
later, when httpx connects, would get past this. Closing that needs the connection
pinned to the checked address; until then the check stops the ordinary mistakes and
misconfigurations, and the deployment's egress firewall is the backstop.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from ivaas.domain.webhooks import address_allowed
from ivaas.ports.webhooks import SendResult

Resolver = Callable[[str, int], Awaitable[list[str]]]


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class HttpWebhookSender:
    def __init__(
        self,
        *,
        allow_private: bool = False,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver = resolve,
        timeout_s: float = 10.0,
    ) -> None:
        self._allow_private = allow_private
        self._client = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)
        self._resolve = resolver

    async def send(self, url: str, headers: dict[str, str], body: bytes) -> SendResult:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            addresses = await self._resolve(parts.hostname or "", port)
        except OSError as exc:
            return SendResult(None, f"could not resolve {parts.hostname}: {exc}")
        refused = [
            a for a in addresses if not address_allowed(a, allow_private=self._allow_private)
        ]
        if not addresses or refused:
            return SendResult(None, f"{parts.hostname} resolves to a private address; not sent")
        try:
            r = await self._client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            return SendResult(None, f"{type(exc).__name__}: {exc}"[:300])
        if 300 <= r.status_code < 400:
            return SendResult(r.status_code, "redirects are not followed; give the final URL")
        return SendResult(r.status_code, None if r.is_success else r.text[:200] or None)

    async def aclose(self) -> None:
        await self._client.aclose()
