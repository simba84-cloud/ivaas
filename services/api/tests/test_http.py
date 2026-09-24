from datetime import UTC, date, datetime

import pytest
from conftest import SERVICE


def test_full_loading_flow(client):
    bay = client.get("/api/v1/bays").json()[0]
    cameras = client.get(f"/api/v1/bays/{bay['id']}/cameras").json()
    assert len(cameras) == 17  # 16 volumetric + 1 LPR, per the POC scope
    chokepoint = next(c for c in cameras if c["role"] == "chokepoint")
    lpr = next(c for c in cameras if c["role"] == "lpr")
    now = datetime.now(UTC).isoformat()

    with client.websocket_connect(f"/ws/events?token={client.headers['Authorization'][7:]}") as ws:
        client.post(
            "/api/v1/ingest/plates",
            headers=SERVICE,
            json={
                "bay_id": bay["id"],
                "camera_id": lpr["id"],
                "plate": "abe 2437",
                "confidence": 0.96,
                "read_at": now,
            },
        )
        opened = ws.receive_json()  # the plate read opened the session by itself
        assert opened["subject"] == "ivaas.session.opened"
        assert ws.receive_json()["data"]["plate"] == "ABE 2437"
    session = client.get("/api/v1/sessions").json()[0]
    assert session["status"] == "open" and session["plate"] == "ABE 2437"

    for track_id in range(48):
        r = client.post(
            "/api/v1/ingest/crossings",
            headers=SERVICE,
            json={
                "bay_id": bay["id"],
                "camera_id": chokepoint["id"],
                "track_id": track_id,
                "direction": "loading",
                "confidence": 0.9,
                "crossed_at": now,
            },
        )
        assert r.status_code == 200

    assert client.post(f"/api/v1/sessions/{session['id']}/close").json()["ai_count"] == 48
    done = client.post(
        f"/api/v1/sessions/{session['id']}/reconcile", json={"manual_count": 50}
    ).json()
    assert done["status"] == "reconciled"
    assert done["variance"] == -2
    assert done["accuracy"] == pytest.approx(0.96)

    summary = client.get("/api/v1/summary").json()
    assert summary["crates_today"] == 48
    assert summary["cameras_total"] == 17


def test_reconcile_open_session_conflicts(client):
    bay = client.get("/api/v1/bays").json()[0]
    s = client.post("/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}).json()
    r = client.post(f"/api/v1/sessions/{s['id']}/reconcile", json={"manual_count": 1})
    assert r.status_code == 409


def test_heartbeat_marks_camera_online(client):
    bay = client.get("/api/v1/bays").json()[0]
    cam = client.get(f"/api/v1/bays/{bay['id']}/cameras").json()[0]
    assert client.post(f"/api/v1/cameras/{cam['id']}/heartbeat", headers=SERVICE).status_code == 204
    assert client.get("/api/v1/summary").json()["cameras_online"] == 1


def test_overview_endpoint_returns_series_deltas_and_insights(client):
    bay = client.get("/api/v1/bays").json()[0]
    s = client.post("/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}).json()
    client.post(f"/api/v1/sessions/{s['id']}/close")
    client.post(f"/api/v1/sessions/{s['id']}/reconcile", json={"manual_count": 0})

    body = client.get("/api/v1/analytics/overview?days=14").json()
    assert body["days"] == 14
    assert len(body["daily"]) == 14
    assert len(body["crates"]["series"]) == 14
    assert body["daily"][-1]["day"] == date.today().isoformat()
    # every camera in the seeded bay is offline, so that insight must be present
    assert any(i["key"] == "cameras_offline" for i in body["insights"])
    assert all(i["severity"] in {"good", "info", "warn", "critical"} for i in body["insights"])
