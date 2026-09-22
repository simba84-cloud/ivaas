"""ONVIF camera discovery, implemented directly against the standard.

ONVIF is the vendor-neutral interface implemented by Hikvision, Dahua, Uniview,
Axis, Bosch, Hanwha and nearly every other IP camera and NVR. Two steps:

  1. WS-Discovery: a UDP multicast probe; devices answer with their service URL.
  2. Media service: GetProfiles + GetStreamUri (WS-Security digest auth) yield
     the RTSP URL of each stream, which is then registered like any other source.

Written against the spec rather than a SOAP library to keep the dependency
surface small; only the four messages we need are implemented.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import os
import socket
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx

from ivaas.ports.streaming import DiscoveredDevice, DiscoveredStream

MULTICAST = ("239.255.255.250", 3702)
NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}

PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<s:Header><a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>
<a:MessageID>uuid:{message_id}</a:MessageID>
<a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To></s:Header>
<s:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></s:Body>
</s:Envelope>"""


def parse_probe_match(xml: bytes) -> DiscoveredDevice | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    xaddrs = root.findtext(".//d:XAddrs", namespaces=NS)
    if not xaddrs:
        return None
    # devices may advertise several URLs (IPv4, IPv6, link-local); prefer plain IPv4
    urls = xaddrs.split()
    address = next((u for u in urls if "[" not in u and "169.254." not in u), urls[0])
    scopes = (root.findtext(".//d:Scopes", namespaces=NS) or "").split()

    def scope(key: str) -> str | None:
        prefix = f"onvif://www.onvif.org/{key}/"
        return next((unquote(s[len(prefix) :]) for s in scopes if s.startswith(prefix)), None)

    return DiscoveredDevice(
        address=address,
        host=urlsplit(address).hostname or "",
        name=scope("name"),
        hardware=scope("hardware"),
    )


def security_header(username: str, password: str) -> str:
    """WS-Security UsernameToken with PasswordDigest = B64(SHA1(nonce + created + password))."""
    nonce = os.urandom(16)
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()  # noqa: S324 (spec)
    ).decode()
    wsse = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss"
    return (
        f'<s:Header><Security s:mustUnderstand="1" xmlns="{wsse}-wssecurity-secext-1.0.xsd">'
        f"<UsernameToken><Username>{_esc(username)}</Username>"
        f'<Password Type="{wsse}-username-token-profile-1.0#PasswordDigest">{digest}</Password>'
        f'<Nonce EncodingType="{wsse}-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</Nonce>"
        f'<Created xmlns="{wsse}-wssecurity-utility-1.0.xsd">{created}</Created>'
        "</UsernameToken></Security></s:Header>"
    )


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def envelope(body: str, username: str, password: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{NS["s"]}">{security_header(username, password)}'
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )


def parse_media_address(xml: bytes) -> str | None:
    root = ET.fromstring(xml)
    for service in root.iterfind(".//tds:Service", NS):
        if service.findtext("tds:Namespace", namespaces=NS) == NS["trt"]:
            return service.findtext("tds:XAddr", namespaces=NS)
    return None


def parse_profiles(xml: bytes) -> list[tuple[str, str, tuple[int, int] | None, str | None]]:
    out = []
    for p in ET.fromstring(xml).iterfind(".//trt:Profiles", NS):
        enc = p.find("tt:VideoEncoderConfiguration", NS)
        width = enc.findtext("tt:Resolution/tt:Width", namespaces=NS) if enc is not None else None
        height = enc.findtext("tt:Resolution/tt:Height", namespaces=NS) if enc is not None else None
        out.append(
            (
                p.get("token", ""),
                p.findtext("tt:Name", default="", namespaces=NS),
                (int(width), int(height)) if width and height else None,
                enc.findtext("tt:Encoding", namespaces=NS) if enc is not None else None,
            )
        )
    return out


def with_credentials(url: str, username: str, password: str) -> str:
    parts = urlsplit(url)
    if parts.username or not username:
        return url
    netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{parts.netloc}"
    return urlunsplit(parts._replace(netloc=netloc))


def is_lan_device_url(url: str) -> bool:
    """ONVIF devices live on the local network. Refusing anything else stops this
    endpoint being used to make the server call arbitrary hosts (SSRF)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return False  # hostnames are refused: they can resolve to anything
    # link-local is excluded: 169.254.169.254 is the cloud metadata service
    return ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)


class OnvifDiscovery:
    async def discover(self, timeout_s: float = 3.0) -> list[DiscoveredDevice]:
        return await asyncio.to_thread(self._probe, timeout_s)

    @staticmethod
    def _probe(timeout_s: float) -> list[DiscoveredDevice]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        found: dict[str, DiscoveredDevice] = {}
        try:
            sock.sendto(PROBE.format(message_id=uuid.uuid4()).encode(), MULTICAST)
            end = time.monotonic() + timeout_s
            while time.monotonic() < end:
                try:
                    data, _ = sock.recvfrom(65535)
                except TimeoutError:
                    continue
                device = parse_probe_match(data)
                if device is not None:
                    found[device.address] = device
        except OSError:
            pass  # no multicast route (e.g. inside a bridged container): report nothing found
        finally:
            sock.close()
        return sorted(found.values(), key=lambda d: d.host)

    async def streams(self, address: str, username: str, password: str) -> list[DiscoveredStream]:
        async with httpx.AsyncClient(timeout=8.0) as client:

            async def call(url: str, body: str) -> bytes:
                r = await client.post(
                    url,
                    content=envelope(body, username, password),
                    headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                )
                r.raise_for_status()
                return r.content

            services = await call(
                address,
                f'<GetServices xmlns="{NS["tds"]}"><IncludeCapability>false</IncludeCapability>'
                "</GetServices>",
            )
            media = parse_media_address(services) or address
            profiles = parse_profiles(await call(media, f'<GetProfiles xmlns="{NS["trt"]}"/>'))

            out: list[DiscoveredStream] = []
            for token, name, resolution, encoding in profiles:
                xml = await call(
                    media,
                    f'<GetStreamUri xmlns="{NS["trt"]}"><StreamSetup>'
                    f'<Stream xmlns="{NS["tt"]}">RTP-Unicast</Stream>'
                    f'<Transport xmlns="{NS["tt"]}"><Protocol>RTSP</Protocol></Transport>'
                    f"</StreamSetup><ProfileToken>{_esc(token)}</ProfileToken></GetStreamUri>",
                )
                uri = ET.fromstring(xml).findtext(".//tt:Uri", namespaces=NS)
                if uri:
                    out.append(
                        DiscoveredStream(
                            name or token,
                            resolution,
                            encoding,
                            with_credentials(uri, username, password),
                        )
                    )
            return out
