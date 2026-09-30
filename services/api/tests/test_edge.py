"""Edge nodes (proposal M2): enrollment, site binding, fleet health, pulled config."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import login, make_client

from ivaas.domain.models import Bay, Camera, CameraRole, Site
from ivaas.domain.tenancy import BAKERS_INN_ID, stable_id
from ivaas.tenancy import tenant_context

SITE_Y = Site(id=stable_id("site.edge-y"), name="Depot Y", timezone="Africa/Harare")
BAY_Y = Bay(id=stable_id("bay.edge-y"), site_id=SITE_Y.id, name="Bay Y")
CAM_Y = Camera(stable_id("cam.edge-y"), BAY_Y.id, "Y chokepoint", CameraRole.CHOKEPOINT, "y/choke")


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at


@pytest.fixture
def bakers():
    """Bakers Inn with its demo site (X) and a second site (Y), on a controllable clock."""
    with make_client() as c:
        container = c.app.state.container
        container.clock = clock = Clock()

        async def seed():
            with tenant_context(BAKERS_INN_ID):
                await container.sites.save(SITE_Y)
                await container.bays.save(BAY_Y)
                await container.cameras.save(CAM_Y)

        c.portal.call(seed)
        admin = login(c, "admin")
        bays = c.get("/api/v1/bays", headers=admin).json()
        bay = next(b for b in bays if b["id"] != str(BAY_Y.id))  # site X's bay
        yield c, admin, bay, clock


def token(c, admin, site_id, **body):
    r = c.post(
        f"/api/v1/sites/{site_id}/enrollment-tokens", json={"name": "Edge 1", **body}, headers=admin
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]


def enrol(c, tok, hostname="edge-01"):
    return c.post("/api/v1/edge/enroll", json={"token": tok, "hostname": hostname})


def node_headers(enrolled):
    return {"X-IVaaS-Node": enrolled["credential"]}


# --- T2.1 ----------------------------------------------------------------------------


def test_a_token_enrols_one_node_once(bakers):
    c, admin, bay, _ = bakers
    tok = token(c, admin, bay["site_id"])
    first = enrol(c, tok)
    assert first.status_code == 201, first.text
    assert first.json()["site_id"] == bay["site_id"]
    again = enrol(c, tok, hostname="copycat")
    assert again.status_code == 401
    nodes = c.get("/api/v1/edge/nodes", headers=admin).json()
    assert [n["hostname"] for n in nodes] == ["edge-01"]


def test_expired_used_and_forged_tokens_get_one_answer(bakers):
    c, admin, bay, clock = bakers
    expired = token(c, admin, bay["site_id"], ttl_hours=1)
    clock.at += timedelta(hours=2)
    used = token(c, admin, bay["site_id"])
    enrol(c, used)
    forged = used[:-4] + "AAAA"
    answers = {enrol(c, t).text for t in (expired, used, forged, "ivaas-enr-garbage", "x")}
    assert len(answers) == 1, answers  # nothing tells an attacker which kind of wrong


def test_the_token_is_shown_once_and_stored_only_as_a_digest(bakers):
    c, admin, bay, _ = bakers
    tok = token(c, admin, bay["site_id"])
    secret = tok.split(".", 1)[1]
    container = c.app.state.container
    stored = next(iter(container.edge._tokens.values()))
    assert secret not in repr(stored)


def test_a_node_is_invisible_to_other_tenants(bakers):
    c, admin, bay, _ = bakers
    enrol(c, token(c, admin, bay["site_id"]))
    other = login(c, "b-admin")
    assert c.get("/api/v1/edge/nodes", headers=other).json() == []


def test_only_device_registrars_create_tokens(bakers):
    c, _, bay, _ = bakers
    for user in ("operator", "viewer"):
        r = c.post(
            f"/api/v1/sites/{bay['site_id']}/enrollment-tokens",
            json={"name": "x"},
            headers=login(c, user),
        )
        assert r.status_code == 403, user


# --- T2.2 (in the API): a node speaks only for its own site -------------------------


def crossing(bay_id, camera_id):
    return {
        "bay_id": bay_id,
        "camera_id": camera_id,
        "track_id": 1,
        "direction": "loading",
        "crates": 12,
        "confidence": 0.9,
        "crossed_at": datetime.now(UTC).isoformat(),
    }


def test_a_node_cannot_report_for_another_site(bakers):
    c, admin, bay, _ = bakers
    node = node_headers(enrol(c, token(c, admin, bay["site_id"])).json())
    y = str(BAY_Y.id)
    c.post("/api/v1/sessions", json={"bay_id": y, "direction": "loading"}, headers=admin)
    before = c.get(f"/api/v1/sessions?bay_id={y}", headers=admin).json()

    assert (
        c.post(
            "/api/v1/ingest/crossings", json=crossing(y, str(CAM_Y.id)), headers=node
        ).status_code
        == 404
    )
    plate = {
        "bay_id": y,
        "camera_id": str(CAM_Y.id),
        "plate": "ABC 1",
        "confidence": 0.9,
        "read_at": datetime.now(UTC).isoformat(),
    }
    assert c.post("/api/v1/ingest/plates", json=plate, headers=node).status_code == 404
    assert c.post(f"/api/v1/cameras/{CAM_Y.id}/heartbeat", headers=node).status_code == 404
    assert c.get(f"/api/v1/pipeline/security?bay_id={y}", headers=node).status_code == 404
    assert c.get(f"/api/v1/sessions?bay_id={y}", headers=admin).json() == before


def test_a_node_reports_for_its_own_site(bakers):
    c, admin, bay, _ = bakers
    node = node_headers(enrol(c, token(c, admin, bay["site_id"])).json())
    cam = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()[0]["id"]
    c.post("/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}, headers=admin)
    r = c.post("/api/v1/ingest/crossings", json=crossing(bay["id"], cam), headers=node)
    assert r.status_code == 200 and r.json()["ai_count"] == 12


def test_a_camera_from_another_bay_is_refused_even_at_the_right_bay(bakers):
    c, admin, bay, _ = bakers
    node = node_headers(enrol(c, token(c, admin, bay["site_id"])).json())
    r = c.post("/api/v1/ingest/crossings", json=crossing(bay["id"], str(CAM_Y.id)), headers=node)
    assert r.status_code == 404


def test_a_revoked_node_is_refused_at_once(bakers):
    c, admin, bay, _ = bakers
    enrolled = enrol(c, token(c, admin, bay["site_id"])).json()
    node = node_headers(enrolled)
    assert c.post("/api/v1/edge/heartbeat", json={}, headers=node).status_code == 200
    assert c.delete(f"/api/v1/edge/nodes/{enrolled['node_id']}", headers=admin).status_code == 204
    assert c.post("/api/v1/edge/heartbeat", json={}, headers=node).status_code == 401
    listed = c.get("/api/v1/edge/nodes", headers=admin).json()[0]
    assert listed["health"] == "revoked"


def test_a_node_cannot_manage_the_fleet_or_browse_people(bakers):
    c, admin, bay, _ = bakers
    enrolled = enrol(c, token(c, admin, bay["site_id"])).json()
    node = node_headers(enrolled)
    assert c.delete(f"/api/v1/edge/nodes/{enrolled['node_id']}", headers=node).status_code == 403
    assert c.get("/api/v1/users", headers=node).status_code == 403
    tok = c.post(
        f"/api/v1/sites/{bay['site_id']}/enrollment-tokens", json={"name": "x"}, headers=node
    )
    assert tok.status_code == 403


# --- T2.5: fleet health --------------------------------------------------------------


def test_health_is_never_assumed(bakers):
    c, admin, bay, clock = bakers
    enrolled = enrol(c, token(c, admin, bay["site_id"])).json()
    node = node_headers(enrolled)

    def health():
        return c.get("/api/v1/edge/nodes", headers=admin).json()[0]

    assert health()["health"] == "never_seen"
    cam = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()[0]
    beat = {
        "version": "0.4.0",
        "uptime_s": 60,
        "spool_pending": 17,
        "cameras": [{"api_camera_id": cam["id"], "connected": False}],
    }
    c.post("/api/v1/edge/heartbeat", json=beat, headers=node)
    h = health()
    assert h["health"] == "online" and h["spool_pending"] == 17 and h["version"] == "0.4.0"
    assert h["cameras"] == [
        {
            "api_camera_id": cam["id"],
            "name": cam["name"],
            "connected": False,
            "fps": None,
            "lag_s": None,
        }
    ]
    clock.at += timedelta(seconds=120)
    assert health()["health"] == "stale"
    clock.at += timedelta(minutes=10)
    assert health()["health"] == "offline"


# --- configuration pulled from the API ----------------------------------------------


def test_config_round_trip_and_version(bakers):
    c, admin, bay, _ = bakers
    enrolled = enrol(c, token(c, admin, bay["site_id"], bay_id=bay["id"])).json()
    node = node_headers(enrolled)
    assert c.get("/api/v1/edge/config", headers=node).json()["configured"] is False

    cams = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
    choke = next(x for x in cams if x["role"] == "chokepoint")
    lpr = next(x for x in cams if x["role"] == "lpr")
    body = {
        "model": {"path": "/models/stacks-v2.onnx"},
        "layers_model": "/models/layers-v3.onnx",
        "cameras": [{"api_camera_id": choke["id"], "zone": [0, 0, 768, 1440], "stride": 2}],
        "lpr_cameras": [{"api_camera_id": lpr["id"], "stride": 10}],
    }
    put = c.put(f"/api/v1/edge/nodes/{enrolled['node_id']}/config", json=body, headers=admin)
    assert put.status_code == 200, put.text
    version = put.json()["config_version"]

    got = c.get("/api/v1/edge/config", headers=node).json()
    assert got["configured"] and got["config_version"] == version
    assert got["bay_id"] == bay["id"]
    assert got["cameras"][0]["uri"] == f"rtsp://localhost:8554/{choke['stream_path']}"
    assert got["cameras"][0]["api_camera_id"] == choke["id"] and got["cameras"][0]["key"]
    assert got["model"] == {"path": "/models/stacks-v2.onnx", "arch": "rtdetr"}

    # the node reports what it runs; the fleet view says whether that is current
    c.post("/api/v1/edge/heartbeat", json={"config_version": version}, headers=node)
    assert c.get("/api/v1/edge/nodes", headers=admin).json()[0]["config_drift"] is False
    body["cameras"][0]["stride"] = 3
    new = c.put(f"/api/v1/edge/nodes/{enrolled['node_id']}/config", json=body, headers=admin)
    assert new.json()["config_version"] != version
    assert new.json()["config_drift"] is True
    beat = c.post("/api/v1/edge/heartbeat", json={"config_version": version}, headers=node)
    assert beat.json()["config_version"] == new.json()["config_version"]  # told to refetch


def test_config_refuses_cameras_from_another_site(bakers):
    c, admin, bay, _ = bakers
    enrolled = enrol(c, token(c, admin, bay["site_id"])).json()
    body = {
        "model": {"path": "/m.onnx"},
        "cameras": [{"api_camera_id": str(CAM_Y.id), "zone": [0, 0, 1, 1]}],
    }
    r = c.put(f"/api/v1/edge/nodes/{enrolled['node_id']}/config", json=body, headers=admin)
    assert r.status_code == 422 and "site" in r.json()["detail"]


def test_enrolment_is_audited(bakers):
    c, admin, bay, _ = bakers
    enrolled = enrol(c, token(c, admin, bay["site_id"])).json()
    c.delete(f"/api/v1/edge/nodes/{enrolled['node_id']}", headers=admin)
    actions = {e["action"] for e in c.get("/api/v1/audit", headers=admin).json()}
    assert {"edge_token_created", "node_enrolled", "node_revoked"} <= actions
