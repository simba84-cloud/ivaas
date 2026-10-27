"""Whether a pulled camera is up, asked without pulling its video (RTSP OPTIONS)."""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from urllib.parse import SplitResult, urlsplit, urlunsplit

from ivaas.domain.models import StreamSource

#: a camera that has not answered by then is treated as not answering
TIMEOUT_S = 3.0

#: srt and udp are absent: connectionless, so nothing answers short of the video itself
DEFAULT_PORTS = {
    "rtsp": 554,
    "rtsps": 322,
    "rtmp": 1935,
    "rtmps": 443,
    "http": 80,
    "https": 443,
    "whep": 80,
    "wheps": 443,
}


class NetworkSourceProbe:
    """RTSP cameras must reply to OPTIONS; other transports must accept a connection.

    A reply proves the camera is up, not that its credentials are right: OPTIONS
    needs none, which is also why the password is never sent.
    """

    async def answers(self, source: StreamSource) -> bool:
        if source.url is None:
            return False
        parts = urlsplit(source.url)
        scheme = parts.scheme.lower()
        port = parts.port or DEFAULT_PORTS.get(scheme)
        if port is None or not parts.hostname:
            return False
        try:
            return await asyncio.wait_for(self._ask(parts, scheme, port), TIMEOUT_S)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError):
            return False

    async def _ask(self, parts: SplitResult, scheme: str, port: int) -> bool:
        tls: ssl.SSLContext | None = None
        if scheme == "rtsps":
            # Cameras serve self-signed certificates, and nothing secret is sent.
            tls = ssl.create_default_context()
            tls.check_hostname = False
            tls.verify_mode = ssl.CERT_NONE
        host = parts.hostname or ""
        reader, writer = await asyncio.open_connection(host, port, ssl=tls)
        try:
            if scheme not in ("rtsp", "rtsps"):
                return True
            netloc = f"[{host}]" if ":" in host else host
            if parts.port:
                netloc = f"{netloc}:{parts.port}"
            target = urlunsplit(parts._replace(netloc=netloc))
            writer.write(f"OPTIONS {target} RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode())
            await writer.drain()
            return (await reader.readline()).startswith(b"RTSP/1.0 ")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
