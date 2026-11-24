"""Alert emails: the people who run a tenant told when something needs them.

Four things are worth an email: a load whose AI and manual counts disagree, a camera
that stops streaming, an analysis job that fails, and a summary of the day. Each is
queued once (its dedupe key makes a repeated event or sweep harmless) and sent from
an outbox, retried on a backoff like a webhook, so a slow or unreachable relay never
slows the load that caused it.

Recipients are people, chosen by role: whoever holds one of the tenant's chosen roles,
is enabled, and has an email address on their account. Nobody is emailed who is not
an account here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from ivaas.domain.rbac import Role
from ivaas.domain.users import User

#: seconds to wait after each failed attempt; when they run out, the email fails
BACKOFF_S = (60, 300, 900, 3600, 7200, 21600)
MAX_ATTEMPTS = len(BACKOFF_S) + 1
#: a claimed email not finished by then is someone's crash: send it again
LEASE_S = 120

#: who an alert goes to, as the tenant chooses it in Settings
RECIPIENT_GROUPS: dict[str, frozenset[Role]] = {
    "owners, admins and site managers": frozenset(
        {Role.TENANT_OWNER, Role.TENANT_ADMIN, Role.SITE_MANAGER}
    ),
    "owners and admins": frozenset({Role.TENANT_OWNER, Role.TENANT_ADMIN}),
}
DEFAULT_GROUP = "owners, admins and site managers"


class AlertEmailError(ValueError):
    pass


class AlertKind(StrEnum):
    DISCREPANCY = "discrepancy"
    CAMERA_OFFLINE = "camera_offline"
    ANALYSIS_FAILED = "analysis_failed"
    DAILY_SUMMARY = "daily_summary"
    #: a person accepted a discrepancy on the record: a disputed load, or a crate
    #: transaction a supervisor accepted
    SIGN_OFF = "sign_off"
    TEST = "test"


class EmailStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"  # every attempt in the schedule failed


def one_line(text: str, limit: int = 200) -> str:
    """For a subject: a plate or camera name must never add a header line."""
    return " ".join(text.split())[:limit]


def in_group(user: User, group: str) -> bool:
    roles = RECIPIENT_GROUPS.get(group, RECIPIENT_GROUPS[DEFAULT_GROUP])
    return not user.disabled and user.tenant_id is not None and bool(user.roles & roles)


def recipients(users: list[User], group: str) -> list[str]:
    """Everyone in the group who has an email address, each once."""
    return sorted({u.email for u in users if in_group(u, group) and u.email})


def missing_email(users: list[User], group: str) -> list[str]:
    """People in the group who would get alerts but have no address to get them at."""
    return sorted(u.username for u in users if in_group(u, group) and not u.email)


@dataclass
class AlertEmail:
    kind: AlertKind
    #: one alert, one email: the same key is never queued twice for a tenant
    dedupe_key: str
    recipients: tuple[str, ...]
    subject: str
    body_text: str
    created_at: datetime
    body_html: str | None = None
    status: EmailStatus = EmailStatus.PENDING
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    sent_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.recipients:
            raise AlertEmailError("an alert email needs at least one recipient")
        self.subject = one_line(self.subject)
        if self.next_attempt_at is None and self.status is EmailStatus.PENDING:
            self.next_attempt_at = self.created_at

    def claim(self, now: datetime) -> None:
        """Taken for sending; if the sender dies, it is due again after the lease."""
        self.next_attempt_at = now + timedelta(seconds=LEASE_S)

    def succeeded(self, now: datetime, note: str | None = None) -> None:
        self.attempts += 1
        self.status, self.sent_at, self.next_attempt_at = EmailStatus.SENT, now, None
        self.last_error = note[:300] if note else None

    def failed(self, now: datetime, error: str, *, final: bool = False) -> None:
        self.attempts += 1
        self.last_error = error[:300]
        if final or self.attempts >= MAX_ATTEMPTS:
            self.status, self.next_attempt_at = EmailStatus.FAILED, None
        else:
            self.next_attempt_at = now + timedelta(seconds=BACKOFF_S[self.attempts - 1])


def discrepancy_key(session_id: UUID | str) -> str:
    return f"discrepancy:{session_id}"


def analysis_key(job_id: UUID | str) -> str:
    return f"analysis:{job_id}"


def camera_key(camera_id: UUID | str, last_seen_at: str) -> str:
    """One email per outage: an outage is named by when the camera was last seen."""
    return f"camera:{camera_id}:offline:{last_seen_at}"


def summary_key(site_id: UUID | str, day: str) -> str:
    return f"summary:{site_id}:{day}"


def sign_off_key(what: str, record_id: UUID | str) -> str:
    """A load or transaction is signed off once, so once is all it is emailed."""
    return f"signoff:{what}:{record_id}"
