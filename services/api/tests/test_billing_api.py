"""M7 through the API: plans and limits, idempotent usage, and the golden invoices.

The demo bay holds the POC array, 16 counting cameras and 1 plate reader, so a
Standard plan at its defaults is exactly full. Prices are the placeholder book's;
every figure checked here is worked by hand in tests/test_billing.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import SERVICE, login, make_client

from ivaas.domain.tenancy import BAKERS_INN_ID

USERS = {
    "admin": ("admin", "admin"),
    "owner": ("owner", "tenant_owner"),
    "viewer": ("viewer", "viewer"),
    "operator": ("operator", "operator"),
}


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at


@pytest.fixture
def billed():
    with make_client(local_users=USERS) as c:
        c.app.state.container.clock = clock = Clock()
        yield c, clock


def direct_tenant(c, slug="direct-co"):
    """A customer Cassava invoices itself (no partner), with its owner signed in."""
    platform = login(c, "platform")
    made = c.post(
        "/api/v1/platform/tenants",
        json={"slug": slug, "name": "Direct Co", "owner_username": f"{slug}.owner"},
        headers={**platform, "Idempotency-Key": f"provision-{slug}-0001"},
    ).json()
    temporary = made["temporary_password"]
    first = login(c, f"{slug}.owner", temporary)
    token = c.post(
        "/api/v1/auth/password",
        json={"current_password": temporary, "new_password": "a long new password 1"},
        headers=first,
    ).json()["access_token"]
    return made["tenant"]["id"], {"Authorization": f"Bearer {token}"}


def standard(c, tenant=BAKERS_INN_ID, **quantities):
    r = c.put(
        f"/api/v1/platform/tenants/{tenant}/subscription",
        json={"plan": "standard", "quantities": quantities},
        headers=login(c, "platform"),
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_t7_3_a_17th_counting_channel_is_refused_with_an_upgrade_and_the_upgrade_lifts_it(billed):
    c, clock = billed
    admin = login(c, "admin")
    bay = c.get("/api/v1/bays", headers=admin).json()[0]["id"]
    new = {"name": "Dock 17", "role": "overhead", "source_url": "rtsp://10.0.0.17/s"}

    # no plan: nothing is limited (and nothing billed); the 17th goes in
    assert (
        c.get("/api/v1/billing/subscription", headers=login(c, "owner")).json()["subscribed"]
        is False
    )

    sub = standard(c)
    assert sub["entitlements"]["limits"]["od_channels"] == 16
    assert sub["channels_in_use"] == {"od": 16, "lpr": 1}
    r = c.post(f"/api/v1/bays/{bay}/cameras", json=new, headers=admin)
    assert r.status_code == 402 and "16 counting channel" in r.json()["detail"]
    assert "Upgrade" in r.json()["detail"]
    lpr = {**new, "name": "Gate LPR 2", "role": "lpr"}
    assert c.post(f"/api/v1/bays/{bay}/cameras", json=lpr, headers=admin).status_code == 402

    # the owner upgrades, and the same camera is registered
    up = c.put(
        "/api/v1/billing/subscription",
        json={"plan": "standard", "quantities": {"ivaas-od-count": 24}},
        headers=login(c, "owner"),
    )
    assert up.status_code == 200 and up.json()["entitlements"]["limits"]["od_channels"] == 24
    assert c.post(f"/api/v1/bays/{bay}/cameras", json=new, headers=admin).status_code == 201
    actions = [e["action"] for e in c.get("/api/v1/audit", headers=admin).json()]
    assert actions.count("subscription_changed") == 2


def test_t7_3_the_edge_is_served_no_more_channels_than_the_plan_allows(billed):
    from test_edge import enrol, node_headers, token

    c, clock = billed
    admin = login(c, "admin")
    bay = c.get("/api/v1/bays", headers=admin).json()[0]
    cams = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
    counting = [x for x in cams if x["role"] != "lpr"][:6]
    enrolled = enrol(c, token(c, admin, bay["site_id"], bay_id=bay["id"])).json()
    body = {
        "model": {"path": "/models/stacks-v2.onnx"},
        "cameras": [{"api_camera_id": x["id"], "zone": [0, 0, 1280, 720]} for x in counting],
    }
    assert (
        c.put(
            f"/api/v1/edge/nodes/{enrolled['node_id']}/config", json=body, headers=admin
        ).status_code
        == 200
    )
    standard(c, **{"ivaas-od-count": 4})  # a smaller plan than the node is set up for
    served = c.get("/api/v1/edge/config", headers=node_headers(enrolled)).json()
    assert [x["api_camera_id"] for x in served["cameras"]] == [x["id"] for x in counting[:4]]
    assert served["not_entitled"] == [x["id"] for x in counting[4:]]


def test_t7_5_a_thousand_copies_of_one_usage_event_count_once(billed):
    c, clock = billed
    standard(c)
    event = {
        "meter": "assistant_tokens",
        "quantity": "250000",
        "at": "2026-10-15T09:00:00Z",
        "key": "litellm-spend:2026-10-15:bakers-inn",
    }
    first = c.post("/api/v1/billing/usage", json=event, headers=SERVICE)
    assert first.status_code == 201 and first.json() == {"counted": True}
    for _ in range(999):
        again = c.post("/api/v1/billing/usage", json=event, headers=SERVICE)
        assert again.status_code == 200 and again.json() == {"counted": False}
    clock.at = datetime(2026, 10, 20, tzinfo=UTC)
    used = c.get("/api/v1/billing/subscription", headers=login(c, "owner")).json()
    assert used["usage_this_month"] == {"assistant_tokens": "250000"}
    bad = {**event, "meter": "crates"}
    assert c.post("/api/v1/billing/usage", json=bad, headers=SERVICE).status_code == 422


def test_t7_1_and_t7_2_october_upgraded_on_the_11th_is_invoiced_to_the_cent(billed):
    c, clock = billed
    tenant, owner = direct_tenant(c)
    standard(c, tenant)  # 1 Oct: Standard, 16 OD + 1 LPR
    clock.at = datetime(2026, 10, 11, tzinfo=UTC)
    c.put(
        "/api/v1/billing/subscription",
        json={"plan": "standard", "quantities": {"ivaas-od-count": 24}},
        headers=owner,
    )
    # before the month is over it cannot be issued, only drafted
    platform = login(c, "platform")
    url = f"/api/v1/platform/tenants/{tenant}/invoices"
    assert c.post(url, params={"period": "2026-10"}, headers=platform).status_code == 422
    draft = c.get(
        "/api/v1/billing/invoices/draft", params={"period": "2026-10"}, headers=owner
    ).json()
    assert draft["number"] is None and draft["total"] == "1464.95"

    clock.at = datetime(2026, 11, 2, tzinfo=UTC)
    issued = c.post(url, params={"period": "2026-10"}, headers=platform)
    assert issued.status_code == 201, issued.text
    inv = issued.json()
    assert inv["number"].startswith("IVAAS-2026-") and inv["due_date"] == "2026-11-16"
    od = [x["amount"] for x in inv["lines"] if x["sku"] == "ivaas-od-count"]
    assert od == ["232.26", "731.61"]
    assert (inv["subtotal"], inv["tax"], inv["total"]) == ("1273.87", "191.08", "1464.95")
    # made-up prices say so, on the invoice itself
    assert inv["placeholder"] and inv["stamp"] == "PLACEHOLDER PRICES: NOT FOR ISSUE"
    assert c.post(url, params={"period": "2026-10"}, headers=platform).status_code == 409
    [listed] = c.get("/api/v1/billing/invoices", headers=owner).json()
    assert listed["number"] == inv["number"]


def test_who_may_see_and_change_billing(billed):
    c, _ = billed
    standard(c)
    assert c.get("/api/v1/billing/price-book", headers=login(c, "owner")).json()["placeholder"]
    # an auditor (the viewer) reads invoices, as the role matrix has it; changes nothing
    viewer = login(c, "viewer")
    assert c.get("/api/v1/billing/invoices", headers=viewer).status_code == 200
    r = c.put("/api/v1/billing/subscription", json={"plan": "enterprise"}, headers=viewer)
    assert r.status_code == 403
    operator = login(c, "operator")
    assert c.get("/api/v1/billing/invoices", headers=operator).status_code == 403
    # another tenant's admin cannot reach Bakers Inn's billing through the platform routes
    other = login(c, "b-admin")
    r = c.put(
        f"/api/v1/platform/tenants/{BAKERS_INN_ID}/subscription",
        json={"plan": "enterprise"},
        headers=other,
    )
    assert r.status_code == 403
    # a partner admin bills its own tenants only
    litzim = login(c, "litzim")
    assert (
        c.put(
            f"/api/v1/platform/tenants/{BAKERS_INN_ID}/subscription",
            json={"plan": "standard"},
            headers=litzim,
        ).status_code
        == 200
    )
    unknown = "00000000-0000-0000-0000-000000000001"
    r = c.put(
        f"/api/v1/platform/tenants/{unknown}/subscription",
        json={"plan": "standard"},
        headers=litzim,
    )
    assert r.status_code == 404
    bad = c.put(
        f"/api/v1/platform/tenants/{BAKERS_INN_ID}/subscription",
        json={"plan": "gold"},
        headers=login(c, "platform"),
    )
    assert bad.status_code == 422 and "no plan" in bad.json()["detail"]


def test_platform_staff_reach_a_tenants_billing_by_naming_it(billed):
    """Found live: platform staff hold the billing permissions but no tenant, and the
    tenant's own routes answered 500. They are pointed to the routes that name one."""
    c, _ = billed
    standard(c)
    platform = login(c, "platform")
    for path in ("/api/v1/billing/subscription", "/api/v1/billing/invoices"):
        r = c.get(path, headers=platform)
        assert r.status_code == 403 and "/api/v1/platform/tenants/" in r.json()["detail"]
    base = f"/api/v1/platform/tenants/{BAKERS_INN_ID}"
    sub = c.get(f"{base}/subscription", headers=platform).json()
    assert sub["subscribed"] and sub["entitlements"]["plan"] == "standard"
    draft = c.get(f"{base}/invoices/draft", params={"period": "2026-10"}, headers=platform)
    assert draft.status_code == 200 and draft.json()["stamp"]


