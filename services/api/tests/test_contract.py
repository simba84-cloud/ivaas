"""T6.3: the contract check itself, and the operations nothing else got a success from.

Every response in the suite is held against the OpenAPI spec as it arrives
(contract.py, wired in conftest), and a full run fails if any operation never
returned a checked success. These tests cover the operations no other test did,
and show that the check refuses what breaks the spec.
"""

from __future__ import annotations

import pytest
from conftest import login, make_client
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ivaas.ports.streaming import DiscoveredDevice, DiscoveredStream

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


class Frames:
    def __init__(self, frame: bytes | None) -> None:
        self.frame, self.urls = frame, []

    async def grab(self, url: str) -> bytes | None:
        self.urls.append(url)
        return self.frame


class Discovery:
    async def discover(self, timeout_s: float = 3.0):
        return [
            DiscoveredDevice(
                "http://192.168.1.20/onvif/device_service", "192.168.1.20", "Dock camera", "IPC-HDW"
            )
        ]

    async def streams(self, address, username, password):
        return [DiscoveredStream("main", (2560, 1440), "H264", "rtsp://192.168.1.20/main")]


@pytest.fixture
def c():
    with make_client() as client:
        yield client


def test_a_snapshot_is_a_jpeg_and_a_silent_camera_has_none(c):
    admin = login(c, "admin")
    bay = c.get("/api/v1/bays", headers=admin).json()[0]["id"]
    cam = c.get(f"/api/v1/bays/{bay}/cameras", headers=admin).json()[0]
    c.app.state.container.frames = frames = Frames(JPEG)
    r = c.get(f"/api/v1/cameras/{cam['id']}/snapshot", headers=admin)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == JPEG and frames.urls[0].endswith(cam["stream_path"])
    c.app.state.container.frames = Frames(None)
    assert c.get(f"/api/v1/cameras/{cam['id']}/snapshot", headers=admin).status_code == 404


def test_platform_config_and_metrics(c):
    assert c.get("/api/v1/config", headers=login(c, "admin")).json()["max_upload_mb"] > 0
    m = c.get("/metrics")
    assert m.status_code == 200 and m.headers["content-type"].startswith("text/plain")


def test_onvif_discovery_lists_devices_and_their_streams(c):
    admin = login(c, "admin")
    c.app.state.container.discovery = Discovery()
    [device] = c.post("/api/v1/discovery/onvif", headers=admin).json()
    assert device["host"] == "192.168.1.20"
    [stream] = c.post(
        "/api/v1/discovery/onvif/streams",
        json={"address": device["address"], "username": "admin", "password": "x"},
        headers=admin,
    ).json()
    assert stream["url"] == "rtsp://192.168.1.20/main"


def test_a_truck_can_be_edited(c):
    admin = login(c, "admin")
    truck = c.post("/api/v1/fleet", json={"plate": "ABC 1234"}, headers=admin).json()
    r = c.put(
        f"/api/v1/fleet/{truck['id']}",
        json={"plate": "ABC 1234", "fleet_number": "T-17", "operator": "Route 7"},
        headers=admin,
    )
    assert r.status_code == 200 and r.json()["fleet_number"] == "T-17"


def test_a_zone_can_be_changed_and_removed(c):
    admin = login(c, "admin")
    bay = c.get("/api/v1/bays", headers=admin).json()[0]["id"]
    cam = c.get(f"/api/v1/bays/{bay}/cameras", headers=admin).json()[0]["id"]
    body = {"name": "Dock", "polygon": [[0, 0], [1, 0], [1, 1]], "rules": ["intrusion"]}
    zone = c.post(f"/api/v1/cameras/{cam}/zones", json=body, headers=admin).json()
    r = c.put(f"/api/v1/zones/{zone['id']}", json={**body, "name": "Loading dock"}, headers=admin)
    assert r.status_code == 200 and r.json()["name"] == "Loading dock"
    assert c.delete(f"/api/v1/zones/{zone['id']}", headers=admin).status_code == 204
    assert c.get(f"/api/v1/bays/{bay}/zones", headers=admin).json() == []


def test_the_platform_sees_partners_and_any_tenant(c):
    platform = login(c, "platform")
    partners = c.get("/api/v1/platform/partners", headers=platform).json()
    assert partners and all({"id", "name"} <= set(p) for p in partners)
    tenant = c.get("/api/v1/platform/tenants", headers=platform).json()[0]
    one = c.get(f"/api/v1/platform/tenants/{tenant['id']}", headers=platform).json()
    assert one["id"] == tenant["id"]


# --- the check refuses what breaks the spec ------------------------------------------
class Thing(BaseModel):
    name: str
    count: int


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/thing", response_model=Thing)
    async def thing(kind: str = "ok"):
        if kind == "wrong-shape":  # bypasses FastAPI's own response validation
            return JSONResponse({"name": "x", "count": "many"})
        if kind == "undocumented":
            return JSONResponse({"name": "x", "count": 1}, status_code=202)
        if kind == "bare-error":
            return JSONResponse({"oops": True}, status_code=400)
        return {"name": "x", "count": 1}

    return app


def test_a_response_that_breaks_the_spec_fails_the_test_that_got_it():
    client = TestClient(_app())
    assert client.get("/thing").status_code == 200
    for kind, says in [
        ("wrong-shape", "at count: 'many' is not of type 'integer'"),
        ("undocumented", "status not in the spec"),
        ("bare-error", "an error without 'detail'"),
    ]:
        with pytest.raises(AssertionError, match="breaks the OpenAPI spec") as e:
            client.get("/thing", params={"kind": kind})
        assert says in str(e.value), kind
