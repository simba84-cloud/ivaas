"""T6.4: webhooks are signed, retried with backoff, replayable, and delivered at least
once with an id a receiver can de-duplicate on.

The real sender is used, pointed at a scripted receiver (httpx.MockTransport) and a
scripted DNS, so the address checks run as they do in production. Signatures are
checked here with hmac directly, as a receiver would, not with the code that made them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import SERVICE, login, make_client

from ivaas.adapters.webhooks import HttpWebhookSender
from ivaas.domain.tenancy import BAKERS_INN_ID, ISOLATION_TEST_ID
from ivaas.domain.webhooks import BACKOFF_S, MAX_ATTEMPTS
from ivaas.tenancy import tenant_context

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
URL = "https://erp.bakers-inn.example/ivaas"
# public addresses (documentation ranges such as 203.0.113.x are not global, and are refused)
PUBLIC = {"erp.bakers-inn.example": ["93.184.216.34"], "other.example": ["1.1.1.1"]}


class Clock:
    def __init__(self) -> None:
        self.at = T0

    def now(self) -> datetime:
        return self.at


class Receiver:
    """What the far end saw, and what it answers next (repeats the last answer)."""

    def __init__(self, *answers: int | httpx.Response) -> None:
        self.answers, self.got = list(answers) or [204], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.got.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer if isinstance(answer, httpx.Response) else httpx.Response(answer)


def receiver_checks(secret: str, request: httpx.Request, now: datetime) -> bool:
    """A receiver's own check, written from the Standard Webhooks description."""
    msg_id, stamp = request.headers["webhook-id"], request.headers["webhook-timestamp"]
    key = base64.b64decode(secret.split("_", 1)[1])
    signed = f"{msg_id}.{stamp}.".encode() + request.content
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    fresh = abs(now.timestamp() - int(stamp)) <= 300
    return fresh and request.headers["webhook-signature"] == f"v1,{expected}"


@pytest.fixture
def world():
    with make_client() as c:
        container = c.app.state.container
        container.clock = clock = Clock()
        far_end = Receiver()

        async def dns(host, port):
            return PUBLIC.get(host, ["10.1.2.3"])  # anything else is on a private network

        container.webhook_sender = HttpWebhookSender(
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: far_end(r))),
            resolver=dns,
        )
        c.far_end = far_end

        def deliver(tenant=BAKERS_INN_ID):
            async def run():
                with tenant_context(tenant):
                    return await container.deliver_webhooks_uc()()

            return c.portal.call(run)

        c.deliver = deliver
        yield c, login(c, "admin"), clock


