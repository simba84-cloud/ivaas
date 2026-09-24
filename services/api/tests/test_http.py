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


def _disputed_session(client) -> str:
    """A load counted by the pipeline, closed, then reconciled well outside tolerance."""
    bay = client.get("/api/v1/bays").json()[0]
    s = client.post("/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}).json()
    cam = client.get(f"/api/v1/bays/{bay['id']}/cameras").json()[0]
    client.post(
        "/api/v1/ingest/crossings",
        headers=SERVICE,
        json={
            "bay_id": bay["id"],
            "camera_id": cam["id"],
            "track_id": 1,
            "direction": "loading",
            "crates": 20,
            "confidence": 0.9,
            "crossed_at": datetime.now(UTC).isoformat(),
        },
    )
    client.post(f"/api/v1/sessions/{s['id']}/close")
    done = client.post(f"/api/v1/sessions/{s['id']}/reconcile", json={"manual_count": 10}).json()
    assert done["status"] == "disputed"
    return s["id"]


def test_admin_approves_a_disputed_load_without_changing_the_counts(client):
    session_id = _disputed_session(client)

    r = client.post(
        f"/api/v1/sessions/{session_id}/approve",
        json={"reason": "damaged_removed", "note": "10 crates pulled damaged"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["approval_reason"] == "damaged_removed"
    assert body["approved_by"]
    assert body["approved_at"]
    # the variance is what the system exists to report, so approval must not erase it
    assert (body["ai_count"], body["manual_count"], body["variance"]) == (20, 10, 10)


def test_approving_anything_not_disputed_is_a_conflict(client):
    bay = client.get("/api/v1/bays").json()[0]
    s = client.post("/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}).json()
    r = client.post(f"/api/v1/sessions/{s['id']}/approve", json={"reason": "other"})
    assert r.status_code == 409


def test_operators_cannot_approve_only_admins(anon):
    from conftest import login

    bay = anon.get("/api/v1/bays", headers=login(anon, "viewer")).json()[0]["id"]
    operator = login(anon, "operator")
    s = anon.post(
        "/api/v1/sessions", json={"bay_id": bay, "direction": "loading"}, headers=operator
    ).json()
    r = anon.post(f"/api/v1/sessions/{s['id']}/approve", json={"reason": "other"}, headers=operator)
    assert r.status_code == 403


def test_approved_loads_leave_the_disputed_insight(client):
    session_id = _disputed_session(client)
    before = client.get("/api/v1/analytics/overview").json()
    assert before["disputed_sessions"] == 1

    client.post(f"/api/v1/sessions/{session_id}/approve", json={"reason": "ai_miscount"})

    after = client.get("/api/v1/analytics/overview").json()
    assert (after["disputed_sessions"], after["approved_sessions"]) == (0, 1)
    assert not [i for i in after["insights"] if i["key"] == "disputed"]
