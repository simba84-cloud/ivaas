"""The edge node's side of M2: enrolment, pulled configuration, heartbeat."""

from __future__ import annotations

import json
import stat

import httpx
import pytest
from prometheus_client import CollectorRegistry

from ivaas_pipeline.adapters import fleet
from ivaas_pipeline.adapters.delivery import SpooledDelivery
from ivaas_pipeline.adapters.prometheus_metrics import PrometheusMetrics

API = "http://api.test"
CREDENTIAL = "ivaas-node-11111111-1111-1111-1111-111111111111.s3cret"


def client(handler) -> httpx.Client:
    return httpx.Client(base_url=API, transport=httpx.MockTransport(handler))


def test_enrolment_keeps_the_credential_readable_by_this_user_only(tmp_path):
    seen = {}

    def api(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            201,
            json={
                "node_id": "11111111-1111-1111-1111-111111111111",
                "credential": CREDENTIAL,
                "name": "Edge 1",
                "site_id": "s",
                "bay_id": None,
            },
        )

    path = tmp_path / "ivaas" / "node.json"
    identity = fleet.enroll(API, "ivaas-enr-x.y", path, hostname="edge-01", client=client(api))
    assert seen == {"token": "ivaas-enr-x.y", "hostname": "edge-01", "version": fleet.VERSION}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert fleet.load_identity(path) == identity
    assert identity.headers == {"X-IVaaS-Node": CREDENTIAL}


def test_a_refused_enrolment_says_why_and_writes_nothing(tmp_path):
    def api(request):
        return httpx.Response(401, json={"detail": "this enrollment token is not valid"})

    path = tmp_path / "node.json"
    with pytest.raises(fleet.EnrollmentError, match="not valid"):
        fleet.enroll(API, "ivaas-enr-x.y", path, client=client(api))
    assert not path.exists()
    assert fleet.load_identity(path) is None


def test_a_node_waits_for_a_configuration_rather_than_counting_nothing():
    answers = iter([{"configured": False}, {"configured": True, "config_version": "v1"}])
    waits = []

    def api(request):
        assert request.url.path == "/api/v1/edge/config"
        return httpx.Response(200, json=next(answers))

    cfg = fleet.fetch_config(client(api), sleep=waits.append)
    assert cfg["config_version"] == "v1" and len(waits) == 1


def test_a_revoked_node_stops_asking():
    with pytest.raises(fleet.EnrollmentError, match="revoked"):
        fleet.fetch_config(client(lambda r: httpx.Response(401)), sleep=lambda s: None)


def test_the_heartbeat_reports_every_configured_camera_and_the_backlog():
    metrics = PrometheusMetrics(CollectorRegistry())
    metrics.connected("choke", True)
    metrics.processed("choke", 0.05, 0.2)
    sent = []

    def api(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"config_version": "v1"})

    beat = fleet.Heartbeat(
        client(api),
        config_version="v1",
        cameras=metrics.snapshot,
        camera_ids={"choke": "cam-1", "lpr": "cam-2"},
        spool_pending=lambda: 42,
        on_new_config=lambda v: pytest.fail("the version did not change"),
    )
    assert beat.beat()
    report = sent[0]
    assert report["config_version"] == "v1" and report["spool_pending"] == 42
    by_id = {c["api_camera_id"]: c for c in report["cameras"]}
    assert by_id["cam-1"]["connected"] is True and by_id["cam-1"]["lag_s"] == 0.2
    # a camera that never produced a frame is reported down, not left out
    assert by_id["cam-2"] == {
        "api_camera_id": "cam-2",
        "connected": False,
        "fps": 0.0,
        "lag_s": None,
    }


def test_a_new_configuration_is_noticed_through_the_heartbeat():
    noticed = []
    beat = fleet.Heartbeat(
        client(lambda r: httpx.Response(200, json={"config_version": "v2"})),
        config_version="v1",
        cameras=dict,
        camera_ids={},
        spool_pending=lambda: 0,
        on_new_config=noticed.append,
    )
    beat.beat()
    assert noticed == ["v2"]


def test_an_unreachable_api_does_not_stop_the_node():
    def down(request):
        raise httpx.ConnectError("no route")

    beat = fleet.Heartbeat(
        client(down),
        config_version="v1",
        cameras=dict,
        camera_ids={},
        spool_pending=lambda: 0,
        on_new_config=lambda v: None,
    )
    assert beat.beat() is False


def test_an_enrolled_node_delivers_events_as_itself(tmp_path):
    headers = []

    def api(request):
        headers.append(dict(request.headers))
        return httpx.Response(200, json=None)

    identity = fleet.NodeIdentity(API, "n", CREDENTIAL, "Edge 1")
    delivery = SpooledDelivery(
        API,
        None,
        tmp_path / "spool",
        client=httpx.Client(
            base_url=API, headers=identity.headers, transport=httpx.MockTransport(api)
        ),
    )
    delivery.send("/api/v1/ingest/crossings", {"x": 1})
    assert headers[0]["x-ivaas-node"] == CREDENTIAL
    assert "x-ivaas-key" not in headers[0]


def test_without_a_client_the_delivery_builds_one_with_the_node_header(tmp_path):
    delivery = SpooledDelivery(API, None, tmp_path / "spool", headers={"X-IVaaS-Node": "c"})
    assert delivery._client.headers["X-IVaaS-Node"] == "c"
    legacy = SpooledDelivery(API, "k", tmp_path / "spool2")
    assert legacy._client.headers["X-IVaaS-Key"] == "k"
