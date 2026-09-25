"""The record of who did what.

This platform exists to produce a number that money depends on, and that number
can be changed by a person: a manual count typed in, a disputed load signed off,
a camera removed. When a count is challenged weeks later, "who changed this, when,
and what did it say before" is the question actually asked. Nothing recorded it
except approvals, so this does.

Entries are append-only. Nothing in the application edits or deletes one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class AuditAction(StrEnum):
    """A fixed set, so entries can be filtered and counted rather than grepped."""

    SIGNED_IN = "signed_in"
    SESSION_OPENED = "session_opened"
    SESSION_CLOSED = "session_closed"
    SESSION_RECONCILED = "session_reconciled"
    SESSION_APPROVED = "session_approved"
    CAMERA_REGISTERED = "camera_registered"
    CAMERA_REMOVED = "camera_removed"
    VIDEO_UPLOADED = "video_uploaded"
    SITE_CREATED = "site_created"
    BAY_CREATED = "bay_created"
    SETTING_CHANGED = "setting_changed"
    USER_CREATED = "user_created"
    USER_ROLES_CHANGED = "user_roles_changed"
    USER_ENABLED = "user_enabled"
    USER_DISABLED = "user_disabled"
    PASSWORD_RESET = "password_reset"
    PASSWORD_CHANGED = "password_changed"


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    actor: str
    action: AuditAction
    #: what was acted on, in the words an operator would use: a plate, a camera name
    subject: str
    #: the specifics worth keeping: counts before and after, the reason given
    detail: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
