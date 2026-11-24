"""Alert emails: queued when something needs a person, sent from an outbox until they land.

Every figure in an email is one the platform measured. Where the data cannot support
one (no tally sheet reconciled, so no accuracy) the email says so instead of printing
a zero.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from ivaas.application.cameras import SUBJECT_CAMERA_OFFLINE, SUBJECT_CAMERA_ONLINE
from ivaas.application.crate_control import SUBJECT_CRATE_ACCEPTED
from ivaas.application.sessions import SUBJECT_SESSION_APPROVED
from ivaas.domain.alert_email import (
    DEFAULT_GROUP,
    AlertEmail,
    AlertKind,
    EmailStatus,
    analysis_key,
    camera_key,
    discrepancy_key,
    recipients,
    sign_off_key,
    summary_key,
)
from ivaas.domain.manifests import site_tz
from ivaas.domain.models import CameraStatus
from ivaas.domain.platform_settings import (
    ALERT_EMAIL,
    ALERT_EMAIL_DAILY_SUMMARY,
    ALERT_EMAIL_RECIPIENTS,
    ALERT_EMAIL_SUMMARY_HOUR,
    DEFAULTS,
)
from ivaas.domain.reports import DailyReport
from ivaas.domain.users import UserError
from ivaas.ports.mail import AlertEmailStore, MailSender, OutgoingMail
from ivaas.tenancy import current_tenant

log = logging.getLogger(__name__)

#: reads a tenant's operating rule: Container.effective
Effective = Callable[[str, Any], Awaitable[Any]]


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str


def render(title: str, rows: list[tuple[str, str]], note: str, link: str | None) -> Rendered:
    """Plain text first (every client shows it), with a simple HTML twin."""
    width = max((len(label) for label, _ in rows), default=0)
    lines = [title, "", *(f"{label.ljust(width)}  {value}" for label, value in rows)]
    if note:
        lines += ["", note]
    if link:
        lines += ["", f"Open in IVaaS: {link}"]
    lines += ["", "Sent by IVaaS. Email alerts are switched on and off under Settings."]
    cells = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;color:#555">{html.escape(label)}</td>'
        f'<td style="padding:4px 0"><strong>{html.escape(value)}</strong></td></tr>'
        for label, value in rows
    )
    body = [
        f'<h2 style="margin:0 0 12px;font-size:18px">{html.escape(title)}</h2>',
        f'<table style="border-collapse:collapse;font-size:14px">{cells}</table>',
    ]
    if note:
        body.append(f'<p style="font-size:14px">{html.escape(note)}</p>')
    if link:
        body.append(f'<p><a href="{html.escape(link, quote=True)}">Open in IVaaS</a></p>')
    body.append(
        '<p style="font-size:12px;color:#777">Sent by IVaaS. Email alerts are switched on '
        "and off under Settings.</p>"
    )
    page = '<div style="font-family:Arial,Helvetica,sans-serif;color:#111">' + "".join(body)
    return Rendered(title, "\n".join(lines) + "\n", page + "</div>")


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else ""


#: worded as the portal words them (web/src/components/ui.tsx, pages/CrateControl.tsx)
APPROVAL_REASONS = {
    "damaged_removed": "Damaged crates removed",
    "camera_blocked": "Camera view blocked",
    "sheet_error": "Error on the manual sheet",
    "ai_miscount": "AI miscount",
    "other": "Other",
}
ACCEPT_REASONS = {
    "damaged_crates": "Damaged crates",
    "camera_blocked": "Camera blocked",
    "counted_by_hand": "Counted by hand",
    "declaration_error": "Declaration error",
    "other": "Other",
}
TRANSACTION_STATUS = {
    "short": "Short",
    "over": "Over",
    "non_standard": "Not in standard units",
    "unverified": "Could not be verified",
}

SIGN_OFF_NOTE = (
    "The counts stay as they were measured: a sign-off records that a person accepted "
    "the difference, not that it did not happen."
)


def _when(iso: Any) -> str:
    return str(iso or "").replace("T", " ")[:19] + " UTC" if iso else "not recorded"


@dataclass
class AlertEmails:
    """Queues one alert for the tenant in context, to the people its rules name."""

    store: AlertEmailStore
    users: Any
    sender: MailSender
    effective: Effective
    clock: Any

    async def enabled(self) -> bool:
        return self.sender.configured and await self.effective(ALERT_EMAIL, "off") == "on"

    async def group(self) -> str:
        return str(await self.effective(ALERT_EMAIL_RECIPIENTS, DEFAULT_GROUP))

    async def queue(self, kind: AlertKind, key: str, message: Rendered) -> AlertEmail | None:
        if current_tenant() is None or not await self.enabled():
            return None
        if await self.store.exists(key):
            return None  # already emailed; not worth reading every account again
        to = recipients(await self.users.list_all(), await self.group())
        if not to:
            log.warning("alert %s not emailed: nobody to send it to has an email address", key)
            return None
        email = AlertEmail(
            kind=kind,
            dedupe_key=key,
            recipients=tuple(to),
            subject=message.subject,
            body_text=message.text,
            body_html=message.html,
            created_at=self.clock.now(),
        )
        return email if await self.store.queue(email) else None


@dataclass
class QueueAlertEmails:
    """An event sink: the events worth an email become one queued alert each."""

    alerts: AlertEmails
    bays: Any
    portal_url: str
    #: finds a crate transaction's driver by name; optional, the email says so without
    crate_control: Any = None

    def _link(self, path: str) -> str:
        return self.portal_url.rstrip("/") + path

    async def _bay_name(self, bay_id: str | None) -> str:
        try:
            bay = await self.bays.get(UUID(bay_id)) if bay_id else None
        except ValueError:
            bay = None
        return bay.name if bay else "an unknown bay"

    async def publish(self, subject: str, payload: dict) -> None:
        if subject == "ivaas.session.reconciled" and payload.get("status") == "disputed":
            await self._discrepancy(payload)
        elif subject == "ivaas.analysis.updated" and payload.get("status") == "failed":
            await self._analysis_failed(payload)
        elif subject == SUBJECT_CAMERA_OFFLINE:
            await self._camera_offline(payload)
        elif subject == SUBJECT_SESSION_APPROVED:
            await self._load_signed_off(payload)
        elif subject == SUBJECT_CRATE_ACCEPTED:
            await self._crates_signed_off(payload)

    async def _load_signed_off(self, p: dict) -> None:
        bay = await self._bay_name(p.get("bay_id"))
        plate = p.get("plate") or "no plate read"
        reason = str(p.get("approval_reason") or "")
        rows = [
            ("Truck", plate),
            ("Bay", bay),
            ("Direction", str(p.get("direction") or "")),
            ("AI count", str(p.get("count_of_record"))),
            ("Manual count", str(p.get("manual_count"))),
        ]
        if p.get("variance") is not None:
            rows.append(("Difference", f"{p['variance']:+d} crates"))
        rows += [
            ("Signed off by", str(p.get("approved_by") or "not recorded")),
            ("Reason", APPROVAL_REASONS.get(reason, reason or "not recorded")),
        ]
        if p.get("approval_note"):
            rows.append(("Note", str(p["approval_note"])))
        rows.append(("When", _when(p.get("approved_at"))))
        message = render(
            f"Load signed off: {plate} at {bay}", rows, SIGN_OFF_NOTE, self._link("/sessions")
        )
        await self.alerts.queue(AlertKind.SIGN_OFF, sign_off_key("load", p["id"]), message)

    async def _driver_name(self, driver_id: str | None) -> str:
        if self.crate_control is None or not driver_id:
            return "an unknown driver"
        try:
            driver = await self.crate_control.get_driver(UUID(driver_id))
        except ValueError:
            driver = None
        return driver.name if driver else "an unknown driver"

    async def _crates_signed_off(self, p: dict) -> None:
        bay = await self._bay_name(p.get("bay_id"))
        driver = await self._driver_name(p.get("driver_id"))
        found = str(p.get("validated_status") or "")
        reason = str(p.get("accept_reason") or "")
        rows = [
            ("Driver", driver),
            ("Bay", bay),
            ("Direction", str(p.get("direction") or "")),
            ("Found", TRANSACTION_STATUS.get(found, found or "not recorded")),
            ("Crates expected", str(p.get("expected_crates"))),
            (
                "Crates counted",
                "Not measured" if p.get("counted_crates") is None else str(p["counted_crates"]),
            ),
            ("Accepted by", str(p.get("accepted_by") or "not recorded")),
            ("Reason", ACCEPT_REASONS.get(reason, reason or "not recorded")),
        ]
        if p.get("accept_note"):
            rows.append(("Note", str(p["accept_note"])))
        rows.append(("When", _when(p.get("accepted_at"))))
        message = render(
            f"Crates signed off: {driver} at {bay}",
            rows,
            SIGN_OFF_NOTE,
            self._link("/crates"),
        )
        await self.alerts.queue(AlertKind.SIGN_OFF, sign_off_key("crates", p["id"]), message)

    async def _discrepancy(self, p: dict) -> None:
        bay = await self._bay_name(p.get("bay_id"))
        plate = p.get("plate") or "no plate read"
        ai, manual = p.get("count_of_record"), p.get("manual_count")
        rows = [
            ("Truck", plate),
            ("Bay", bay),
            ("Direction", str(p.get("direction") or "")),
            ("AI count", str(ai)),
            ("Manual count", str(manual)),
        ]
        if p.get("variance") is not None:
            rows.append(("Difference", f"{p['variance']:+d} crates"))
        if p.get("accuracy") is not None:
            rows.append(("Accuracy", _pct(p["accuracy"])))
        message = render(
            f"Count disputed: {plate} at {bay}",
            rows,
            "The AI and manual counts disagree by more than the accuracy target. A site "
            "manager needs to review the load's video and sign it off.",
            self._link("/sessions"),
        )
        await self.alerts.queue(AlertKind.DISCREPANCY, discrepancy_key(p["id"]), message)

    async def _analysis_failed(self, p: dict) -> None:
        name = p.get("filename") or "an uploaded video"
        message = render(
            f"Video analysis failed: {name}",
            [("Video", name), ("Error", str(p.get("error") or "no error was recorded"))],
            "Nothing was counted from this video. Upload it again once the cause is fixed.",
            self._link(f"/analysis/{p.get('id')}"),
        )
        await self.alerts.queue(AlertKind.ANALYSIS_FAILED, analysis_key(p["id"]), message)

    async def _camera_offline(self, p: dict) -> None:
        name = p.get("name") or "A camera"
        bay = await self._bay_name(p.get("bay_id"))
        seen = str(p.get("last_seen_at") or "")
        message = render(
            f"Camera offline: {name} at {bay}",
            [("Camera", name), ("Bay", bay), ("Last seen", seen.replace("T", " ")[:19] + " UTC")],
            "Loads at this bay are not being counted from this camera until it streams again.",
            self._link("/cameras"),
        )
        await self.alerts.queue(AlertKind.CAMERA_OFFLINE, camera_key(p["id"], seen), message)


@dataclass
class DeliverAlertEmails:
    store: AlertEmailStore
    sender: MailSender
    clock: Any

    async def __call__(self) -> list[AlertEmail]:
        sent = []
        for email in await self.store.claim_due(self.clock.now()):
            result = await self.sender.send(_outgoing(email))
            if result.ok:
                email.succeeded(self.clock.now(), result.error)
            else:
                email.failed(self.clock.now(), result.error or "not sent")
            await self.store.save(email)
            sent.append(email)
        return sent


def _outgoing(email: AlertEmail) -> OutgoingMail:
    return OutgoingMail(email.recipients, email.subject, email.body_text, email.body_html)


@dataclass
class SendTestEmail:
    """Sent at once, to the person asking, so they see the relay's answer straight away.
    It works with alerts switched off: it is how someone checks before switching on."""

    store: AlertEmailStore
    users: Any
    sender: MailSender
    clock: Any
    portal_url: str

    async def __call__(self, username: str) -> AlertEmail:
        user = await self.users.get(username)
        if user is None or not user.email:
            raise UserError("add an email address to your account first, then send the test")
        message = render(
            "IVaaS test email",
            [("Sent to", user.email), ("Requested by", user.display_name or username)],
            "If you can read this, IVaaS can email alerts to this address.",
            self.portal_url.rstrip("/") + "/settings",
        )
        email = AlertEmail(
            kind=AlertKind.TEST,
            dedupe_key=f"test:{uuid4()}",
            recipients=(user.email,),
            subject=message.subject,
            body_text=message.text,
            body_html=message.html,
            created_at=self.clock.now(),
        )
        result = await self.sender.send(_outgoing(email))
        if result.ok:
            email.succeeded(self.clock.now(), result.error)
        else:  # not retried: the person is waiting for the answer now
            email.failed(self.clock.now(), result.error or "not sent", final=True)
        await self.store.queue(email)
        return email


def summary_message(report: DailyReport, offline: int, link: str) -> Rendered:
    rows = [("Site", report.site), ("Day", report.day.isoformat())]
    if not report.loads:
        rows.append(("Loads", "No loads recorded"))
    else:
        rows += [
            ("Loads", str(len(report.loads))),
            ("Crates dispatched", str(report.dispatched)),
            ("Crates returned", str(report.returned)),
        ]
        if report.in_progress:
            rows.append(("Loads still open", str(report.in_progress)))
        if report.unidentified:
            rows.append(("Loads with no plate", str(report.unidentified)))
        if report.verified and report.mean_accuracy is not None:
            rows += [
                ("Verified against tally", f"{report.verified} load(s)"),
                ("Accuracy", f"{_pct(report.mean_accuracy)} (target {_pct(report.target)})"),
                ("Meeting the target", f"{report.passing} of {report.verified}"),
            ]
        else:
            rows.append(("Accuracy", "No tally sheets reconciled, so it cannot be computed"))
    exceptions = len(report.exceptions)
    rows.append(("Manifest exceptions", str(exceptions) if exceptions else "None"))
    rows.append(("Cameras offline now", str(offline) if offline else "None"))
    return render(f"Daily summary: {report.site}, {report.day.isoformat()}", rows, "", link)


@dataclass
class SendDailySummaries:
    """After the chosen hour in each site's own time: that day's summary, once."""

    alerts: AlertEmails
    build: Any  # BuildDailyReport
    sites: Any
    bays: Any
    cameras: Any
    clock: Any
    portal_url: str

    async def __call__(self) -> list[AlertEmail]:
        if not await self.alerts.enabled():
            return []
        if await self.alerts.effective(ALERT_EMAIL_DAILY_SUMMARY, "on") != "on":
            return []
        default_hour = DEFAULTS[ALERT_EMAIL_SUMMARY_HOUR]
        hour = int(await self.alerts.effective(ALERT_EMAIL_SUMMARY_HOUR, default_hour))
        queued = []
        now: datetime = self.clock.now()
        for site in await self.sites.list_all():
            local = now.astimezone(site_tz(site.timezone))
            if local.hour < hour:
                continue
            key = summary_key(site.id, local.date().isoformat())
            if await self.alerts.store.exists(key):
                continue
            report = await self.build(site.id, local.date())
            offline = 0
            for bay in await self.bays.list_for_site(site.id):
                cams = await self.cameras.list_for_bay(bay.id)
                offline += sum(1 for c in cams if c.status is CameraStatus.OFFLINE)
            link = self.portal_url.rstrip("/") + "/reports"
            email = await self.alerts.queue(
                AlertKind.DAILY_SUMMARY, key, summary_message(report, offline, link)
            )
            if email is not None:
                queued.append(email)
        return queued


__all__ = [
    "AlertEmails",
    "DeliverAlertEmails",
    "EmailStatus",
    "QueueAlertEmails",
    "SendDailySummaries",
    "SendTestEmail",
    "SUBJECT_CAMERA_OFFLINE",
    "SUBJECT_CAMERA_ONLINE",
    "summary_message",
]
