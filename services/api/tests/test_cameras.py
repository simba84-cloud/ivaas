import httpx
import pytest

from ivaas.adapters.streaming.mediamtx import MediaMtxGateway, StreamGatewayError
from ivaas.adapters.streaming.onvif import (
    envelope,
    is_lan_device_url,
    parse_media_address,
    parse_probe_match,
    parse_profiles,
    with_credentials,
)
from ivaas.domain.models import InvalidStreamSourceError, StreamSource


# --- any transport, any vendor --------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "rtsp://admin:pw@192.168.1.64:554/Streaming/Channels/101",  # Hikvision
        "rtsp://admin:pw@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0",  # Dahua
        "rtsp://admin:pw@192.168.1.13:554/media/video1",  # Uniview
        "rtsps://cam.example.com:322/stream",
        "rtmp://encoder.local/live/bay1",
        "srt://10.0.0.5:9000?streamid=bay1",
        "http://192.168.1.20/mjpg/video.mjpg",  # Axis MJPEG
        "https://cdn.example.com/bay1/index.m3u8",  # HLS
        "udp://239.0.0.1:1234",  # MPEG-TS multicast
        "whep://gateway.local/cam1/whep",
    ],
)
def test_every_pull_transport_is_accepted(url):
    assert StreamSource(url).protocol == url.split(":")[0]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y", "not a url", "rtsp://"])
def test_unsafe_or_malformed_sources_are_rejected(url):
    with pytest.raises(InvalidStreamSourceError):
        StreamSource(url)


def test_password_is_redacted_but_rest_of_url_survives():
    src = StreamSource("rtsp://admin:s3cr3t@10.0.0.9:8554/ch1?sub=0")
    assert src.redacted == "rtsp://admin:***@10.0.0.9:8554/ch1?sub=0"
    assert "s3cr3t" not in src.redacted


# --- HTTP -------------------------------------------------------------------
def test_register_list_remove_camera(client):
    bay = client.get("/api/v1/bays").json()[0]
    r = client.post(
        f"/api/v1/bays/{bay['id']}/cameras",
        json={
            "name": "Dock Door PTZ",
            "role": "chokepoint",
            "source_url": "rtsp://admin:hunter2@192.168.1.64:554/Streaming/Channels/101",
        },
    )
    assert r.status_code == 201, r.text
    cam = r.json()
    # the stream path is derived from the bay name, so renaming the bay changes it
    assert cam["stream_path"] == "loading-bay/dock-door-ptz"
    assert cam["protocol"] == "rtsp"
    assert "hunter2" not in r.text

    listed = client.get(f"/api/v1/bays/{bay['id']}/cameras")
    assert "hunter2" not in listed.text
    assert len(listed.json()) == 18

    assert client.delete(f"/api/v1/cameras/{cam['id']}").status_code == 204
    assert len(client.get(f"/api/v1/bays/{bay['id']}/cameras").json()) == 17
    assert client.delete(f"/api/v1/cameras/{cam['id']}").status_code == 404


def test_push_camera_needs_no_url(client):
    bay = client.get("/api/v1/bays").json()[0]
    r = client.post(
        f"/api/v1/bays/{bay['id']}/cameras", json={"name": "Body cam", "role": "overhead"}
    )
    assert r.status_code == 201
    assert r.json()["protocol"] == "push"


def test_bad_scheme_is_422_and_duplicate_is_409(client):
    bay = client.get("/api/v1/bays").json()[0]
    url = f"/api/v1/bays/{bay['id']}/cameras"
    bad = client.post(url, json={"name": "X", "role": "overhead", "source_url": "file:///x"})
    assert bad.status_code == 422
    ok = {"name": "Twin", "role": "overhead", "source_url": "rtsp://10.0.0.1/a"}
    assert client.post(url, json=ok).status_code == 201
    assert client.post(url, json=ok).status_code == 409


# --- MediaMTX adapter against a fake control API ----------------------------
@pytest.mark.asyncio
async def test_mediamtx_gateway_adds_then_patches_and_tolerates_missing_delete():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content.decode()))
        if request.method == "POST" and len([c for c in calls if c[0] == "POST"]) > 1:
            return httpx.Response(400, json={"error": "path already exists"})
        if request.method == "DELETE":
            return httpx.Response(404)
        return httpx.Response(200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mtx:9997")
    gw = MediaMtxGateway("http://mtx:9997", client=http)
    await gw.provision("bay/cam-1", StreamSource("rtsp://u:p@10.0.0.1/s"))
    await gw.provision("bay/cam-1", StreamSource("rtsp://u:p@10.0.0.2/s"))
    await gw.provision("bay/push", StreamSource(None))
    await gw.remove("bay/gone")

    assert calls[0][:2] == ("POST", "/v3/config/paths/add/bay/cam-1")
    assert '"sourceOnDemand":true' in calls[0][2].replace(" ", "")
    assert calls[2][:2] == ("PATCH", "/v3/config/paths/patch/bay/cam-1")
    assert '"publisher"' in calls[-2][2] or '"publisher"' in calls[-3][2]


@pytest.mark.asyncio
async def test_mediamtx_errors_never_leak_credentials():
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")),
        base_url="http://mtx:9997",
    )
    with pytest.raises(StreamGatewayError) as err:
        await MediaMtxGateway("x", client=http).provision(
            "bay/c", StreamSource("rtsp://admin:topsecret@10.0.0.1/s")
        )
    assert "topsecret" not in str(err.value)


