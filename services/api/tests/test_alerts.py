"""Alert acknowledgements: a person saying "I have seen this", and nothing more.

An acknowledgement is a record, like an approval: it names who and when, it is
audited, and it changes no count.
"""

from conftest import login

ACK = "/api/v1/alerts/acknowledgements"
BODY = {"key": "cam-c1-offline@never", "title": "Door 1 has no signal"}


def test_acknowledging_records_who_and_is_audited(client):
    r = client.post(ACK, json={**BODY, "note": "Electrician called"})
    assert r.status_code == 200, r.text
    assert r.json()["acknowledged_by"] == "admin"
    assert r.json()["note"] == "Electrician called"

    listed = client.get(ACK).json()
    assert [a["key"] for a in listed] == [BODY["key"]]

    entry = client.get("/api/v1/audit", params={"action": "alert_acknowledged"}).json()[0]
    assert entry["subject"] == "Door 1 has no signal"  # in operator terms, not a key
    assert entry["detail"] == {"key": BODY["key"], "note": "Electrician called"}


def test_the_first_acknowledgement_stands(client, anon):
    client.post(ACK, json=BODY)
    second = anon.post(ACK, json={**BODY, "note": "me too"}, headers=login(anon, "operator"))
    assert second.status_code == 200
    assert second.json()["acknowledged_by"] == "admin"
    assert second.json()["note"] is None

    audited = client.get("/api/v1/audit", params={"action": "alert_acknowledged"}).json()
    assert len(audited) == 1  # agreeing again is not a new event


def test_viewers_can_see_acknowledgements_but_not_make_them(anon):
    viewer = login(anon, "viewer")
    assert anon.get(ACK, headers=viewer).status_code == 200
    assert anon.post(ACK, json=BODY, headers=viewer).status_code == 403
    assert anon.post(ACK, json=BODY, headers=login(anon, "operator")).status_code == 200


def test_a_key_cannot_carry_arbitrary_text(client):
    r = client.post(ACK, json={**BODY, "key": "drop table; <script>"})
    assert r.status_code == 422
    assert client.get(ACK).json() == []


def test_acknowledging_changes_no_count(client):
    bay = client.get("/api/v1/bays").json()[0]
    s = client.post("/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}).json()
    client.post(f"/api/v1/sessions/{s['id']}/close")
    client.post(f"/api/v1/sessions/{s['id']}/reconcile", json={"manual_count": 5})
    before = client.get("/api/v1/sessions").json()

    client.post(ACK, json={"key": f"disputed-{s['id']}", "title": "Load disputed"})

    assert client.get("/api/v1/sessions").json() == before
