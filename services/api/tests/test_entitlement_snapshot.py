"""M7, T7.7: the signed entitlement snapshot an edge node keeps for when the cloud is
unreachable.

The API's side, checked with the pipeline's own verifier (the API depends on the
pipeline package): a configuration served by the API must verify on the node, and
any edit to it must not.
"""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest
from ivaas_pipeline.adapters import entitlement
from test_edge import bakers, enrol, node_headers, token  # noqa: F401


def _configured(c, admin, bay):
    enrolled = enrol(c, token(c, admin, bay["site_id"], bay_id=bay["id"])).json()
    cams = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
    choke = next(x for x in cams if x["role"] == "chokepoint")
    body = {
        "model": {"path": "/models/stacks-v2.onnx"},
        "cameras": [{"api_camera_id": choke["id"], "zone": [0, 0, 768, 1440]}],
    }
    put = c.put(f"/api/v1/edge/nodes/{enrolled['node_id']}/config", json=body, headers=admin)
    assert put.status_code == 200, put.text
    return enrolled


def test_t7_7_a_served_configuration_carries_a_snapshot_the_node_verifies(bakers):  # noqa: F811
    c, admin, bay, clock = bakers
    enrolled = _configured(c, admin, bay)
    key = enrolled["entitlement_public_key"]  # pinned by the node at enrolment
    cfg = c.get("/api/v1/edge/config", headers=node_headers(enrolled)).json()
    snap = entitlement.verify(cfg, enrolled["node_id"], key)
    # issued now; valid 7 days, then 7 days' grace
    assert snap["issued_at"].startswith("2026-10-01T08:00")
    assert snap["valid_until"].startswith("2026-10-08T08:00")
    assert snap["grace_until"].startswith("2026-10-15T08:00")
    assert snap["plan"] is None and snap["limits"] is None  # no plan: nothing limited
    assert entitlement.state(snap, clock.at + timedelta(days=6)) == "valid"
    assert entitlement.state(snap, clock.at + timedelta(days=10)) == "grace"
    assert entitlement.state(snap, clock.at + timedelta(days=15)) == "expired"


def test_an_edited_or_foreign_configuration_does_not_verify(bakers):  # noqa: F811
    c, admin, bay, _ = bakers
    enrolled = _configured(c, admin, bay)
    key, node = enrolled["entitlement_public_key"], enrolled["node_id"]
    cfg = c.get("/api/v1/edge/config", headers=node_headers(enrolled)).json()

    more = copy.deepcopy(cfg)  # a camera beyond what was signed
    more["cameras"].append({**more["cameras"][0], "api_camera_id": "extra"})
    with pytest.raises(entitlement.EntitlementError, match="not the one the snapshot"):
        entitlement.verify(more, node, key)
    longer = copy.deepcopy(cfg)  # a validity stretched by hand
    longer["entitlement"]["snapshot"]["grace_until"] = "2099-01-01T00:00:00+00:00"
    with pytest.raises(entitlement.EntitlementError, match="signature"):
        entitlement.verify(longer, node, key)
    with pytest.raises(entitlement.EntitlementError, match="another node"):
        entitlement.verify(cfg, "some-other-node", key)
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    other = base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    with pytest.raises(entitlement.EntitlementError, match="other than the one enrolled"):
        entitlement.verify(cfg, node, other)


def test_the_snapshot_records_the_plans_limits(bakers):  # noqa: F811
    c, admin, bay, _ = bakers
    from conftest import login

    r = c.put(
        "/api/v1/platform/tenants/" + str(_bakers_id()) + "/subscription",
        json={"plan": "standard", "quantities": {"ivaas-od-count": 20}},
        headers=login(c, "platform"),
    )
    assert r.status_code == 200, r.text
    enrolled = _configured(c, admin, bay)
    cfg = c.get("/api/v1/edge/config", headers=node_headers(enrolled)).json()
    snap = entitlement.verify(cfg, enrolled["node_id"], enrolled["entitlement_public_key"])
    assert snap["plan"] == "standard" and snap["limits"]["od_channels"] == 20


def test_the_heartbeat_carries_the_nodes_entitlement_state(bakers):  # noqa: F811
    c, admin, bay, _ = bakers
    enrolled = _configured(c, admin, bay)
    beat = {"entitlement": {"state": "grace", "grace_until": "2026-10-15T08:00:00+00:00"}}
    assert (
        c.post("/api/v1/edge/heartbeat", json=beat, headers=node_headers(enrolled)).status_code
        == 200
    )


def _bakers_id():
    from ivaas.domain.tenancy import BAKERS_INN_ID

    return BAKERS_INN_ID
