"""Site security: zones, incidents, badges and face enrolment.

The edge pipeline proposes incidents; the API decides whether they stand, and a person
decides what they meant. These tests pin what is refused as much as what is accepted,
because a security feed that cries wolf is switched off within a week.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import SERVICE, login

from ivaas.domain.security import BadgeEvent, SecurityError, Window, Zone, ZoneRule, admitted

JPEG = b"\xff\xd8\xff\xe0" + b"evidence" * 50
NOW = datetime.now(UTC)
SQUARE = [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6], [0.1, 0.6]]


# --- the rules themselves ----------------------------------------------------------


def test_an_overnight_window_runs_past_midnight():
    night = Window((0,), "22:00", "05:00")  # armed from Monday 22:00 to Tuesday 05:00
    monday = datetime(2026, 9, 28)  # a Monday
    assert night.contains(monday.replace(hour=23))
    assert night.contains((monday + timedelta(days=1)).replace(hour=4, minute=59))
    assert not night.contains((monday + timedelta(days=1)).replace(hour=5))
    assert not night.contains(monday.replace(hour=4))  # Monday early: belongs to Sunday's night
    assert not night.contains(monday.replace(hour=12))


def test_zone_rules_that_would_mislead_are_refused():
    cam = uuid4()
    poly = ((0, 0), (1, 0), (1, 1))
    with pytest.raises(SecurityError, match="door"):
        Zone(cam, "Store", poly, frozenset({ZoneRule.BADGE}))
    with pytest.raises(SecurityError, match="ignored area"):
        Zone(cam, "Oven", poly, frozenset({ZoneRule.FIRE}), exclude=True)
    with pytest.raises(SecurityError, match="three points"):
        Zone(cam, "Line", ((0, 0), (1, 1)))
    with pytest.raises(SecurityError, match="between 0 and 1"):
        Zone(cam, "Off frame", ((0, 0), (2, 0), (1, 1)))


def test_only_a_granted_swipe_at_that_door_in_time_admits():
    t = NOW
    swipe = lambda door, mins, ok=True: BadgeEvent("B1", door, t - timedelta(minutes=mins), ok)  # noqa: E731
    grace = timedelta(minutes=10)
    assert admitted([swipe("Store", 5)], "store ", t, grace)  # case and spaces ignored
    assert not admitted([swipe("Store", 5, ok=False)], "Store", t, grace)  # refused swipe
    assert not admitted([swipe("Office", 5)], "Store", t, grace)  # another door
    assert not admitted([swipe("Store", 15)], "Store", t, grace)  # too long ago


# --- over HTTP ---------------------------------------------------------------------


def camera(client) -> str:
    bay = client.get("/api/v1/bays").json()[0]
    return bay["id"], client.get(f"/api/v1/bays/{bay['id']}/cameras").json()[0]["id"]


def make_zone(client, cam, **over) -> dict:
    body = {"name": "Yard", "polygon": SQUARE, "rules": ["intrusion"], **over}
    r = client.post(f"/api/v1/cameras/{cam}/zones", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def report(anon, bay, cam, zone, kind="intrusion", at=NOW, snapshot=True):
    body = {
        "bay_id": bay,
        "camera_id": cam,
        "zone_id": zone,
        "kind": kind,
        "confidence": 0.91,
        "detected_at": at.isoformat(),
        "snapshot_jpeg_b64": base64.b64encode(JPEG).decode() if snapshot else None,
        "detail": {"track_id": 7},
    }
    return anon.post("/api/v1/ingest/incidents", json=body, headers=SERVICE)


def test_an_admin_draws_a_zone_and_everyone_can_see_whether_it_is_armed(client, anon):
    bay, cam = camera(client)
    z = make_zone(
        client, cam, schedule=[{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59"}]
    )
    assert z["armed"] is True
    viewer = login(anon, "viewer")
    assert [x["name"] for x in anon.get(f"/api/v1/bays/{bay}/zones", headers=viewer).json()] == [
        "Yard"
    ]
    assert (
        anon.post(
            f"/api/v1/cameras/{cam}/zones",
            json={"name": "X", "polygon": SQUARE},
            headers=login(anon, "operator"),
        ).status_code
        == 403
    )
    entry = client.get("/api/v1/audit", params={"action": "zone_saved"}).json()[0]
    assert entry["subject"] == "Yard"


def test_an_intrusion_is_stored_with_its_evidence(client, anon):
    bay, cam = camera(client)
    z = make_zone(client, cam)
    r = report(anon, bay, cam, z["id"])
    assert r.status_code == 200, r.text
    incident = r.json()
    assert incident["kind"] == "intrusion" and incident["zone_name"] == "Yard"
    assert incident["status"] == "open"
    # the snapshot link works without a token, as an <img> needs it to
    link = incident["snapshot_url"]
    assert "sig=" in link
    anon.headers.pop("Authorization", None)
    assert anon.get(link).content == JPEG


def test_the_same_event_reported_again_is_one_incident(client, anon):
    bay, cam = camera(client)
    z = make_zone(client, cam)
    first = report(anon, bay, cam, z["id"]).json()
    again = report(anon, bay, cam, z["id"], at=NOW + timedelta(seconds=20)).json()
    later = report(anon, bay, cam, z["id"], at=NOW + timedelta(minutes=5)).json()
    assert again["id"] == first["id"]
    assert later["id"] != first["id"]


def test_a_report_the_zone_does_not_watch_for_is_refused(client, anon):
    bay, cam = camera(client)
    z = make_zone(client, cam, rules=["fire"])
    assert report(anon, bay, cam, z["id"], kind="intrusion").status_code == 422
    oven = make_zone(client, cam, name="Oven", rules=[], exclude=True)
    assert report(anon, bay, cam, oven["id"], kind="fire").status_code == 422
    assert report(anon, bay, cam, z["id"], kind="smoke").status_code == 200


def test_only_the_pipeline_reports_incidents(client, anon):
    bay, cam = camera(client)
    z = make_zone(client, cam)
    body = {
        "bay_id": bay,
        "camera_id": cam,
        "zone_id": z["id"],
        "kind": "intrusion",
        "confidence": 0.9,
        "detected_at": NOW.isoformat(),
    }
    assert client.post("/api/v1/ingest/incidents", json=body).status_code == 403  # an admin token


def test_a_badge_swipe_accounts_for_someone_in_a_badge_zone(client, anon):
    bay, cam = camera(client)
    z = make_zone(client, cam, name="Store", rules=["badge"], badge_door="Store door")
    swipe = {
        "badge_id": "B-17",
        "door": "Store door",
        "at": (NOW - timedelta(minutes=3)).isoformat(),
        "granted": True,
        "holder": "T. Moyo",
    }
    assert anon.post("/api/v1/ingest/badges", json=swipe, headers=SERVICE).status_code == 200
    assert report(anon, bay, cam, z["id"], kind="unbadged").json() is None  # accounted for
    late = report(anon, bay, cam, z["id"], kind="unbadged", at=NOW + timedelta(hours=1))
    assert late.json()["kind"] == "unbadged"
    # the badge log names people, so viewers do not see it
    assert anon.get("/api/v1/badges", headers=login(anon, "viewer")).status_code == 403
    assert client.get("/api/v1/badges").json()[0]["holder"] == "T. Moyo"


def test_incidents_are_acknowledged_then_resolved_with_a_note(client, anon):
    bay, cam = camera(client)
    z = make_zone(client, cam)
    i = report(anon, bay, cam, z["id"]).json()
    assert (
        anon.post(
            f"/api/v1/incidents/{i['id']}/acknowledge", headers=login(anon, "viewer")
        ).status_code
        == 403
    )

    ack = client.post(f"/api/v1/incidents/{i['id']}/acknowledge").json()
    assert ack["status"] == "acknowledged" and ack["acknowledged_by"] == "admin"
    assert client.post(f"/api/v1/incidents/{i['id']}/acknowledge").status_code == 422
    assert (
        client.post(f"/api/v1/incidents/{i['id']}/resolve", json={"note": " "}).status_code == 422
    )
    done = client.post(
        f"/api/v1/incidents/{i['id']}/resolve", json={"note": "Night guard on patrol"}
    ).json()
    assert done["status"] == "resolved" and done["resolution_note"] == "Night guard on patrol"

    assert [
        x["id"] for x in client.get("/api/v1/incidents", params={"status": "resolved"}).json()
    ] == [i["id"]]
    audited = {e["action"] for e in client.get("/api/v1/audit").json()}
    assert {"incident_acknowledged", "incident_resolved"} <= audited


class FakeEncoder:
    def embed(self, image_bytes: bytes):
        if image_bytes == b"no face":
            raise ValueError("no clear face found; use a well-lit, front-on photo")
        return tuple(0.01 * i for i in range(128))


def enrol(client, photo=b"a photo"):
    return client.post(
        "/api/v1/people",
        data={
            "name": "Tendai Moyo",
            "employee_ref": "E-1042",
            "consent_reference": "HR/consent/2026/118",
        },
        files={"photo": ("t.jpg", photo, "image/jpeg")},
    )


def test_face_recognition_stays_off_until_its_legal_basis_is_recorded(client, anon):
    client.app.state.container._face_encoder = FakeEncoder()
    assert enrol(client).status_code == 422  # off by default
    r = client.put("/api/v1/settings/face_recognition", json={"value": "on"})
    assert r.status_code == 422 and "legal basis" in r.json()["detail"]

    client.put(
        "/api/v1/settings/face_recognition_basis",
        json={"value": "DPIA 2026-04, staff consent forms"},
    )
    assert client.put("/api/v1/settings/face_recognition", json={"value": "on"}).status_code == 200
    assert client.get("/api/v1/security/status").json()["face_recognition"] is True


def test_enrolment_keeps_no_photo_and_never_returns_the_embedding(client, anon):
    client.app.state.container._face_encoder = FakeEncoder()
    client.put("/api/v1/settings/face_recognition_basis", json={"value": "DPIA 2026-04"})
    client.put("/api/v1/settings/face_recognition", json={"value": "on"})

    assert enrol(client, photo=b"no face").status_code == 422
    r = enrol(client)
    assert r.status_code == 201, r.text
    person = r.json()
    assert "embedding" not in person
    assert person["consent_reference"] == "HR/consent/2026/118"
    assert "embedding" not in client.get("/api/v1/people").json()[0]
    assert anon.get("/api/v1/people", headers=login(anon, "operator")).status_code == 403

    # the pipeline gets the gallery while recognition is on, and nothing once it is off
    bay, _ = camera(client)
    gallery = anon.get("/api/v1/pipeline/security", params={"bay_id": bay}, headers=SERVICE).json()[
        "gallery"
    ]
    assert gallery[0]["name"] == "Tendai Moyo" and len(gallery[0]["embedding"]) == 128
    client.put("/api/v1/settings/face_recognition", json={"value": "off"})
    assert (
        anon.get("/api/v1/pipeline/security", params={"bay_id": bay}, headers=SERVICE).json()[
            "gallery"
        ]
        == []
    )

    assert client.delete(f"/api/v1/people/{person['id']}").status_code == 204
    actions = [e["action"] for e in client.get("/api/v1/audit").json()]
    assert "person_enrolled" in actions and "person_removed" in actions


def test_an_unknown_face_is_refused_while_recognition_is_off(client, anon):
    bay, cam = camera(client)
    z = make_zone(client, cam, rules=["face"])
    assert report(anon, bay, cam, z["id"], kind="unknown_face").status_code == 422


def test_the_portal_sees_what_the_edge_can_detect(client, anon):
    assert client.get("/api/v1/security/status").json()["edge"] is None  # never checked in
    bay, cam = camera(client)
    make_zone(client, cam)
    body = anon.get(
        "/api/v1/pipeline/security",
        params={"bay_id": bay, "capabilities": "people,fire"},
        headers=SERVICE,
    ).json()
    assert body["zones"][0]["armed"] is True
    edge = client.get("/api/v1/security/status").json()["edge"]
    assert edge["detectors"] == ["fire", "people"]


def test_a_snapshot_of_a_camera_that_is_not_streaming_says_so(client):
    _, cam = camera(client)
    client.app.state.container.settings.media_rtsp_url = "rtsp://127.0.0.1:1"  # nothing there
    r = client.get(f"/api/v1/cameras/{cam}/snapshot")
    assert r.status_code == 404
    assert "not streaming" in r.json()["detail"]