def subscribe(c, admin, events=("session.closed",), url=URL):
    r = c.post("/api/v1/webhooks", json={"url": url, "events": list(events)}, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()


def a_load(c, admin, crates=24, plate=None):
    bay = c.get("/api/v1/bays", headers=admin).json()[0]["id"]
    cams = {x["role"]: x["id"] for x in c.get(f"/api/v1/bays/{bay}/cameras", headers=admin).json()}
    choke = cams["chokepoint"]
    s = c.post("/api/v1/sessions", json={"bay_id": bay, "direction": "loading"}, headers=admin)
    if plate:
        read = {"bay_id": bay, "camera_id": cams["lpr"], "plate": plate, "confidence": 0.9}
        c.post("/api/v1/ingest/plates", json={**read, "read_at": T0.isoformat()}, headers=SERVICE)
    crossing = {
        "bay_id": bay,
        "camera_id": choke,
        "track_id": 1,
        "direction": "loading",
        "crates": crates,
        "confidence": 0.9,
        "crossed_at": T0.isoformat(),
    }
    c.post("/api/v1/ingest/crossings", json=crossing, headers=SERVICE)
    c.post(f"/api/v1/sessions/{s.json()['id']}/close", headers=admin)
    return s.json()["id"]


def test_a_closed_load_is_sent_signed_so_the_receiver_can_check_it(world):
    c, admin, clock = world
    hook = subscribe(c, admin)
    assert hook["secret"].startswith("whsec_")
    session = a_load(c, admin, crates=24)
    [sent] = c.deliver()
    [request] = c.far_end.got
    assert receiver_checks(hook["secret"], request, clock.now())
    body = json.loads(request.content)
    assert body["type"] == "session.closed" and body["id"] == request.headers["webhook-id"]
    assert body["data"]["id"] == session and body["data"]["count_of_record"] == 24
    assert request.headers["content-type"] == "application/json"
    # a wrong secret, a changed body or a stale timestamp all fail the receiver's check
    other = subscribe(c, admin)["secret"]
    assert not receiver_checks(other, request, clock.now())
    tampered = httpx.Request("POST", URL, headers=request.headers, content=b'{"id":"x"}')
    assert not receiver_checks(hook["secret"], tampered, clock.now())
    assert not receiver_checks(hook["secret"], request, clock.now() + timedelta(minutes=6))
    [listed] = c.get(f"/api/v1/webhooks/{hook['id']}/deliveries", headers=admin).json()
    assert listed["status"] == "delivered" and listed["last_status_code"] == 204


def test_only_what_an_endpoint_subscribes_to_and_only_its_own_tenants(world):
    c, admin, _ = world
    loads = subscribe(c, admin, ["session.closed"])
    disputes = subscribe(c, admin, ["exception.raised"], url="https://other.example/x")
    b = login(c, "b-admin")
    b_hook = subscribe(c, b, ["session.closed", "exception.raised"])
    a_load(c, admin, crates=24, plate="ABC 1234")
    # the manifest says 10 crates for that truck: a count mismatch is raised
    manifest = (
        "date,plate,direction,expected,reference,route\n2026-10-01,ABC 1234,loading,10,M-9,R\n"
    )
    r = c.post(
        "/api/v1/manifests/import", files={"file": ("m.csv", manifest.encode())}, headers=admin
    )
    assert r.json()["exceptions_raised"] == 1, r.text
    c.deliver()
    sent = sorted((str(r.url), json.loads(r.content)["type"]) for r in c.far_end.got)
    assert (URL, "session.closed") in sent
    assert all(url != "https://other.example/x" or t == "exception.raised" for url, t in sent)
    for hook, kind in [(loads, "session.closed"), (disputes, "exception.raised")]:
        rows = c.get(f"/api/v1/webhooks/{hook['id']}/deliveries", headers=admin).json()
        assert rows and {d["event"] for d in rows} == {kind}
    # tenant B's endpoint heard nothing of A's day
    assert c.get(f"/api/v1/webhooks/{b_hook['id']}/deliveries", headers=b).json() == []
    assert c.deliver(ISOLATION_TEST_ID) == []


def test_a_failing_receiver_is_retried_on_the_backoff_then_given_up_then_replayed(world):
    c, admin, clock = world
    hook = subscribe(c, admin)
    c.far_end.answers = [500]
    a_load(c, admin)
    tried_at = []
    for wait in (0, *BACKOFF_S):
        if wait:  # a second short of the next attempt, nothing is sent
            clock.at += timedelta(seconds=wait - 1)
            assert c.deliver() == [], wait
            clock.at += timedelta(seconds=1)
        assert len(c.deliver()) == 1, wait
        tried_at.append(clock.at)
    assert len(c.far_end.got) == MAX_ATTEMPTS
    assert len({r.headers["webhook-id"] for r in c.far_end.got}) == 1  # one event, one id
    gaps = [int((b - a).total_seconds()) for a, b in zip(tried_at, tried_at[1:], strict=False)]
    assert tuple(gaps) == BACKOFF_S
    [failed] = c.get(f"/api/v1/webhooks/{hook['id']}/deliveries", headers=admin).json()
    assert failed["status"] == "failed" and failed["attempts"] == MAX_ATTEMPTS
    assert failed["last_status_code"] == 500
    clock.at += timedelta(days=1)
    assert c.deliver() == []  # given up means given up

    # the receiver is fixed; someone replays it
    c.far_end.answers = [200]
    again = c.post(f"/api/v1/webhooks/deliveries/{failed['id']}/replay", headers=admin)
    assert again.status_code == 202 and again.json()["replay_of"] == failed["id"]
    c.deliver()
    assert c.far_end.got[-1].headers["webhook-id"] == c.far_end.got[0].headers["webhook-id"]
    rows = {
        d["id"]: d for d in c.get(f"/api/v1/webhooks/{hook['id']}/deliveries", headers=admin).json()
    }
    assert rows[again.json()["id"]]["status"] == "delivered"
    assert rows[failed["id"]]["status"] == "failed"  # its history stays as it was
    audit = c.get("/api/v1/audit", headers=admin).json()
    assert any(e["action"] == "webhook_replayed" for e in audit)


def test_a_sender_that_dies_mid_send_sends_it_again_after_the_lease(world):
    """At least once: a crash between claiming and recording loses nothing."""
    c, admin, clock = world
    subscribe(c, admin)
    a_load(c, admin)
    real = c.app.state.container.webhook_sender

    class Dies:
        async def send(self, url, headers, body):
            raise RuntimeError("the process was killed")

    c.app.state.container.webhook_sender = Dies()
    with pytest.raises(RuntimeError):
        c.deliver()
    c.app.state.container.webhook_sender = real
    assert c.deliver() == []  # still claimed by the sender that died
    clock.at += timedelta(seconds=61)
    [sent] = c.deliver()
    assert sent.status.value == "delivered" and len(c.far_end.got) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://erp.bakers-inn.example/x",  # not https
        "https://10.0.0.5/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/x",
        "https://erp.local/x",
        "https://user:pw@erp.bakers-inn.example/x",
        "ftp://erp.bakers-inn.example/x",
    ],
)
def test_an_endpoint_must_be_public_https(world, url):
    c, admin, _ = world
    r = c.post("/api/v1/webhooks", json={"url": url, "events": ["session.closed"]}, headers=admin)
    assert r.status_code == 422, url


