"""Evidence clips (proposal M2, M4, M5): filed with the load, kept 90 days, then gone."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import login, make_client

from ivaas.adapters.http.evidence_routes import sweep_expired
from ivaas.domain.tenancy import BAKERS_INN_ID
from ivaas.tenancy import tenant_context

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 2048


class Clock:
    def __init__(self) -> None:
        self.at = datetime.now(UTC)

    def now(self) -> datetime:
        return self.at


@pytest.fixture
def rig():
    """Bakers Inn with an enrolled node, a load at the bay, and a controllable clock."""
    with make_client() as c:
        container = c.app.state.container
        container.clock = clock = Clock()
        admin = login(c, "admin")
        bay = c.get("/api/v1/bays", headers=admin).json()[0]
        cam = next(
            x
            for x in c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
            if x["role"] == "chokepoint"
        )
        tok = c.post(
            f"/api/v1/sites/{bay['site_id']}/enrollment-tokens",
            json={"name": "Edge", "bay_id": bay["id"]},
            headers=admin,
        ).json()["token"]
        node = {
            "X-IVaaS-Node": c.post("/api/v1/edge/enroll", json={"token": tok}).json()["credential"]
        }
        session = c.post(
            "/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}, headers=admin
        ).json()
        yield c, admin, node, bay, cam, session, clock


def clip(c, node, bay, cam, *, at=None, seconds=8, data=MP4, event_id=None, kind="crossing"):
    start = at or datetime.now(UTC)
    form = {
        "bay_id": bay["id"],
        "camera_id": cam["id"],
        "kind": kind,
        "started_at": start.isoformat(),
        "ended_at": (start + timedelta(seconds=seconds)).isoformat(),
    }
    if event_id:
        form["event_id"] = str(event_id)
    return c.post(
        "/api/v1/ingest/evidence", data=form, files={"file": ("clip.mp4", data)}, headers=node
    )


def test_a_clip_is_filed_with_the_load_it_shows(rig):
    c, admin, node, bay, cam, session, _ = rig
    r = clip(c, node, bay, cam)
    assert r.status_code == 201, r.text
    assert r.json()["session_id"] == session["id"] and r.json()["stored"] is True

    [shown] = c.get(f"/api/v1/sessions/{session['id']}/evidence", headers=admin).json()
    assert shown["kind"] == "crossing" and shown["seconds"] == 8
    assert shown["sha256"] == hashlib.sha256(MP4).hexdigest()
    assert c.get(shown["url"]).content == MP4  # the signed link plays without a token
    # an operator reviewing a dispute sees it too
    assert (
        len(
            c.get(f"/api/v1/sessions/{session['id']}/evidence", headers=login(c, "operator")).json()
        )
        == 1
    )


def test_it_is_kept_for_the_tenants_retention_period(rig):
    c, admin, node, bay, cam, session, clock = rig
    clip(c, node, bay, cam)
    [shown] = c.get(f"/api/v1/sessions/{session['id']}/evidence", headers=admin).json()
    kept = datetime.fromisoformat(shown["expires_at"]) - clock.at
    assert timedelta(days=89, hours=23) < kept <= timedelta(days=90)


def test_expired_clips_are_deleted_video_first(rig):
    c, admin, node, bay, cam, session, clock = rig
    r = c.put("/api/v1/settings/evidence_retention_days", json={"value": 7}, headers=admin)
    assert r.status_code == 200, r.text
    clip(c, node, bay, cam)
    [shown] = c.get(f"/api/v1/sessions/{session['id']}/evidence", headers=admin).json()
    container = c.app.state.container
    clock.at += timedelta(days=6)
    with tenant_context(BAKERS_INN_ID):
        assert c.portal.call(sweep_expired, container) == 0  # not yet
    clock.at += timedelta(days=2)
    with tenant_context(BAKERS_INN_ID):
        assert c.portal.call(sweep_expired, container) == 1
    assert c.get(f"/api/v1/sessions/{session['id']}/evidence", headers=admin).json() == []
    assert c.get(shown["url"]).status_code == 404  # the video itself is gone


def test_a_clip_outside_any_load_is_still_kept(rig):
    c, admin, node, bay, cam, session, _ = rig
    c.post(f"/api/v1/sessions/{session['id']}/close", headers=admin)
    later = datetime.now(UTC) + timedelta(hours=2)
    r = clip(c, node, bay, cam, at=later)
    assert r.status_code == 201 and r.json()["session_id"] is None and r.json()["stored"]


def test_a_retried_upload_is_stored_once(rig):
    c, admin, node, bay, cam, session, _ = rig
    event = uuid4()
    assert clip(c, node, bay, cam, event_id=event).json()["stored"] is True
    assert clip(c, node, bay, cam, event_id=event).json()["stored"] is False
    assert len(c.get(f"/api/v1/sessions/{session['id']}/evidence", headers=admin).json()) == 1


def test_uploads_are_checked(rig):
    c, _, node, bay, cam, _, _ = rig
    assert clip(c, node, bay, cam, data=b"GIF89a not a video").status_code == 422
    assert clip(c, node, bay, cam, seconds=600).status_code == 422
    assert clip(c, node, bay, cam, seconds=0).status_code == 422
    other_cam = {"id": str(uuid4())}
    assert clip(c, node, bay, other_cam).status_code == 404


def test_people_cannot_upload_evidence(rig):
    c, admin, _, bay, cam, _, _ = rig
    assert clip(c, admin, bay, cam).status_code == 403