# --- second slice: lifecycle (T7.6, T7.8), wholesale (T7.9), partner holds (T7.10) ------
def refresh(c, tenant_id):
    """What the sweep does every minute: put the tenant where its billing says."""
    from uuid import UUID

    from ivaas.tenancy import tenant_context

    async def run():
        with tenant_context(UUID(str(tenant_id))):
            return await c.app.state.container.billing().refresh_status()

    return c.portal.call(run)


def status(c, tenant_id):
    return c.get(f"/api/v1/platform/tenants/{tenant_id}", headers=login(c, "platform")).json()[
        "status"
    ]


def test_t7_6_unpaid_goes_past_due_then_suspended_read_only_and_payment_restores(billed):
    c, clock = billed
    tenant, owner = direct_tenant(c)
    standard(c, tenant)
    clock.at = datetime(2026, 11, 2, tzinfo=UTC)
    platform = login(c, "platform")
    inv = c.post(
        f"/api/v1/platform/tenants/{tenant}/invoices",
        params={"period": "2026-10"},
        headers=platform,
    ).json()
    assert inv["due_date"] == "2026-11-16" and not inv["settled"]

    clock.at = datetime(2026, 11, 17, tzinfo=UTC)  # a day late
    assert refresh(c, tenant) == ("active", "past_due")
    clock.at = datetime(2026, 11, 24, tzinfo=UTC)  # eight days late: past the 7 of grace
    assert refresh(c, tenant) == ("past_due", "suspended")
    # suspended: everything can be looked at, nothing changed
    assert c.get("/api/v1/billing/invoices", headers=owner).status_code == 200
    r = c.put("/api/v1/billing/subscription", json={"plan": "standard"}, headers=owner)
    assert r.status_code == 402 and "an invoice is unpaid" in r.json()["detail"]

    pay = f"/api/v1/platform/tenants/{tenant}/invoices/{inv['number']}/payments"
    part = c.post(pay, json={"amount": "500.00", "reference": "BT-77"}, headers=platform).json()
    assert part["paid"] == "500.00" and not part["settled"]
    assert status(c, tenant) == "suspended"  # part-paid is still unpaid
    rest = c.post(pay, json={"amount": "686.80", "reference": "BT-78"}, headers=platform).json()
    assert rest["settled"] and status(c, tenant) == "active"
    assert (
        c.put("/api/v1/billing/subscription", json={"plan": "standard"}, headers=owner).status_code
        == 200
    )
    assert c.post(pay, json={"amount": "1", "reference": "x"}, headers=platform).status_code == 422
    actions = [e["action"] for e in c.get("/api/v1/audit", headers=owner).json()]
    assert actions.count("payment_recorded") == 2
    # the console lists what was issued; a partner sees none of a direct customer's
    listed = c.get(f"/api/v1/platform/tenants/{tenant}/invoices", headers=platform).json()
    assert [(i["number"], i["settled"]) for i in listed] == [(inv["number"], True)]
    theirs = c.get(f"/api/v1/platform/tenants/{tenant}/invoices", headers=login(c, "litzim"))
    assert theirs.status_code == 404


