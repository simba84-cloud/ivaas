"""T2.4, the API's half: a replayed edge event is acknowledged, never counted twice.

The node removes an event from its spool only after the API acknowledges it. A crash
in between sends it again after the restart; the event id makes that harmless.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from conftest import SERVICE, login, make_client


@pytest.fixture
def bay():
    with make_client() as c:
        admin = login(c, "admin")
        b = c.get("/api/v1/bays", headers=admin).json()[0]
        cam = c.get(f"/api/v1/bays/{b['id']}/cameras", headers=admin).json()[0]["id"]
        opened = c.post(
            "/api/v1/sessions", json={"bay_id": b["id"], "direction": "loading"}, headers=admin
        )
        yield c, admin, b["id"], cam, opened.json()["id"]


def crossing(bay_id, cam, event_id=None, crates=10):
    body = {
        "bay_id": bay_id,
        "camera_id": cam,
        "track_id": 1,
        "direction": "loading",
        "crates": crates,
        "confidence": 0.9,
        "crossed_at": datetime.now(UTC).isoformat(),
    }
    return body | ({"event_id": str(event_id)} if event_id else {})


def count(c, admin, session):
    return next(s for s in c.get("/api/v1/sessions", headers=admin).json() if s["id"] == session)[
        "ai_count"
    ]


def test_a_replayed_crossing_is_acknowledged_and_not_counted_again(bay):
    c, admin, bay_id, cam, session = bay
    event = uuid4()
    for _ in range(3):  # the original, then two replays after crashes
        r = c.post("/api/v1/ingest/crossings", json=crossing(bay_id, cam, event), headers=SERVICE)
        assert r.status_code == 200  # acknowledged every time, so the node drops it
    assert count(c, admin, session) == 10


def test_distinct_events_all_count(bay):
    c, admin, bay_id, cam, session = bay
    for _ in range(3):
        c.post("/api/v1/ingest/crossings", json=crossing(bay_id, cam, uuid4()), headers=SERVICE)
    assert count(c, admin, session) == 30


def test_events_without_an_id_still_count_as_before(bay):
    """Nodes that predate the ledger send no id; they are applied as they always were."""
    c, admin, bay_id, cam, session = bay
    c.post("/api/v1/ingest/crossings", json=crossing(bay_id, cam), headers=SERVICE)
    c.post("/api/v1/ingest/crossings", json=crossing(bay_id, cam), headers=SERVICE)
    assert count(c, admin, session) == 20


def test_an_event_that_failed_is_not_refused_on_retry(bay, monkeypatch):
    c, admin, bay_id, cam, session = bay
    container = c.app.state.container
    real = type(container).record_crossing

    class Boom(Exception):
        pass

    def failing(self):
        async def fail(*a, **k):
            raise Boom()

        return fail

    event = uuid4()
    monkeypatch.setattr(type(container), "record_crossing", property(failing))
    with pytest.raises(Boom):
        c.post("/api/v1/ingest/crossings", json=crossing(bay_id, cam, event), headers=SERVICE)
    monkeypatch.setattr(type(container), "record_crossing", real)
    r = c.post("/api/v1/ingest/crossings", json=crossing(bay_id, cam, event), headers=SERVICE)
    assert r.status_code == 200
    assert count(c, admin, session) == 10  # applied once, on the retry


def test_a_replayed_plate_is_skipped(bay):
    c, admin, bay_id, cam, session = bay
    event = uuid4()
    plate = {
        "bay_id": bay_id,
        "camera_id": cam,
        "plate": "ABC 1234",
        "confidence": 0.95,
        "read_at": datetime.now(UTC).isoformat(),
        "event_id": str(event),
    }
    assert c.post("/api/v1/ingest/plates", json=plate, headers=SERVICE).status_code == 200
    again = c.post("/api/v1/ingest/plates", json=plate, headers=SERVICE)
    assert again.status_code == 200 and again.json()["plate"] == "ABC 1234"