# --- ONVIF parsing against spec-shaped responses -----------------------------
PROBE_MATCH = b"""<?xml version="1.0"?><SOAP-ENV:Envelope
 xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><SOAP-ENV:Body><d:ProbeMatches>
 <d:ProbeMatch><d:Scopes>onvif://www.onvif.org/type/video_encoder
 onvif://www.onvif.org/hardware/IPC3614SR3 onvif://www.onvif.org/name/Dock%20Cam%202</d:Scopes>
 <d:XAddrs>http://[fe80::1]/onvif/device_service http://192.168.1.13:80/onvif/device_service</d:XAddrs>
 </d:ProbeMatch></d:ProbeMatches></SOAP-ENV:Body></SOAP-ENV:Envelope>"""

SERVICES = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><s:Body><tds:GetServicesResponse>
 <tds:Service><tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>
 <tds:XAddr>http://192.168.1.13/onvif/device_service</tds:XAddr></tds:Service>
 <tds:Service><tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>
 <tds:XAddr>http://192.168.1.13/onvif/media_service</tds:XAddr></tds:Service>
 </tds:GetServicesResponse></s:Body></s:Envelope>"""

PROFILES = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
 <s:Body><trt:GetProfilesResponse>
 <trt:Profiles token="Profile_1"><tt:Name>mainStream</tt:Name><tt:VideoEncoderConfiguration>
 <tt:Encoding>H265</tt:Encoding><tt:Resolution><tt:Width>2560</tt:Width><tt:Height>1440</tt:Height>
 </tt:Resolution></tt:VideoEncoderConfiguration></trt:Profiles>
 <trt:Profiles token="Profile_2"><tt:Name>subStream</tt:Name></trt:Profiles>
 </trt:GetProfilesResponse></s:Body></s:Envelope>"""


def test_onvif_probe_match_prefers_ipv4_and_decodes_scopes():
    d = parse_probe_match(PROBE_MATCH)
    assert d.address == "http://192.168.1.13:80/onvif/device_service"
    assert (d.host, d.name, d.hardware) == ("192.168.1.13", "Dock Cam 2", "IPC3614SR3")
    assert parse_probe_match(b"not xml") is None


def test_onvif_media_address_and_profiles():
    assert parse_media_address(SERVICES) == "http://192.168.1.13/onvif/media_service"
    assert parse_profiles(PROFILES) == [
        ("Profile_1", "mainStream", (2560, 1440), "H265"),
        ("Profile_2", "subStream", None, None),
    ]


def test_onvif_envelope_uses_digest_not_plaintext_password():
    xml = envelope("<x/>", "admin", "p&ss<word>")
    assert "p&ss" not in xml and "PasswordDigest" in xml
    import xml.etree.ElementTree as ET

    ET.fromstring(xml)  # well-formed


def test_stream_url_gets_encoded_credentials_once():
    assert (
        with_credentials("rtsp://10.0.0.1:554/s", "admin", "p@ss:w/rd")
        == "rtsp://admin:p%40ss%3Aw%2Frd@10.0.0.1:554/s"
    )
    assert with_credentials("rtsp://a:b@10.0.0.1/s", "x", "y") == "rtsp://a:b@10.0.0.1/s"


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://192.168.1.13/onvif/device_service", True),
        ("http://10.4.0.9:8080/onvif/device_service", True),
        ("http://169.254.169.254/latest/meta-data", False),  # cloud metadata service
        ("http://127.0.0.1:8000/api/v1/bays", False),
        ("http://8.8.8.8/x", False),
        ("http://internal.corp/x", False),
        ("file:///etc/passwd", False),
    ],
)
def test_onvif_address_must_be_a_lan_ip(url, ok):
    assert is_lan_device_url(url) is ok


def test_onvif_streams_endpoint_refuses_non_lan_targets(client):
    r = client.post(
        "/api/v1/discovery/onvif/streams",
        json={"address": "http://127.0.0.1:8000/x", "username": "a", "password": "b"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_camera_status_follows_what_the_gateway_receives():
    from ivaas.adapters.persistence.memory import (
        InMemoryBayRepository,
        InMemoryCameraRepository,
        SystemClock,
    )
    from ivaas.adapters.streaming.mediamtx import NullStreamGateway
    from ivaas.application.cameras import RefreshCameraStatus
    from ivaas.config.container import demo_topology
    from ivaas.domain.models import CameraStatus

    _, bay, cams = demo_topology()
    repo, gw = InMemoryCameraRepository(cams), NullStreamGateway()
    refresh = RefreshCameraStatus(InMemoryBayRepository([bay]), repo, gw, SystemClock())

    gw.live = {cams[0].stream_path}
    changed = await refresh()
    assert changed == {"online": 1, "offline": 0}
    assert (await repo.get(cams[0].id)).status is CameraStatus.ONLINE
    assert (await repo.get(cams[1].id)).status is CameraStatus.OFFLINE

    gw.live = set()  # the stream drops
    changed = await refresh()
    assert changed["offline"] == 1
    assert (await repo.get(cams[0].id)).status is CameraStatus.OFFLINE
    assert (await repo.get(cams[0].id)).last_seen_at is not None  # history kept