def test_t7_8_a_trial_put_on_a_paid_plan_is_active_with_the_same_tenant(billed):
    c, _ = billed
    tenant, owner = direct_tenant(c)
    c.put(
        f"/api/v1/platform/tenants/{tenant}/subscription",
        json={"plan": "poc-trial"},
        headers=login(c, "platform"),
    )
    assert status(c, tenant) == "trial"
    standard(c, tenant)
    assert status(c, tenant) == "active"
    me = c.get("/api/v1/billing/subscription", headers=owner).json()
    assert [s["plan"] for s in me["segments"]] == ["poc-trial", "standard"]


def litzims_october(c, clock):
    """Bakers Inn on the POC from 5 Oct; Test Depot on Standard all month."""
    from ivaas.domain.tenancy import ISOLATION_TEST_ID, LITZIM_ID

    platform = login(c, "platform")
    clock.at = datetime(2026, 10, 5, tzinfo=UTC)
    c.put(
        f"/api/v1/platform/tenants/{BAKERS_INN_ID}/subscription",
        json={"plan": "poc-trial"},
        headers=platform,
    )
    clock.at = datetime(2026, 10, 1, tzinfo=UTC)
    standard(c, ISOLATION_TEST_ID)
    clock.at = datetime(2026, 11, 2, tzinfo=UTC)
    return LITZIM_ID, ISOLATION_TEST_ID


