"""Over-the-air models (proposal M2, T2.6): register once, deliver verified, roll back."""

from __future__ import annotations

import hashlib
import json

import pytest
from conftest import login, make_client

META = {"labels": ["stack", "crate"], "input_width": 640, "input_height": 640}
V1 = b"\x08\x07" + b"model-one" * 100
V2 = b"\x08\x07" + b"model-two" * 100


def upload(c, headers, version="v1", data=V1, name="stacks", meta=META):
    return c.post(
        "/api/v1/models",
        data={"name": name, "version": version, "meta": json.dumps(meta)},
        files={"file": ("model.onnx", data)},
        headers=headers,
    )


@pytest.fixture
def rig():
    """Bakers Inn with an enrolled node for its bay and two registered model versions."""
    with make_client() as c:
        admin = login(c, "admin")
        bay = c.get("/api/v1/bays", headers=admin).json()[0]
        tok = c.post(
            f"/api/v1/sites/{bay['site_id']}/enrollment-tokens",
            json={"name": "Edge", "bay_id": bay["id"]},
            headers=admin,
        ).json()["token"]
        node = c.post("/api/v1/edge/enroll", json={"token": tok}).json()
        v1, v2 = upload(c, admin).json(), upload(c, admin, "v2", V2).json()
        yield c, admin, bay, node, v1, v2


def configure(c, admin, node, model_id, layers_id=None):
    body = {"model": {"version_id": model_id}, "cameras": []}
    if layers_id:
        body["layers_model_id"] = layers_id
    r = c.put(f"/api/v1/edge/nodes/{node['node_id']}/config", json=body, headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def node_headers(node):
    return {"X-IVaaS-Node": node["credential"]}


def test_an_upload_is_recorded_with_the_digest_of_what_was_stored(rig):
    c, admin, *_, v1, _ = rig
    assert v1["sha256"] == hashlib.sha256(V1).hexdigest() and v1["size_bytes"] == len(V1)
    assert v1["meta"] == META
    names = [(m["name"], m["version"]) for m in c.get("/api/v1/models", headers=admin).json()]
    assert set(names) == {("stacks", "v1"), ("stacks", "v2")}


def test_a_version_is_immutable_and_uploads_are_checked(rig):
    c, admin, *_ = rig
    assert upload(c, admin, "v1", V2).status_code == 409  # same version, new bytes: refused
    assert upload(c, admin, "v3", b"PK\x03\x04 not onnx").status_code == 422
    assert upload(c, admin, "v3", meta={"labels": []}).status_code == 422
    assert upload(c, admin, "v3", name="Bad Name").status_code == 422
    assert upload(c, login(c, "operator"), "v3").status_code == 403


def test_the_node_is_told_what_to_fetch_and_fetches_exactly_that(rig):
    c, admin, _, node, v1, _ = rig
    configure(c, admin, node, v1["id"])
    cfg = c.get("/api/v1/edge/config", headers=node_headers(node)).json()
    model = cfg["model"]
    assert model["sha256"] == v1["sha256"] and model["meta"] == META
    assert model["arch"] == "rtdetr" and model["version"] == "v1"
    r = c.get(model["url"], headers=node_headers(node))
    assert r.status_code == 200 and r.content == V1
    assert r.headers["x-ivaas-sha256"] == v1["sha256"]


def test_a_layers_model_can_be_delivered_too(rig):
    c, admin, _, node, v1, v2 = rig
    configure(c, admin, node, v1["id"], layers_id=v2["id"])
    cfg = c.get("/api/v1/edge/config", headers=node_headers(node)).json()
    assert cfg["layers_model_ref"]["sha256"] == v2["sha256"]
    assert "layers_model_id" not in cfg


def test_only_nodes_download_models(rig):
    c, admin, _, _, v1, _ = rig
    assert c.get(f"/api/v1/edge/models/{v1['id']}/file", headers=admin).status_code == 403


def test_an_unregistered_version_is_refused_in_a_config(rig):
    c, admin, _, node, *_ = rig
    body = {"model": {"version_id": "00000000-0000-0000-0000-000000000000"}}
    r = c.put(f"/api/v1/edge/nodes/{node['node_id']}/config", json=body, headers=admin)
    assert r.status_code == 422
    both = {"model": {"version_id": node["node_id"], "path": "/x.onnx"}}
    r = c.put(f"/api/v1/edge/nodes/{node['node_id']}/config", json=both, headers=admin)
    assert r.status_code == 422


def test_roll_back_and_forward(rig):
    c, admin, _, node, v1, v2 = rig
    url = f"/api/v1/edge/nodes/{node['node_id']}"
    assert c.post(f"{url}/rollback", headers=admin).status_code == 409  # no history yet
    first = configure(c, admin, node, v1["id"])
    assert first["can_roll_back"] is False  # the first config replaced nothing
    second = configure(c, admin, node, v2["id"])
    assert second["can_roll_back"] is True

    back = c.post(f"{url}/rollback", headers=admin).json()
    assert back["config_version"] == first["config_version"]
    assert back["config"]["model"]["version_id"] == v1["id"]
    forward = c.post(f"{url}/rollback", headers=admin).json()  # a second rollback undoes it
    assert forward["config_version"] == second["config_version"]

    # saving the same config again keeps the history rather than erasing it
    again = configure(c, admin, node, v2["id"])
    assert again["can_roll_back"] is True
    actions = [e["action"] for e in c.get("/api/v1/audit", headers=admin).json()]
    assert actions.count("node_rolled_back") == 2 and "model_uploaded" in actions


def test_the_fleet_view_shows_the_model_running_and_a_refused_switch(rig):
    c, admin, _, node, v1, v2 = rig
    configure(c, admin, node, v2["id"])
    beat = {
        "config_version": "old",
        "models": {"detector": {"name": "stacks", "version": "v1", "sha256": v1["sha256"]}},
        "model_error": "stacks v2: checksum mismatch; kept stacks v1",
    }
    c.post("/api/v1/edge/heartbeat", json=beat, headers=node_headers(node))
    listed = c.get("/api/v1/edge/nodes", headers=admin).json()[0]
    assert listed["models"]["detector"]["version"] == "v1"
    assert "checksum mismatch" in listed["model_error"]
    assert listed["config_drift"] is True


def test_models_are_invisible_to_other_tenants():
    with make_client() as c:
        admin, other = login(c, "admin"), login(c, "b-admin")
        upload(c, admin)
        assert c.get("/api/v1/models", headers=other).json() == []
