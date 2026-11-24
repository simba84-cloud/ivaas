"""Email alerts as an administrator sees them: where they go, who gets them, what
went out, and a test sent to yourself.

Nothing here is secret. The relay takes no login, so there is no password to hide;
the host, port and sender are shown so a person can check them against what the
relay's owner allowlisted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel

from ivaas.adapters.http.auth import current_principal, require
from ivaas.domain.alert_email import AlertEmail, missing_email, recipients
from ivaas.domain.audit import AuditAction
from ivaas.domain.platform_settings import ALERT_EMAIL
from ivaas.domain.rbac import Permission as P
from ivaas.ports.auth import Principal

Audit = Callable[..., Awaitable[None]]


class AlertEmailOut(BaseModel):
    id: str
    kind: str
    subject: str
    recipients: list[str]
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None

    @staticmethod
    def of(e: AlertEmail) -> AlertEmailOut:
        return AlertEmailOut(
            id=str(e.id),
            kind=e.kind.value,
            subject=e.subject,
            recipients=list(e.recipients),
            status=e.status.value,
            attempts=e.attempts,
            last_error=e.last_error,
            created_at=e.created_at,
            sent_at=e.sent_at,
        )


class AlertEmailStatusOut(BaseModel):
    #: the deployment has a relay; without one nothing is queued
    configured: bool
    relay: str | None
    sender: str | None
    starttls: str
    #: the tenant's rule: alerts switched on
    enabled: bool
    group: str
    recipients: list[str]
    #: people the rule names who have no email address to be reached at
    missing_email: list[str]
    recent: list[AlertEmailOut]


def add_alert_email_routes(
    app: FastAPI, get_container: Callable[[Request], Any], audit: Audit
) -> None:
    @app.get(
        "/api/v1/alerts/email",
        response_model=AlertEmailStatusOut,
        dependencies=[Depends(require(P.SETTINGS_MANAGE))],
    )
    async def alert_email_status(c: Any = Depends(get_container)) -> AlertEmailStatusOut:
        queue = c.alert_email_queue()
        group = await queue.group()
        users = await c.users.list_all()
        sender = c.mail_sender
        return AlertEmailStatusOut(
            configured=sender.configured,
            relay=f"{sender.host}:{sender.port}" if sender.configured else None,
            sender=sender.sender or None,
            starttls=c.settings.smtp_starttls,
            enabled=await c.effective(ALERT_EMAIL, "off") == "on",
            group=group,
            recipients=recipients(users, group),
            missing_email=missing_email(users, group),
            recent=[AlertEmailOut.of(e) for e in await c.alert_emails.recent(20)],
        )

    @app.post(
        "/api/v1/alerts/email/test",
        response_model=AlertEmailOut,
        dependencies=[Depends(require(P.SETTINGS_MANAGE))],
    )
    async def send_test_email(
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> AlertEmailOut:
        """Sent now, to your own address, so the relay's answer comes straight back."""
        email = await c.send_test_email_uc()(principal.subject)
        await audit(
            c,
            principal.name,
            AuditAction.ALERT_EMAIL_TESTED,
            ", ".join(email.recipients),
            status=email.status.value,
            error=email.last_error,
        )
        return AlertEmailOut.of(email)
