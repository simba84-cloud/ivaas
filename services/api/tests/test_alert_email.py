"""Email alerts: a disputed load, a manual sign-off, a camera gone dark, a failed
analysis and the day's
summary each reach the tenant's chosen people once, through the relay, and the
figures in them are only ones the platform measured.

The mail sender is a fake that records what it was asked to send. The SMTP adapter
is tested separately against a scripted smtplib.SMTP.
"""

from __future__ import annotations

import smtplib
from datetime import UTC, datetime, timedelta

import pytest
from conftest import SERVICE, login, make_client

from ivaas.adapters.mail import DisabledMailSender, SmtpMailSender
from ivaas.domain.alert_email import (
    BACKOFF_S,
    MAX_ATTEMPTS,
    AlertEmail,
    AlertKind,
    EmailStatus,
)
from ivaas.domain.tenancy import BAKERS_INN_ID
from ivaas.ports.mail import MailResult, OutgoingMail
from ivaas.tenancy import tenant_context

# 17:30 UTC is 19:30 in Harare: after the default 18:00 summary hour
T0 = datetime(2026, 10, 1, 17, 30, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.at = T0

    def now(self) -> datetime:
        return self.at


class FakeMailSender:
    configured = True
    host, port, sender = "relay.example", 25, "alerts@example.com"

    def __init__(self) -> None:
        self.sent: list[OutgoingMail] = []
        self.answers: list[MailResult] = []

    async def send(self, mail: OutgoingMail) -> MailResult:
        self.sent.append(mail)
        return self.answers.pop(0) if self.answers else MailResult(True)


@pytest.fixture
def world():
    with make_client() as c:
        container = c.app.state.container
        container.clock = clock = Clock()
        container.mail_sender = mail = FakeMailSender()
        container.probe = None  # never reach out to the demo cameras' addresses
        # the background loops would race the test for the same outbox and cameras
        c.portal.call(lambda: [t.cancel() for t in c.app.state.background_tasks])
        admin = login(c, "admin")

        def run(make):
            async def go():
                with tenant_context(BAKERS_INN_ID):
                    return await make()

            return c.portal.call(go)

        c.run = run
        c.deliver = lambda: run(lambda: container.deliver_alert_emails_uc()())
        c.outbox = lambda: run(lambda: container.alert_emails.recent(100))
        yield c, admin, container, mail, clock


def switch_on(c, admin, email="admin@bakers-inn.example"):
    r = c.put("/api/v1/users/admin/email", json={"email": email}, headers=admin)
    assert r.status_code == 200, r.text
    r = c.put("/api/v1/settings/alert_email", json={"value": "on"}, headers=admin)
    assert r.status_code == 200, r.text


def disputed_load(c, admin, crates=24, manual=30):
    bay = c.get("/api/v1/bays", headers=admin).json()[0]["id"]
    cams = {x["role"]: x["id"] for x in c.get(f"/api/v1/bays/{bay}/cameras", headers=admin).json()}
    s = c.post("/api/v1/sessions", json={"bay_id": bay, "direction": "loading"}, headers=admin)
    session = s.json()["id"]
    crossing = {
        "bay_id": bay,
        "camera_id": cams["chokepoint"],
        "track_id": 1,
        "direction": "loading",
        "crates": crates,
        "confidence": 0.9,
        "crossed_at": T0.isoformat(),
    }
    c.post("/api/v1/ingest/crossings", json=crossing, headers=SERVICE)
    c.post(f"/api/v1/sessions/{session}/close", headers=admin)
    url = f"/api/v1/sessions/{session}/reconcile"
    r = c.post(url, json={"manual_count": manual}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "disputed", r.text
    return session


# accounts --------------------------------------------------------------------


def test_an_account_gets_an_email_address_and_a_bad_one_is_refused(world):
    c, admin, *_ = world
    mixed = {"email": " Admin@Bakers-Inn.Example "}
    r = c.put("/api/v1/users/admin/email", json=mixed, headers=admin)
    assert r.status_code == 200 and r.json()["email"] == "admin@bakers-inn.example"
    r = c.put("/api/v1/users/admin/email", json={"email": "not an address"}, headers=admin)
    assert r.status_code == 422
    listed = {u["username"]: u for u in c.get("/api/v1/users", headers=admin).json()}
    assert listed["admin"]["email"] == "admin@bakers-inn.example"
    actions = [e["action"] for e in c.get("/api/v1/audit", headers=admin).json()]
    assert "user_email_changed" in actions
    r = c.put("/api/v1/users/admin/email", json={"email": ""}, headers=admin)
    assert r.json()["email"] is None


def test_a_new_account_can_be_created_with_its_email(world):
    c, admin, *_ = world
    body = {"username": "manager1", "roles": ["site_manager"], "email": "m1@bakers-inn.example"}
    r = c.post("/api/v1/users", json=body, headers=admin)
    assert r.status_code == 201, r.text
    assert r.json()["user"]["email"] == "m1@bakers-inn.example"


# triggers --------------------------------------------------------------------


def test_a_disputed_load_is_emailed_once_with_its_real_counts(world):
    c, admin, container, mail, _ = world
    switch_on(c, admin)
    session = disputed_load(c, admin, crates=24, manual=30)
    # reconciling again publishes the same dispute again: still one email
    c.post(f"/api/v1/sessions/{session}/reconcile", json={"manual_count": 30}, headers=admin)
    [queued] = c.outbox()
    assert queued.kind is AlertKind.DISCREPANCY and queued.recipients == (
        "admin@bakers-inn.example",
    )
    [sent] = c.deliver()
    assert sent.status is EmailStatus.SENT
    [message] = mail.sent
    assert message.subject.startswith("Count disputed: no plate read")
    assert "AI count" in message.text and "24" in message.text and "30" in message.text
    assert "-6 crates" in message.text
    assert c.deliver() == []  # nothing left to send


def test_a_signed_off_load_is_emailed_once_with_who_why_and_the_unchanged_counts(world):
    c, admin, container, mail, _ = world
    switch_on(c, admin)
    session = disputed_load(c, admin, crates=24, manual=30)
    body = {"reason": "damaged_removed", "note": "six crates torn at the dock"}
    r = c.post(f"/api/v1/sessions/{session}/approve", json=body, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "approved", r.text
    # signing off again is refused, so it can never be emailed twice
    again = c.post(f"/api/v1/sessions/{session}/approve", json=body, headers=admin)
    assert again.status_code != 200
    kinds = sorted(e.kind.value for e in c.outbox())
    assert kinds == ["discrepancy", "sign_off"]

    c.deliver()
    [message] = [m for m in mail.sent if m.subject.startswith("Load signed off")]
    assert message.subject == "Load signed off: no plate read at Loading Bay"
    assert "Damaged crates removed" in message.text
    assert "six crates torn at the dock" in message.text
    assert "-6 crates" in message.text and "24" in message.text and "30" in message.text
    assert "not that it did not happen" in message.text
    assert message.to == ("admin@bakers-inn.example",)


def test_a_crate_exception_a_supervisor_accepts_is_emailed_once(world):
    c, admin, container, mail, _ = world
    switch_on(c, admin)
    bay = c.get("/api/v1/bays", headers=admin).json()[0]["id"]
    cams = {x["role"]: x["id"] for x in c.get(f"/api/v1/bays/{bay}/cameras", headers=admin).json()}
    r = c.put(f"/api/v1/bays/{bay}/standard", json={"columns": 2, "layers": 14}, headers=admin)
    assert r.status_code == 200, r.text
    driver = {"name": "Tendai Moyo", "badge_code": "bi-0042"}
    assert c.post("/api/v1/drivers", json=driver, headers=admin).status_code == 201
    checked_in = c.post(
        f"/api/v1/bays/{bay}/check-in",
        json={"badge": "BI0042", "direction": "offloading", "declared_units": 1},
        headers=admin,
    )
    assert checked_in.status_code == 201, checked_in.text
    txn = checked_in.json()["id"]
    for n, crates in ((1, 14), (2, 12)):
        column = {
            "bay_id": bay,
            "camera_id": cams["chokepoint"],
            "track_id": n,
            "direction": "offloading",
            "crates": crates,
            "confidence": 0.9,
            "agreement": 0.9,
            "crossed_at": (T0 + timedelta(seconds=n)).isoformat(),
        }
        assert c.post("/api/v1/ingest/crossings", json=column, headers=SERVICE).status_code == 200
    closed = c.post(f"/api/v1/crate-transactions/{txn}/close", headers=admin)
    assert closed.json()["status"] == "short", closed.text
    r = c.post(
        f"/api/v1/crate-transactions/{txn}/accept",
        json={"reason": "counted_by_hand", "note": "two crates split"},
        headers=admin,
    )
    assert r.status_code == 200, r.text

    [queued] = [e for e in c.outbox() if e.kind is AlertKind.SIGN_OFF]
    assert queued.dedupe_key == f"signoff:crates:{txn}"
    c.deliver()
    [message] = [m for m in mail.sent if m.subject.startswith("Crates signed off")]
    assert message.subject == "Crates signed off: Tendai Moyo at Loading Bay"
    for expected in ("Short", "28", "26", "Counted by hand", "two crates split", "admin"):
        assert expected in message.text, expected
    assert "/crates" in message.text


def test_nothing_is_queued_while_alerts_are_off_or_nobody_has_an_address(world):
    c, admin, *_ = world
    disputed_load(c, admin)  # alerts are off by default
    assert c.outbox() == []
    c.put("/api/v1/settings/alert_email", json={"value": "on"}, headers=admin)
    disputed_load(c, admin)  # on, but no account in the group has an email
    assert c.outbox() == []
    status = c.get("/api/v1/alerts/email", headers=admin).json()
    assert status["recipients"] == [] and "admin" in status["missing_email"]


def test_no_relay_means_nothing_is_queued(world):
    c, admin, container, *_ = world
    container.mail_sender = DisabledMailSender()
    switch_on(c, admin)
    disputed_load(c, admin)
    assert c.outbox() == []
    assert c.get("/api/v1/alerts/email", headers=admin).json()["configured"] is False


def test_a_camera_that_stops_streaming_is_emailed_once_per_outage(world):
    c, admin, container, mail, clock = world
    switch_on(c, admin)
    gateway = container.gateway
    bay = c.get("/api/v1/bays", headers=admin).json()[0]["id"]
    camera = c.get(f"/api/v1/bays/{bay}/cameras", headers=admin).json()[0]
    refresh = lambda: c.run(lambda: container.refresh_camera_status())  # noqa: E731

    gateway.live = {camera["stream_path"]}
    refresh()
    assert c.outbox() == []  # coming online is not an alert
    gateway.live = set()
    refresh()
    refresh()  # still offline: the same outage
    [queued] = c.outbox()
    assert queued.kind is AlertKind.CAMERA_OFFLINE and camera["name"] in queued.subject

    clock.at += timedelta(minutes=5)
    gateway.live = {camera["stream_path"]}
    refresh()
    gateway.live = set()
    refresh()  # a second outage, after it came back
    assert len(c.outbox()) == 2


def test_a_camera_never_seen_streaming_is_not_an_outage(world):
    c, admin, container, *_ = world
    switch_on(c, admin)
    container.gateway.live = set()
    c.run(lambda: container.refresh_camera_status())
    assert c.outbox() == []


def test_a_failed_analysis_is_emailed_with_its_error(world):
    c, admin, container, mail, _ = world
    switch_on(c, admin)
    payload = {"id": "job-1", "status": "failed", "filename": "bay1.mp4", "error": "bad codec"}
    c.run(lambda: container.events.publish("ivaas.analysis.updated", payload))
    c.run(lambda: container.events.publish("ivaas.analysis.updated", payload))
    running = {**payload, "id": "job-2", "status": "running"}
    c.run(lambda: container.events.publish("ivaas.analysis.updated", running))
    [queued] = c.outbox()
    assert queued.kind is AlertKind.ANALYSIS_FAILED and "bad codec" in queued.body_text


def test_the_daily_summary_says_what_it_cannot_compute(world):
    c, admin, container, mail, clock = world
    switch_on(c, admin)
    summaries = lambda: c.run(  # noqa: E731
        lambda: _call(container.send_daily_summaries_uc())
    )
    clock.at = T0 - timedelta(hours=2)  # 17:30 in Harare: before the hour
    assert summaries() == []
    clock.at = T0
    [email] = summaries()
    assert summaries() == []  # once a day
    assert email.kind is AlertKind.DAILY_SUMMARY
    assert "No loads recorded" in email.body_text
    assert "0%" not in email.body_text  # no accuracy invented for a day with none


async def _call(make):
    return await (await make)()


def test_the_daily_summary_can_be_switched_off(world):
    c, admin, container, *_ = world
    switch_on(c, admin)
    c.put("/api/v1/settings/alert_email_daily_summary", json={"value": "off"}, headers=admin)
    assert c.run(lambda: _call(container.send_daily_summaries_uc())) == []


def test_the_summary_hour_must_be_a_whole_hour(world):
    c, admin, *_ = world
    r = c.put("/api/v1/settings/alert_email_summary_hour", json={"value": 7.5}, headers=admin)
    assert r.status_code == 422
    r = c.put("/api/v1/settings/alert_email_summary_hour", json={"value": 7}, headers=admin)
    assert r.status_code == 200


# delivery --------------------------------------------------------------------


def test_a_failing_relay_is_retried_on_the_backoff_then_given_up(world):
    c, admin, container, mail, clock = world
    switch_on(c, admin)
    disputed_load(c, admin)
    mail.answers = [MailResult(False, "451 try later")] * MAX_ATTEMPTS
    for attempt in range(1, MAX_ATTEMPTS + 1):
        [email] = c.deliver()
        assert email.attempts == attempt and email.last_error == "451 try later"
        if attempt < MAX_ATTEMPTS:
            assert email.status is EmailStatus.PENDING
            assert c.deliver() == []  # not due yet
            clock.at += timedelta(seconds=BACKOFF_S[attempt - 1])
    assert email.status is EmailStatus.FAILED
    clock.at += timedelta(days=1)
    assert c.deliver() == []


def test_a_test_email_goes_to_you_now_and_is_audited(world):
    c, admin, container, mail, _ = world
    r = c.post("/api/v1/alerts/email/test", headers=admin)
    assert r.status_code == 409  # no address on the account yet
    c.put("/api/v1/users/admin/email", json={"email": "admin@bakers-inn.example"}, headers=admin)
    r = c.post("/api/v1/alerts/email/test", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent" and r.json()["recipients"] == ["admin@bakers-inn.example"]
    assert mail.sent[0].subject == "IVaaS test email"
    status = c.get("/api/v1/alerts/email", headers=admin).json()
    assert status["relay"] == "relay.example:25" and status["recent"][0]["kind"] == "test"
    actions = [e["action"] for e in c.get("/api/v1/audit", headers=admin).json()]
    assert "alert_email_tested" in actions


def test_a_test_with_no_relay_fails_at_once_and_says_why(world):
    c, admin, container, *_ = world
    container.mail_sender = DisabledMailSender()
    c.put("/api/v1/users/admin/email", json={"email": "admin@bakers-inn.example"}, headers=admin)
    r = c.post("/api/v1/alerts/email/test", headers=admin)
    assert r.json()["status"] == "failed" and "IVAAS_SMTP_HOST" in r.json()["last_error"]


def test_only_settings_managers_see_or_send_alert_emails(world):
    c, *_ = world
    operator = login(c, "operator")
    assert c.get("/api/v1/alerts/email", headers=operator).status_code == 403
    assert c.post("/api/v1/alerts/email/test", headers=operator).status_code == 403


def test_settings_report_the_relay_without_inventing_one():
    with make_client() as c:
        admin = login(c, "admin")
        facts = {f["label"]: f for f in c.get("/api/v1/settings", headers=admin).json()["platform"]}
        assert facts["Email relay"]["value"] == "Not configured"
    with make_client(smtp_host="relay.example", smtp_from="alerts@example.com") as c:
        admin = login(c, "admin")
        facts = {f["label"]: f for f in c.get("/api/v1/settings", headers=admin).json()["platform"]}
        assert facts["Email relay"]["value"] == "relay.example:25"


def test_a_subject_never_spans_lines():
    email = AlertEmail(
        kind=AlertKind.DISCREPANCY,
        dedupe_key="k",
        recipients=("a@example.com",),
        subject="Count disputed: AB\r\nBcc: someone@evil.example",
        body_text="x",
        created_at=T0,
    )
    assert "\n" not in email.subject and "\r" not in email.subject


# the SMTP adapter --------------------------------------------------------------


class ScriptedSMTP:
    """Stands in for smtplib.SMTP: records the conversation, offers what it is told."""

    offers_tls = True
    refuse: dict = {}
    fail: Exception | None = None
    last: ScriptedSMTP | None = None

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout, self.calls = host, port, timeout, []
        ScriptedSMTP.last = self
        if ScriptedSMTP.fail is not None:
            raise ScriptedSMTP.fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")

    def ehlo(self):
        self.calls.append("ehlo")

    def has_extn(self, name):
        return name == "starttls" and self.offers_tls

    def starttls(self, context):
        self.calls.append("starttls")

    def send_message(self, msg):
        self.calls.append("send")
        self.message = msg
        return dict(ScriptedSMTP.refuse)


@pytest.fixture
def smtp():
    ScriptedSMTP.offers_tls, ScriptedSMTP.refuse, ScriptedSMTP.fail = True, {}, None
    return ScriptedSMTP


MAIL = OutgoingMail(("ops@bakers-inn.example",), "Camera offline", "plain", "<p>html</p>")


@pytest.mark.asyncio
async def test_smtp_uses_starttls_when_the_relay_offers_it(smtp):
    sender = SmtpMailSender("relay.example", 25, "xyz@liquid.example", smtp_factory=smtp)
    result = await sender.send(MAIL)
    assert result.ok and result.error is None
    assert smtp.last.calls == ["ehlo", "starttls", "ehlo", "send", "quit"]
    msg = smtp.last.message
    assert msg["From"] == "xyz@liquid.example" and msg["To"] == "ops@bakers-inn.example"
    assert msg["Subject"] == "Camera offline" and msg.is_multipart()


@pytest.mark.asyncio
async def test_smtp_sends_in_the_clear_only_when_tls_is_not_required(smtp):
    smtp.offers_tls = False
    plain = SmtpMailSender("relay.example", 25, "a@example.com", smtp_factory=smtp)
    assert (await plain.send(MAIL)).ok
    assert "starttls" not in smtp.last.calls
    strict = SmtpMailSender(
        "relay.example", 25, "a@example.com", starttls="required", smtp_factory=smtp
    )
    result = await strict.send(MAIL)
    assert not result.ok and "STARTTLS" in result.error and "send" not in smtp.last.calls


@pytest.mark.asyncio
async def test_smtp_failures_come_back_as_reasons_not_exceptions(smtp):
    smtp.fail = ConnectionRefusedError(61, "Connection refused")
    sender = SmtpMailSender("relay.example", 25, "a@example.com", smtp_factory=smtp)
    result = await sender.send(MAIL)
    assert not result.ok and "Connection refused" in result.error

    smtp.fail = smtplib.SMTPRecipientsRefused({"ops@bakers-inn.example": (550, b"relay denied")})
    result = await sender.send(MAIL)
    assert not result.ok and "refused every recipient" in result.error

    smtp.fail = None
    smtp.refuse = {"x@example.com": (550, b"no")}
    result = await sender.send(MAIL)
    assert result.ok and "refused 1" in result.error