def test_t7_9_litzims_wholesale_invoice_breaks_down_by_customer_and_bakers_inn_sees_usage(billed):
    c, clock = billed
    litzim_id, test_depot = litzims_october(c, clock)
    platform, litzim = login(c, "platform"), login(c, "litzim")
    base = f"/api/v1/platform/partners/{litzim_id}/invoices"
    # LITZIM sees its own draft; only Cassava issues it
    draft = c.get(f"{base}/draft", params={"period": "2026-10"}, headers=litzim).json()
    assert c.post(base, params={"period": "2026-10"}, headers=litzim).status_code == 403
    inv = c.post(base, params={"period": "2026-10"}, headers=platform)
    assert inv.status_code == 201, inv.text
    inv = inv.json()
    by = {x["tenant_name"]: x["subtotal"] for x in inv["customers"]}
    assert by == {"Bakers Inn": "341.42", "Isolation Test Foods": "721.00"}  # hand-worked
    assert (inv["subtotal"], inv["tax"], inv["total"]) == ("1062.42", "159.36", "1221.78")
    assert draft["total"] == inv["total"] and inv["stamp"] and inv["due_date"] == "2026-11-16"
    assert [i["number"] for i in c.get(base, headers=litzim).json()] == [inv["number"]]
    # Bakers Inn is LITZIM's to invoice: it sees what it used, not Cassava's prices
    owner = login(c, "owner")
    r = c.get("/api/v1/billing/invoices/draft", params={"period": "2026-10"}, headers=owner)
    assert r.status_code == 409 and "statement" in r.json()["detail"]
    st = c.get("/api/v1/billing/statement", params={"period": "2026-10"}, headers=owner).json()
    assert st["billed_by"] == "LITZIM" and st["lines"] and "amount" not in st["lines"][0]
    issue = f"/api/v1/platform/tenants/{BAKERS_INN_ID}/invoices"
    assert c.post(issue, params={"period": "2026-10"}, headers=platform).status_code == 422


