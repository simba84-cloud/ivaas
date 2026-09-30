"""T1.1: cross-tenant isolation (proposal M1).

Tenant B, holding every tenant role there is, calls every endpoint with tenant A's
ids. Each must answer 404 — not 403, which would confirm the thing exists — and
nothing tenant A owns may appear in any response B can read.

The routes are enumerated from the app, not listed by hand. A new route that
takes an id and is not classified below fails `test_every_route_is_classified`,
so isolation cannot be forgotten for it.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from conftest import login, make_client
from fastapi.routing import APIRoute

from ivaas.domain.analysis import AnalysisJob
from ivaas.domain.manifests import ExceptionKind, ManifestException
from ivaas.domain.rbac import Role, RoleBinding
from ivaas.domain.security import EnrolledPerson, Incident, IncidentKind
from ivaas.domain.tenancy import BAKERS_INN_ID, ISOLATION_TEST_ID, ScopeType
from ivaas.domain.users import User
from ivaas.ports.assistant import ChatMessage, ToolCall
from ivaas.tenancy import system_context, tenant_context

A_SERVICE = {"X-IVaaS-Key": "dev-pipeline-key"}
B_SERVICE = {"X-IVaaS-Key": "b-pipeline-key"}
B_PASSWORD = "b-holds-every-role-2026"
META = '{"labels": ["stack"], "input_width": 640, "input_height": 640}'

#: Every route that names a resource in its path, and the body a real caller would send.
#: None: the route takes no body. "service": called by the pipeline, with B's key.
BODIES: dict[tuple[str, str], object] = {
    ("GET", "/api/v1/sites/{site_id}/bays"): None,
    ("POST", "/api/v1/sites/{site_id}/bays"): {"name": "B's bay"},
    ("GET", "/api/v1/bays/{bay_id}/cameras"): None,
    ("POST", "/api/v1/bays/{bay_id}/cameras"): {
        "name": "x",
        "role": "overhead",
        "source_url": "rtsp://10.0.0.9/b",
    },
    ("DELETE", "/api/v1/cameras/{camera_id}"): None,
    ("POST", "/api/v1/cameras/{camera_id}/heartbeat"): "service",
    ("POST", "/api/v1/sessions/{session_id}/close"): None,
    ("POST", "/api/v1/sessions/{session_id}/reconcile"): {"manual_count": 1},
    ("POST", "/api/v1/sessions/{session_id}/approve"): {"reason": "other", "note": "x"},
    ("PUT", "/api/v1/users/{username}/roles"): {"roles": ["auditor"]},
    ("PUT", "/api/v1/users/{username}/bindings"): {"bindings": [{"role": "auditor"}]},
    ("PUT", "/api/v1/users/{username}/enabled"): {"enabled": False},
    ("POST", "/api/v1/users/{username}/reset-password"): None,
    ("GET", "/api/v1/analysis/{job_id}"): None,
    ("GET", "/api/v1/objects/{key:path}"): None,
    ("GET", "/api/v1/bays/{bay_id}/zones"): None,
    ("POST", "/api/v1/cameras/{camera_id}/zones"): {
        "name": "z",
        "polygon": [[0, 0], [1, 0], [1, 1]],
    },
    ("PUT", "/api/v1/zones/{zone_id}"): {"name": "z", "polygon": [[0, 0], [1, 0], [1, 1]]},
    ("DELETE", "/api/v1/zones/{zone_id}"): None,
    ("GET", "/api/v1/cameras/{camera_id}/snapshot"): None,
    ("POST", "/api/v1/incidents/{incident_id}/acknowledge"): None,
    ("POST", "/api/v1/incidents/{incident_id}/resolve"): {"note": "x"},
    ("DELETE", "/api/v1/people/{person_id}"): None,
    ("POST", "/api/v1/sites/{site_id}/enrollment-tokens"): {"name": "B's node"},
    ("DELETE", "/api/v1/edge/nodes/{node_id}"): None,
    ("PUT", "/api/v1/edge/nodes/{node_id}/config"): {"model": {"path": "/models/x.onnx"}},
    ("POST", "/api/v1/edge/nodes/{node_id}/rollback"): None,
    ("GET", "/api/v1/sessions/{session_id}/evidence"): None,
    ("POST", "/api/v1/sessions/{session_id}/vehicle"): {"plate": "B 999 ZZ"},
    ("POST", "/api/v1/sessions/{session_id}/override"): {"count": 1, "reason": "double_counted"},
    ("PUT", "/api/v1/fleet/{vehicle_id}"): {"plate": "B 999 ZZ"},
    ("POST", "/api/v1/exceptions/{exception_id}/resolve"): {"note": "looked"},
    ("GET", "/api/v1/edge/models/{model_id}/file"): "node",
}

#: Path parameters that are not a tenant's resource, and why.
NOT_TENANT_RESOURCES = {
    # a setting name, not an id: each tenant writes its own (see the settings test)
    ("PUT", "/api/v1/settings/{key}"),
    # platform records, reached only with platform or partner roles (test_provisioning)
    ("GET", "/api/v1/platform/tenants/{tenant_id}"),
}


def _routes_with_ids(app) -> set[tuple[str, str]]:
    found = set()
    for r in app.routes:
        if isinstance(r, APIRoute) and "{" in r.path:
            for method in r.methods - {"HEAD", "OPTIONS"}:
                found.add((method, r.path))
    return found


@pytest.fixture
def world():
    """Tenant A (Bakers Inn) with one of everything; tenant B with a user holding all roles."""
    keys = {"dev-pipeline-key": "pipeline", "b-pipeline-key": "pipeline@isolation-test"}
    with make_client(service_api_keys=keys) as c:
        container = c.app.state.container
        a = login(c, "admin")
        bay = c.get("/api/v1/bays", headers=a).json()[0]
        camera = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=a).json()[0]
        session = c.post(
            "/api/v1/sessions", json={"bay_id": bay["id"], "direction": "loading"}, headers=a
        ).json()
        zone = c.post(
            f"/api/v1/cameras/{camera['id']}/zones",
            json={"name": "Dock", "polygon": [[0, 0], [1, 0], [1, 1]], "rules": ["intrusion"]},
            headers=a,
        ).json()
        now = datetime.now(UTC)
        incident = Incident(UUID(bay["id"]), UUID(camera["id"]), IncidentKind.INTRUSION, now, 0.9)
        person = EnrolledPerson("Ann", "E1", "consent-1", "admin", now, (0.1,) * 128)
        job = AnalysisJob(UUID(bay["id"]), "a.mp4", "k", "admin", now)
        exception = ManifestException(ExceptionKind.NOT_SEEN, now.date(), now, plate="AAA 111")

        async def seed_a():
            with tenant_context(BAKERS_INN_ID):
                await container.incidents.save(incident)
                await container.people.save(person)
                await container.jobs.save(job)
                await container.exceptions.save(exception)
                key = f"tenants/{BAKERS_INN_ID}/uploads/{job.id}/a.mp4"
                await container.objects.put(key, b"secret video", "video/mp4")
            with system_context():
                await container.users.save(
                    User(
                        username="b-all",
                        display_name="B, everything",
                        password_hash=container.hasher.hash(B_PASSWORD),
                        tenant_id=ISOLATION_TEST_ID,
                        bindings=[
                            RoleBinding(r, ScopeType.TENANT, ISOLATION_TEST_ID)
                            for r in (
                                Role.TENANT_OWNER,
                                Role.TENANT_ADMIN,
                                Role.SITE_MANAGER,
                                Role.BAY_OPERATOR,
                                Role.AUDITOR,
                            )
                        ],
                        created_at=now,
                        password_changed_at=now,
                    )
                )
            return key

        object_key = c.portal.call(seed_a)
        made = c.post(
            f"/api/v1/sites/{bay['site_id']}/enrollment-tokens", json={"name": "A node"}, headers=a
        ).json()
        node = c.post("/api/v1/edge/enroll", json={"token": made["token"]}).json()
        vehicle = c.post("/api/v1/fleet", json={"plate": "AAA 111"}, headers=a).json()
        model = c.post(
            "/api/v1/models",
            data={"name": "stacks", "version": "a1", "meta": META},
            files={"file": ("m.onnx", b"\x08\x07fake-onnx")},
            headers=a,
        ).json()
        ids = {
            "site_id": bay["site_id"],
            "bay_id": bay["id"],
            "camera_id": camera["id"],
            "session_id": session["id"],
            "zone_id": zone["id"],
            "incident_id": str(incident.id),
            "person_id": str(person.id),
            "job_id": str(job.id),
            "username": "operator",
            "key:path": object_key,
            "node_id": node["node_id"],
            "model_id": model["id"],
            "vehicle_id": vehicle["id"],
            "exception_id": str(exception.id),
        }
        b = login(c, "b-all", B_PASSWORD)
        b_site = c.get("/api/v1/sites", headers=b).json()[0]["id"]
        b_tok = c.post(
            f"/api/v1/sites/{b_site}/enrollment-tokens", json={"name": "B node"}, headers=b
        ).json()["token"]
        b_node = c.post("/api/v1/edge/enroll", json={"token": b_tok}).json()["credential"]
        c.b_node = {"X-IVaaS-Node": b_node}  # tenant B's enrolled node, for node-only routes
        yield c, a, b, ids


def test_every_route_is_classified():
    with make_client() as c:
        routes = _routes_with_ids(c.app)
    unclassified = routes - set(BODIES) - NOT_TENANT_RESOURCES
    assert not unclassified, f"classify these for the isolation suite: {sorted(unclassified)}"


def _call(c, method, path, body, b, ids):
    url = path
    used = ""
    for name, value in ids.items():
        if "{" + name + "}" in url:
            url, used = url.replace("{" + name + "}", value), value
    assert "{" not in url, f"no id for {path}"
    callers = {"service": B_SERVICE, "node": getattr(c, "b_node", None)}
    headers = callers[body] if isinstance(body, str) else b
    payload = None if body in (None, "service", "node") else body
    r = c.request(method, url, json=payload, headers=headers)
    return r.status_code, r.text.replace(used, "<id>")


def test_tenant_b_gets_404_for_every_one_of_tenant_as_ids(world):
    """404, and word for word what an id that exists nowhere gets: B learns nothing."""
    c, _, b, ids = world
    nowhere = {k: str(uuid4()) for k in ids} | {
        "username": "nobody-at-all",
        "key:path": f"tenants/{BAKERS_INN_ID}/uploads/{uuid4()}/a.mp4",
    }
    for (method, path), body in BODIES.items():
        status, text = _call(c, method, path, body, b, ids)
        assert status == 404, (method, path, status, text)
        assert (status, text) == _call(c, method, path, body, b, nowhere), (method, path)


def test_nothing_of_tenant_as_appears_in_anything_b_can_list(world):
    c, _, b, ids = world
    secrets = [v for k, v in ids.items() if k != "username"]
    for path in (
        "/api/v1/sites",
        "/api/v1/bays",
        "/api/v1/sessions",
        "/api/v1/summary",
        "/api/v1/analytics/overview",
        "/api/v1/users",
        "/api/v1/audit",
        "/api/v1/analysis",
        "/api/v1/alerts/acknowledgements",
        "/api/v1/incidents",
        "/api/v1/badges",
        "/api/v1/people",
        "/api/v1/security/status",
        "/api/v1/tally/sheets",
        "/api/v1/tally/report",
        "/api/v1/settings",
    ):
        r = c.get(path, headers=b)
        assert r.status_code == 200, (path, r.text)
        for value in secrets:
            assert value not in r.text, (path, "shows tenant A's", value)
    users = {u["username"] for u in c.get("/api/v1/users", headers=b).json()}
    assert users.isdisjoint({"admin", "operator", "viewer"})
    assert c.get("/api/v1/summary", headers=b).json()["cameras_total"] == 0


def test_writes_that_name_tenant_as_ids_in_the_body_change_nothing(world):
    c, a, b, ids = world
    before = c.get("/api/v1/sessions", headers=a).json()

    r = c.post(
        "/api/v1/sessions", json={"bay_id": ids["bay_id"], "direction": "loading"}, headers=b
    )
    assert r.status_code == 404
    r = c.post(
        f"/api/v1/analysis?bay_id={ids['bay_id']}", files={"file": ("v.mp4", b"x")}, headers=b
    )
    assert r.status_code == 404
    sheet = {
        "sheet_id": "B-0001",
        "bay_id": ids["bay_id"],
        "date": date.today().isoformat(),
        "plate": "ABC 1234",
        "direction": "LOAD",
        "lines": [{"line_no": 1, "crates": 10}],
    }
    r = c.post("/api/v1/tally/sheets", json=sheet, headers=b)
    assert r.status_code in (404, 422), r.text
    # B's pipeline reporting crates at A's bay must not reach A's open session
    crossing = {
        "bay_id": ids["bay_id"],
        "camera_id": ids["camera_id"],
        "track_id": 1,
        "direction": "loading",
        "crates": 50,
        "confidence": 0.9,
        "crossed_at": datetime.now(UTC).isoformat(),
    }
    c.post("/api/v1/ingest/crossings", json=crossing, headers=B_SERVICE)
    plate = {
        "bay_id": ids["bay_id"],
        "camera_id": ids["camera_id"],
        "plate": "EVIL 1",
        "confidence": 0.99,
        "read_at": datetime.now(UTC).isoformat(),
    }
    c.post("/api/v1/ingest/plates", json=plate, headers=B_SERVICE)

    after = c.get("/api/v1/sessions", headers=a).json()
    assert after == before, "tenant B changed tenant A's sessions"


def test_each_tenant_keeps_its_own_settings(world):
    c, a, b, _ = world
    r = c.put("/api/v1/settings/reconcile_tolerance", json={"value": 0.5}, headers=b)
    assert r.status_code == 200, r.text
    mine = {s["key"]: s for s in c.get("/api/v1/settings", headers=a).json()["editable"]}
    assert mine["reconcile_tolerance"]["overridden"] is False


def test_live_events_reach_only_their_own_tenant(world):
    c, a, b, ids = world
    token = b["Authorization"].split()[1]
    with c.websocket_connect(f"/ws/events?token={token}") as ws:
        # A's activity, then B's: B's socket must hear only its own
        c.post(f"/api/v1/sessions/{ids['session_id']}/close", headers=a)
        b_bay = c.get("/api/v1/bays", headers=b).json()[0]["id"]
        c.post("/api/v1/sessions", json={"bay_id": b_bay, "direction": "loading"}, headers=b)
        message = ws.receive_json()
        assert message["subject"] == "ivaas.session.opened"
        assert message["data"]["bay_id"] == b_bay


def test_a_signed_in_user_cannot_read_another_tenants_objects_by_key(world):
    c, a, b, ids = world
    url = f"/api/v1/objects/{ids['key:path']}"
    assert c.get(url, headers=a).content == b"secret video"
    assert c.get(url, headers=b).status_code == 404


def test_an_unknown_tenant_in_a_service_key_is_refused():
    with make_client(service_api_keys={"k": "pipeline@no-such-tenant"}) as c:
        assert c.get("/api/v1/bays", headers={"X-IVaaS-Key": "k"}).status_code == 401


def test_the_tenant_cannot_be_chosen_by_the_client(world):
    c, a, _, _ = world
    # headers and query parameters naming another tenant are ignored, not honoured
    sneaky = {**a, "X-Tenant-Id": str(ISOLATION_TEST_ID), "X-IVaaS-Tenant": "isolation-test"}
    names = {s["name"] for s in c.get(f"/api/v1/sites?tenant={uuid4()}", headers=sneaky).json()}
    assert names == {"Bakery Industrial Site"}


class _Scripted:
    """A model that calls the tools it is told to, then says it is done. What it was
    shown is everything the assistant would let a real model read."""

    def __init__(self, calls):
        self.calls, self.seen = list(calls), []

    async def complete(self, messages, tools):
        self.seen.extend(m.content for m in messages if m.role == "tool")
        if self.calls:  # all in one turn, as a model may; the loop allows few turns
            calls = tuple(ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(self.calls))
            self.calls = []
            return ChatMessage("assistant", tool_calls=calls)
        return ChatMessage("assistant", "done")


def test_tenant_bs_assistant_learns_nothing_of_tenant_as(world):
    """T6.5: B asks about A's trucks, A's site and A's day, and gets none of it."""
    c, a, b, ids = world
    lpr = next(
        x["id"]
        for x in c.get(f"/api/v1/bays/{ids['bay_id']}/cameras", headers=a).json()
        if x["role"] == "lpr"
    )
    read = {"bay_id": ids["bay_id"], "camera_id": lpr, "plate": "AAA 111", "confidence": 0.9}
    read["read_at"] = datetime.now(UTC).isoformat()
    assert c.post("/api/v1/ingest/plates", json=read, headers=A_SERVICE).status_code < 300
    a_site = c.get("/api/v1/sites", headers=a).json()[0]["name"]
    a_names = [a_site, "AAA 111", "AAA111"] + [
        x["name"] for x in c.get(f"/api/v1/bays/{ids['bay_id']}/cameras", headers=a).json()
    ]
    today = date.today().isoformat()
    model = _Scripted(
        [
            ("list_sessions", {"plate": "AAA 111", "days": 90}),
            ("list_sessions", {"days": 90}),
            ("daily_report", {"site": a_site, "day": today}),
            ("daily_report", {"day": today}),
            ("balances", {"by": "truck", "days": 90}),
            ("balances", {"by": "day", "days": 90}),
            ("balances", {"by": "route", "days": 90}),
            ("accuracy_report", {"days": 90}),
            ("camera_health", {}),
        ]
    )
    c.app.state.container.chat_model = model
    r = c.post(
        "/api/v1/assistant/chat",
        json={"messages": [{"role": "user", "content": "Tell me about truck AAA 111."}]},
        headers=b,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["tools_used"]) == 9 and len(model.seen) == 9
    seen = "\n".join(model.seen)
    for value in a_names + [v for k, v in ids.items() if k != "username"]:
        assert value not in seen, ("the assistant showed B tenant A's", value)
    # A's site is not "someone else's": to B it does not exist, like a made-up one
    named = json.loads(model.seen[2])
    assert named["error"] == "no site by that name" and a_site not in named["sites"]
    # and A, asking the same, does see its own truck: the test would notice a blind tool
    model = _Scripted([("list_sessions", {"plate": "AAA 111", "days": 90})])
    c.app.state.container.chat_model = model
    c.post(
        "/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "?"}]}, headers=a
    )
    assert "AAA 111" in model.seen[0]


def test_a_role_held_at_one_site_cannot_ask_the_assistant_about_the_tenant(world):
    """Totals across a tenant include sites a site-bound role may not see, so the
    assistant needs its permission across the whole tenant (safe by default)."""
    c, a, _, ids = world
    container = c.app.state.container
    now = datetime.now(UTC)

    async def seed():
        with system_context():
            await container.users.save(
                User(
                    username="a-site-manager",
                    display_name="A, one site",
                    password_hash=container.hasher.hash(B_PASSWORD),
                    tenant_id=BAKERS_INN_ID,
                    bindings=[RoleBinding(Role.SITE_MANAGER, ScopeType.SITE, UUID(ids["site_id"]))],
                    created_at=now,
                    password_changed_at=now,
                )
            )

    c.portal.call(seed)
    container.chat_model = _Scripted([])
    one_site = login(c, "a-site-manager", B_PASSWORD)
    r = c.post(
        "/api/v1/assistant/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers=one_site,
    )
    assert r.status_code == 403