def test_a_name_that_resolves_inside_is_not_sent_to_nor_is_a_redirect_followed(world):
    c, admin, clock = world
    subscribe(c, admin, url="https://sneaky.example/x")  # public-looking; resolves to 10.x
    a_load(c, admin)
    [d] = c.deliver()
    assert d.status.value == "pending" and "private address" in d.last_error
    assert c.far_end.got == []

    redirecting = subscribe(c, admin)
    c.far_end.answers = [httpx.Response(302, headers={"location": "http://10.0.0.1/"})]
    c.post(f"/api/v1/webhooks/{redirecting['id']}/test", headers=admin)
    clock.at += timedelta(seconds=1)
    sent = [x for x in c.deliver() if x.endpoint_id.hex == redirecting["id"].replace("-", "")]
    assert sent and sent[0].last_status_code == 302 and "redirects" in sent[0].last_error
    assert [str(r.url) for r in c.far_end.got] == [URL]  # nothing went to 10.0.0.1


def test_receivers_on_the_site_network_are_allowed_only_when_the_deployment_says_so():
    with make_client(webhook_allow_private=True) as c:
        admin = login(c, "admin")
        r = c.post(
            "/api/v1/webhooks",
            json={"url": "http://192.168.10.20:8080/ivaas", "events": ["exception.raised"]},
            headers=admin,
        )
        assert r.status_code == 201


def test_the_secret_is_shown_once_and_never_again(world):
    c, admin, _ = world
    hook = subscribe(c, admin)
    for path in ("/api/v1/webhooks", "/api/v1/audit", f"/api/v1/webhooks/{hook['id']}/deliveries"):
        assert hook["secret"] not in c.get(path, headers=admin).text, path
    assert c.get("/api/v1/webhooks/events", headers=admin).json()["events"] == [
        "exception.raised",
        "session.closed",
    ]


def test_a_test_event_reaches_the_receiver_and_removing_the_endpoint_stops_it(world):
    c, admin, _ = world
    hook = subscribe(c, admin)
    assert c.post(f"/api/v1/webhooks/{hook['id']}/test", headers=admin).status_code == 202
    c.deliver()
    assert json.loads(c.far_end.got[0].content)["type"] == "webhook.test"
    c.post(f"/api/v1/webhooks/{hook['id']}/test", headers=admin)
    assert c.delete(f"/api/v1/webhooks/{hook['id']}", headers=admin).status_code == 204
    assert c.deliver() == [] and len(c.far_end.got) == 1
    assert c.get("/api/v1/webhooks", headers=admin).json() == []
    assert c.delete(f"/api/v1/webhooks/{hook['id']}", headers=admin).status_code == 404


def test_only_those_who_manage_integrations_can_touch_webhooks(world):
    c, _, _ = world
    for who in ("operator", "viewer"):
        h = login(c, who)
        assert c.get("/api/v1/webhooks", headers=h).status_code == 403
        r = c.post("/api/v1/webhooks", json={"url": URL, "events": ["session.closed"]}, headers=h)
        assert r.status_code == 403