def test_t7_10a_litzim_unpaid_suspends_its_customers_while_their_edges_keep_counting(billed):
    c, clock = billed
    litzim_id, test_depot = litzims_october(c, clock)
    platform = login(c, "platform")
    inv = c.post(
        f"/api/v1/platform/partners/{litzim_id}/invoices",
        params={"period": "2026-10"},
        headers=platform,
    ).json()
    clock.at = datetime(2026, 11, 24, tzinfo=UTC)  # past due and past the grace
    for t in (BAKERS_INN_ID, test_depot):
        assert refresh(c, t) == ("trial" if t == BAKERS_INN_ID else "active", "suspended")
    admin = login(c, "admin")
    bay = c.get("/api/v1/bays", headers=admin).json()[0]
    cams = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
    new = {"name": "x", "role": "overhead", "source_url": "rtsp://10.0.0.99/s"}
    assert c.post(f"/api/v1/bays/{bay['id']}/cameras", json=new, headers=admin).status_code == 402
    # counting carries on: the edge's crossing lands while the tenant is suspended
    choke = next(x["id"] for x in cams if x["role"] == "chokepoint")
    crossing = {
        "bay_id": bay["id"],
        "camera_id": choke,
        "track_id": 1,
        "direction": "loading",
        "crates": 12,
        "confidence": 0.9,
        "crossed_at": clock.at.isoformat(),
    }
    assert c.post("/api/v1/ingest/crossings", json=crossing, headers=SERVICE).status_code < 300
    c.post(
        f"/api/v1/platform/partner-invoices/{inv['number']}/payments",
        json={"amount": inv["total"], "reference": "LITZIM-BT-9"},
        headers=platform,
    )
    assert status(c, BAKERS_INN_ID) == "trial" and status(c, test_depot) == "active"


def test_t7_10b_a_partner_holds_one_customer_and_only_lifting_it_returns_it(billed):
    c, clock = billed
    litzim_id, test_depot = litzims_october(c, clock)
    litzim = login(c, "litzim")
    hold = f"/api/v1/platform/tenants/{BAKERS_INN_ID}/hold"
    held = c.put(hold, json={"on_hold": True, "reason": "LITZIM invoice 41 unpaid"}, headers=litzim)
    assert held.status_code == 200 and held.json() == {
        "tenant_id": str(BAKERS_INN_ID),
        "status": "suspended",
        "on_hold": True,
    }
    assert status(c, test_depot) == "active"  # only Bakers Inn
    r = c.post(
        "/api/v1/sessions", json={"bay_id": "x", "direction": "loading"}, headers=login(c, "admin")
    )
    assert r.status_code == 402 and "partner has put it on hold" in r.json()["detail"]
    assert refresh(c, BAKERS_INN_ID) is None  # nothing paid or unpaid lifts a hold
    released = c.put(hold, json={"on_hold": False}, headers=litzim).json()
    assert released["status"] == "trial" and not released["on_hold"]
    actions = [e["action"] for e in c.get("/api/v1/audit", headers=login(c, "admin")).json()]
    assert {"tenant_held", "tenant_released"} <= set(actions)
    # a partner holds its own customers only
    other = f"/api/v1/platform/tenants/{direct_tenant(c)[0]}/hold"
    assert c.put(other, json={"on_hold": True}, headers=litzim).status_code == 404


