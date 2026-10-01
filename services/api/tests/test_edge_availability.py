"""POC reliability: heartbeats are kept as availability history, read back as uptime."""

from __future__ import annotations

from datetime import timedelta

from test_edge import bakers, enrol, node_headers, token  # noqa: F401

MIN = timedelta(minutes=1)


def configured_node(c, admin, bay):
    enrolled = enrol(c, token(c, admin, bay["site_id"], bay_id=bay["id"])).json()
    cams = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
    choke = next(x for x in cams if x["role"] == "chokepoint")
    lpr = next(x for x in cams if x["role"] == "lpr")
    body = {
        "model": {"path": "/models/stacks-v2.onnx"},
        "cameras": [{"api_camera_id": choke["id"], "line": [[640, 0], [640, 720]]}],
        "lpr_cameras": [{"api_camera_id": lpr["id"], "stride": 5}],
    }
    r = c.put(f"/api/v1/edge/nodes/{enrolled['node_id']}/config", json=body, headers=admin)
    assert r.status_code == 200, r.text
    return enrolled, choke, lpr


def beat(c, node, clock, minutes, cams, spool=0, every=timedelta(seconds=30)):
    """Heartbeats for `minutes`; `cams(t)` -> [(camera, connected)] at each one."""
    end = clock.at + minutes * MIN
    while clock.at < end:
        report = [{"api_camera_id": cam["id"], "connected": up} for cam, up in cams(clock.at)]
        body = {"spool_pending": spool(clock.at) if callable(spool) else spool, "cameras": report}
        assert c.post("/api/v1/edge/heartbeat", json=body, headers=node).status_code == 200
        clock.at += every


def test_a_day_of_heartbeats_is_uptime_with_its_outage_and_the_backlog_it_drained(bakers):  # noqa: F811
    c, admin, bay, clock = bakers
    enrolled, choke, lpr = configured_node(c, admin, bay)
    node = node_headers(enrolled)
    start = clock.at
    both = lambda t: [(choke, True), (lpr, True)]  # noqa: E731
    beat(c, node, clock, 60, both)
    clock.at += 30 * MIN  # the WAN drops for half an hour: no heartbeats at all
    # back, with what it counted meanwhile queued, then sent
    back = clock.at
    beat(c, node, clock, 30, both, spool=lambda t: 25 if t - back < MIN else 0)
    r = c.get(
        f"/api/v1/edge/nodes/{enrolled['node_id']}/availability",
        params={"start": start.isoformat(), "end": clock.at.isoformat()},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    got = r.json()
    [outage] = got["node"]["outages"]
    assert round(outage["minutes"]) == 30 and outage["backlog"] == 25 and outage["drained"]
    assert 74 < got["node"]["uptime_pct"] < 76  # about 90 of 120 minutes
    # the cameras could not be vouched for while the node was silent: down too
    assert {x["name"] for x in got["cameras"]} == {choke["name"], lpr["name"]}
    assert all(len(x["outages"]) == 1 for x in got["cameras"])


def test_a_camera_the_node_reports_unplugged_is_down_while_the_node_is_up(bakers):  # noqa: F811
    c, admin, bay, clock = bakers
    enrolled, choke, lpr = configured_node(c, admin, bay)
    node = node_headers(enrolled)
    start = clock.at
    unplugged = (start + 20 * MIN, start + 30 * MIN)
    beat(c, node, clock, 60, lambda t: [(choke, not unplugged[0] <= t < unplugged[1]), (lpr, True)])
    got = c.get(
        f"/api/v1/edge/nodes/{enrolled['node_id']}/availability",
        params={"start": start.isoformat(), "end": clock.at.isoformat()},
        headers=admin,
    ).json()
    assert got["node"]["outages"] == [] and got["node"]["uptime_pct"] > 99
    cams = {x["name"]: x for x in got["cameras"]}
    assert len(cams[choke["name"]]["outages"]) == 1 and cams[lpr["name"]]["outages"] == []


def test_a_node_is_credited_only_for_cameras_it_is_configured_with(bakers):  # noqa: F811
    c, admin, bay, clock = bakers
    enrolled, choke, _ = configured_node(c, admin, bay)
    node = node_headers(enrolled)
    other = next(
        x
        for x in c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
        if x["role"] == "overhead"
    )
    beat(c, node, clock, 5, lambda t: [(choke, True), (other, True)])
    got = c.get(f"/api/v1/edge/nodes/{enrolled['node_id']}/availability", headers=admin).json()
    assert [x["api_camera_id"] for x in got["cameras"]] == [choke["id"]]


def test_never_heard_from_is_down_and_windows_are_bounded(bakers):  # noqa: F811
    c, admin, bay, clock = bakers
    enrolled, _, _ = configured_node(c, admin, bay)
    url = f"/api/v1/edge/nodes/{enrolled['node_id']}/availability"
    got = c.get(url, headers=admin).json()
    assert got["node"]["uptime_pct"] == 0 and got["node"]["down_minutes"] == 24 * 60
    end = clock.at
    too_long = {"start": (end - timedelta(days=90)).isoformat(), "end": end.isoformat()}
    assert c.get(url, params=too_long, headers=admin).status_code == 422
