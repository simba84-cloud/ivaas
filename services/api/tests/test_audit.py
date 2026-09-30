"""The audit trail: who changed a number, when, and what it said before.

This platform produces a figure that money depends on, and a person can change it.
These tests pin the actions that must leave a trace, and that the trail is not
something an operator can read or an audited action can be lost to.
"""

from datetime import UTC, datetime

from conftest import SERVICE, login

from ivaas.domain.tenancy import BAKERS_INN_ID


def _entries(client, **params):
    r = client.get("/api/v1/audit", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_signing_in_is_recorded(client):
    rows = _entries(client, action="signed_in")  # the fixture signed in as admin
    assert [r["actor"] for r in rows] == ["admin"]
    assert rows[0]["detail"]["roles"] == "site_manager,tenant_admin"


def test_reconciling_records_both_counts_and_the_outcome(client):
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
    client.post(f"/api/v1/sessions/{s['id']}/reconcile", json={"manual_count": 18})

    entry = _entries(client, action="session_reconciled")[0]
    assert entry["actor"] == "admin"
    # the numbers on both sides, so a challenged count can be reconstructed
    assert entry["detail"]["ai_count"] == 20
    assert entry["detail"]["manual_count"] == 18
    assert entry["detail"]["variance"] == 2
    assert entry["detail"]["outcome"] == "disputed"


def test_approving_records_the_reason_given(client):
    bay = client.get("/api/v1/bays").json()[0]
    s = client.post("/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}).json()
    client.post(f"/api/v1/sessions/{s['id']}/close")
    client.post(f"/api/v1/sessions/{s['id']}/reconcile", json={"manual_count": 9})
    client.post(
        f"/api/v1/sessions/{s['id']}/approve",
        json={"reason": "damaged_removed", "note": "9 pulled damaged"},
    )

    entry = _entries(client, action="session_approved")[0]
    assert entry["detail"]["reason"] == "damaged_removed"
    assert entry["detail"]["note"] == "9 pulled damaged"


def test_adding_and_removing_a_camera_is_recorded_by_name(client):
    bay = client.get("/api/v1/bays").json()[0]
    cam = client.post(
        f"/api/v1/bays/{bay['id']}/cameras",
        json={"name": "Gate LPR", "role": "lpr", "source_url": None},
    ).json()
    client.delete(f"/api/v1/cameras/{cam['id']}")

    removed = _entries(client, action="camera_removed")[0]
    added = _entries(client, action="camera_registered")[0]
    # the name, not just an id: the id means nothing once the camera is gone
    assert removed["subject"] == "Gate LPR"
    assert added["subject"] == "Gate LPR"


def test_the_trail_is_newest_first_and_filterable_by_actor(anon):
    admin = login(anon, "admin")
    login(anon, "operator")

    rows = anon.get("/api/v1/audit", headers=admin).json()
    assert [r["at"] for r in rows] == sorted((r["at"] for r in rows), reverse=True)

    mine = anon.get("/api/v1/audit", params={"actor": "operator"}, headers=admin).json()
    assert mine and {r["actor"] for r in mine} == {"operator"}


def test_the_trail_is_for_admins_and_auditors_not_operators(anon):
    """Proposal §4.2: audit.read belongs to owners, admins and auditors. The seeded
    viewer became an auditor when roles were scoped (migration 0012)."""
    r = anon.get("/api/v1/audit", headers=login(anon, "operator"))
    assert r.status_code == 403
    for user in ("admin", "viewer"):
        assert anon.get("/api/v1/audit", headers=login(anon, user)).status_code == 200, user


def test_a_failing_audit_write_does_not_fail_the_action(client, monkeypatch):
    """An approval that worked must not be reported as failed because its audit
    row could not be written. The failure is logged; the action stands."""
    container = client.app.state.container

    async def boom(entry):
        raise RuntimeError("audit store unavailable")

    # the in-memory audit log is one store per tenant; break Bakers Inn's
    monkeypatch.setattr(container.audit.partition(BAKERS_INN_ID), "record", boom)

    bay = client.get("/api/v1/bays").json()[0]
    r = client.post("/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"})
    assert r.status_code == 201, "the session was opened; the audit write must not break it"


def test_settings_expose_the_editable_rules_and_never_a_secret(client):
    body = client.get("/api/v1/settings").json()

    keys = {s["key"] for s in body["editable"]}
    assert keys == {
        "reconcile_tolerance",
        "auto_close_idle_minutes",
        "auto_open_direction",
        "badge_grace_minutes",
        "face_recognition_basis",
        "face_recognition",
        "evidence_retention_days",
        "manifest_tolerance_crates",
    }
    assert all(s["overridden"] is False for s in body["editable"])

    # the payload may say whether a secret is configured, never what it is
    blob = str(body)
    for secret in (
        "ivaas-secret",  # s3 secret key
        "dev-pipeline-key",  # service api key
        "dev-only-change-me-please-32chars",  # local token signing secret
        "dev-only-object-link-secret-change-me",
    ):
        assert secret not in blob, f"{secret} leaked into the settings response"


def test_changing_the_accuracy_target_takes_effect_and_is_audited(client):
    bay = client.get("/api/v1/bays").json()[0]
    cam = client.get(f"/api/v1/bays/{bay['id']}/cameras").json()[0]

    def load_of_nine_against_ten() -> str:
        """AI counts 9, the sheet says 10: exactly 90% accurate."""
        s = client.post(
            "/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}
        ).json()
        client.post(
            "/api/v1/ingest/crossings",
            headers=SERVICE,
            json={
                "bay_id": bay["id"],
                "camera_id": cam["id"],
                "track_id": 1,
                "direction": "loading",
                "crates": 9,
                "confidence": 0.9,
                "crossed_at": datetime.now(UTC).isoformat(),
            },
        )
        client.post(f"/api/v1/sessions/{s['id']}/close")
        return client.post(
            f"/api/v1/sessions/{s['id']}/reconcile", json={"manual_count": 10}
        ).json()["status"]

    assert load_of_nine_against_ten() == "disputed"  # 90% is under the 95% default

    r = client.put("/api/v1/settings/reconcile_tolerance", json={"value": 0.85})
    assert r.status_code == 200, r.text
    assert [s for s in r.json()["editable"] if s["key"] == "reconcile_tolerance"][0][
        "overridden"
    ] is True

    # the new target is in force on the very next request, not after a restart
    assert load_of_nine_against_ten() == "reconciled"

    entry = client.get("/api/v1/audit", params={"action": "setting_changed"}).json()[0]
    assert entry["subject"] == "reconcile_tolerance" and entry["detail"]["value"] == 0.85


def test_a_setting_outside_its_bounds_is_refused(client):
    assert client.put("/api/v1/settings/reconcile_tolerance", json={"value": 2}).status_code == 422
    assert (
        client.put("/api/v1/settings/auto_open_direction", json={"value": "sideways"}).status_code
        == 422
    )
    assert client.put("/api/v1/settings/database_url", json={"value": "x"}).status_code == 422


def test_only_admins_read_or_change_settings(anon):
    for user in ("viewer", "operator"):
        h = login(anon, user)
        assert anon.get("/api/v1/settings", headers=h).status_code == 403
        assert (
            anon.put(
                "/api/v1/settings/reconcile_tolerance", json={"value": 0.9}, headers=h
            ).status_code
            == 403
        )


def test_every_mutation_leaves_an_entry_in_its_own_tenant_only(anon):
    """T1.7: each call that changes a count or the setup records who and what.

    The entries land in the acting tenant's trail; the other tenant's trail stays
    exactly as it was.
    """
    admin, other = login(anon, "admin"), login(anon, "b-admin")
    others_before = anon.get("/api/v1/audit", headers=other).json()
    bay = anon.get("/api/v1/bays", headers=admin).json()[0]["id"]
    camera = anon.get(f"/api/v1/bays/{bay}/cameras", headers=admin).json()[0]["id"]
    opened = anon.post(
        "/api/v1/sessions", json={"bay_id": bay, "direction": "loading"}, headers=admin
    )
    session = opened.json()["id"]

    def post(path, body=None):
        return anon.post(path, json=body, headers=admin)

    def put(path, body):
        return anon.put(path, json=body, headers=admin)

    cam = {"name": "Audit cam", "role": "overhead", "source_url": "rtsp://10.0.0.7/a"}
    zone = {"name": "Audit zone", "polygon": [[0, 0], [1, 0], [1, 1]]}
    steps = [
        ("site_created", lambda: post("/api/v1/sites", {"name": "S"})),
        ("camera_registered", lambda: post(f"/api/v1/bays/{bay}/cameras", cam)),
        ("session_closed", lambda: post(f"/api/v1/sessions/{session}/close")),
        (
            "session_reconciled",
            lambda: post(f"/api/v1/sessions/{session}/reconcile", {"manual_count": 3}),
        ),
        ("user_created", lambda: post("/api/v1/users", {"username": "aud", "roles": ["auditor"]})),
        ("user_roles_changed", lambda: put("/api/v1/users/aud/roles", {"roles": ["bay_operator"]})),
        ("setting_changed", lambda: put("/api/v1/settings/reconcile_tolerance", {"value": 0.9})),
        ("zone_saved", lambda: post(f"/api/v1/cameras/{camera}/zones", zone)),
    ]
    for action, call in steps:
        before = len(anon.get("/api/v1/audit", params={"action": action}, headers=admin).json())
        r = call()
        assert r.status_code < 300, (action, r.text)
        rows = anon.get("/api/v1/audit", params={"action": action}, headers=admin).json()
        assert len(rows) == before + 1, action
        assert rows[0]["actor"] == "admin", action

    changed = anon.get("/api/v1/audit", params={"action": "user_roles_changed"}, headers=admin)
    assert changed.json()[0]["detail"]["before"] == ["auditor"]
    assert changed.json()[0]["detail"]["after"] == ["bay_operator"]
    reconciled = anon.get("/api/v1/audit", params={"action": "session_reconciled"}, headers=admin)
    assert reconciled.json()[0]["detail"]["manual_count"] == 3  # the value it now holds
    assert anon.get("/api/v1/audit", headers=other).json() == others_before