# --- the usage producers: storage and assistant tokens ----------------------------------
def test_storage_is_metered_daily_from_the_tenants_own_bytes(billed):
    from decimal import Decimal
    from uuid import UUID

    from ivaas.domain.tenancy import ISOLATION_TEST_ID
    from ivaas.tenancy import object_key, tenant_context

    c, clock = billed
    standard(c)
    container = c.app.state.container
    gib = 1024**3

    async def put(tenant, name, n):
        with tenant_context(tenant):
            await container.objects.put(object_key(name), b"\0" * n, "application/octet-stream")

    c.portal.call(put, BAKERS_INN_ID, "clips/a.mp4", 3 * 1024**2)
    c.portal.call(put, ISOLATION_TEST_ID, "clips/b.mp4", 5 * 1024**2)  # not Bakers Inn's
    clock.at = datetime(2026, 10, 16, 1, tzinfo=UTC)

    async def meter():
        with tenant_context(UUID(str(BAKERS_INN_ID))):
            return await container.billing().meter_storage()

    assert c.portal.call(meter) is True
    assert c.portal.call(meter) is False  # once a day
    used = c.get("/api/v1/billing/subscription", headers=login(c, "owner")).json()
    # 3 MiB on 15 Oct, as a 31st of a month: 3 / 1024 / 31 GB-month
    expected = (Decimal(3 * 1024**2) / Decimal(gib) / 31).quantize(Decimal("0.000001"))
    assert used["usage_this_month"]["storage_gb_month"] == f"{expected.normalize():f}"


def test_assistant_tokens_are_what_the_model_reports_and_nothing_when_it_reports_none(billed):
    from ivaas.ports.assistant import ChatMessage, ToolCall

    c, clock = billed
    standard(c)
    owner = login(c, "owner")

    class Model:
        def __init__(self, *turns):
            self.turns = list(turns)

        async def complete(self, messages, tools):
            return self.turns.pop(0)

    container = c.app.state.container
    ask = {"messages": [{"role": "user", "content": "how many crates today?"}]}
    container.chat_model = Model(
        ChatMessage("assistant", tool_calls=(ToolCall("t1", "camera_health", {}),), tokens=900),
        ChatMessage("assistant", "All cameras are offline.", tokens=350),
    )
    assert c.post("/api/v1/assistant/chat", json=ask, headers=owner).status_code == 200
    container.chat_model = Model(ChatMessage("assistant", "No figures to give."))  # no usage
    assert c.post("/api/v1/assistant/chat", json=ask, headers=owner).status_code == 200
    used = c.get("/api/v1/billing/subscription", headers=owner).json()["usage_this_month"]
    assert used == {"assistant_tokens": "1250"}  # both steps of the first, none of the second


def test_the_model_adapter_reads_reported_usage():
    import asyncio

    import httpx

    from ivaas.adapters.llm.openai_compatible import OpenAiCompatibleChatModel
    from ivaas.ports.assistant import ChatMessage

    def reply(usage):
        body = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        return httpx.Response(200, json={**body, **({"usage": usage} if usage else {})})

    async def run(usage):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: reply(usage)), base_url="http://llm"
        )
        model = OpenAiCompatibleChatModel("http://llm", "m", client=client)
        return (await model.complete([ChatMessage("user", "hi")], [])).tokens

    assert asyncio.run(run({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})) == 15
    assert asyncio.run(run(None)) is None
    assert asyncio.run(run({"total_tokens": "lots"})) is None
