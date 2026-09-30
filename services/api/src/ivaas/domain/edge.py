"""Edge nodes: the machine at a site that decodes the cameras and counts (proposal M2).

A node proves who it is with a credential it received once, at enrollment, in
exchange for a single-use token an administrator created for one site. From then
on it can speak only for that site: its role is bound there, so a node at one
depot cannot post counts for another.

Secrets are high-entropy random values, so they are stored as SHA-256 digests: a
slow password hash buys nothing against a 256-bit secret. They are shown exactly
once, like temporary passwords, and can never be read back.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

TOKEN_PREFIX = "ivaas-enr-"
CREDENTIAL_PREFIX = "ivaas-node-"
MAX_TOKEN_TTL = timedelta(hours=72)
#: a node that has not reported for longer than this is not known to be working
ONLINE_WITHIN = timedelta(seconds=90)
#: after this long it is not merely late, it is down
OFFLINE_AFTER = timedelta(minutes=5)


class EdgeError(ValueError):
    pass


class NodeStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class NodeHealth(StrEnum):
    """What the fleet view says. "Never seen" is its own state, never "online"."""

    NEVER_SEEN = "never_seen"
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"
    REVOKED = "revoked"


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _mint(prefix: str, ident: UUID) -> tuple[str, str]:
    """-> (the full secret to hand out once, the digest to store)."""
    secret = secrets.token_urlsafe(32)
    return f"{prefix}{ident}.{secret}", _digest(secret)


def parse_secret(value: str, prefix: str) -> tuple[UUID, str] | None:
    """`<prefix><uuid>.<secret>` -> (uuid, secret), or None for anything malformed."""
    if not value.startswith(prefix):
        return None
    ident, _, secret = value[len(prefix) :].partition(".")
    try:
        return UUID(ident), secret
    except ValueError:
        return None


def matches(secret: str, digest: str) -> bool:
    return bool(secret) and hmac.compare_digest(_digest(secret), digest)


def config_version(config: dict[str, Any]) -> str:
    """A stable name for a configuration: equal configs, equal versions."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass
class EnrollmentToken:
    site_id: UUID
    name: str
    token_hash: str
    expires_at: datetime
    created_by: str
    created_at: datetime
    bay_id: UUID | None = None
    used_at: datetime | None = None
    used_by_node: UUID | None = None
    #: set when stored; known before any tenant is, which is what enrollment needs
    tenant_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    @staticmethod
    def issue(
        *,
        site_id: UUID,
        bay_id: UUID | None,
        name: str,
        ttl: timedelta,
        by: str,
        now: datetime,
    ) -> tuple[EnrollmentToken, str]:
        """-> (the token record, the token itself, to show once)."""
        if ttl <= timedelta(0) or ttl > MAX_TOKEN_TTL:
            raise EdgeError(f"an enrollment token lives between 1 and {MAX_TOKEN_TTL} hours")
        if not name.strip():
            raise EdgeError("name the node this token is for")
        ident = uuid4()
        shown, digest = _mint(TOKEN_PREFIX, ident)
        record = EnrollmentToken(
            site_id=site_id,
            bay_id=bay_id,
            name=name.strip(),
            token_hash=digest,
            expires_at=now + ttl,
            created_by=by,
            created_at=now,
            id=ident,
        )
        return record, shown

    def usable(self, secret: str, now: datetime) -> bool:
        return self.used_at is None and now < self.expires_at and matches(secret, self.token_hash)


@dataclass
class CameraReport:
    api_camera_id: str
    connected: bool
    fps: float | None = None
    lag_s: float | None = None


@dataclass
class EdgeNode:
    site_id: UUID
    name: str
    credential_hash: str
    enrolled_at: datetime
    bay_id: UUID | None = None
    hostname: str = ""
    status: NodeStatus = NodeStatus.ACTIVE
    last_seen_at: datetime | None = None
    #: the last heartbeat, as sent: version, uptime, spool backlog, cameras
    last_report: dict[str, Any] = field(default_factory=dict)
    #: the desired pipeline configuration; the node applies it
    config: dict[str, Any] = field(default_factory=dict)
    #: the configuration before the last change: what a rollback restores
    previous_config: dict[str, Any] | None = None
    revoked_at: datetime | None = None
    tenant_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    @staticmethod
    def enrol(token: EnrollmentToken, hostname: str, now: datetime) -> tuple[EdgeNode, str]:
        """-> (the node, its credential, to show once)."""
        ident = uuid4()
        shown, digest = _mint(CREDENTIAL_PREFIX, ident)
        node = EdgeNode(
            site_id=token.site_id,
            bay_id=token.bay_id,
            name=token.name,
            hostname=hostname.strip()[:120],
            credential_hash=digest,
            enrolled_at=now,
            tenant_id=token.tenant_id,
            id=ident,
        )
        token.used_at, token.used_by_node = now, ident
        return node, shown

    @property
    def config_version(self) -> str:
        return config_version(self.config)

    def authenticates(self, secret: str) -> bool:
        return self.status is NodeStatus.ACTIVE and matches(secret, self.credential_hash)

    def set_config(self, config: dict[str, Any]) -> bool:
        """Adopt a new configuration, keeping the current one for a rollback.
        False when nothing changed (and nothing is kept)."""
        if config == self.config:
            return False
        self.previous_config = dict(self.config) if self.config else None
        self.config = dict(config)
        return True

    def rollback(self) -> None:
        """Back to the configuration before the last change. Rolling back twice returns."""
        if not self.previous_config:
            raise EdgeError("this node has no earlier configuration to roll back to")
        self.config, self.previous_config = dict(self.previous_config), dict(self.config)

    def revoke(self, now: datetime) -> None:
        self.status, self.revoked_at = NodeStatus.REVOKED, now

    def record_heartbeat(self, report: dict[str, Any], now: datetime) -> None:
        self.last_report, self.last_seen_at = report, now

    def health(self, now: datetime) -> NodeHealth:
        if self.status is NodeStatus.REVOKED:
            return NodeHealth.REVOKED
        if self.last_seen_at is None:
            return NodeHealth.NEVER_SEEN
        silent = now - self.last_seen_at
        if silent <= ONLINE_WITHIN:
            return NodeHealth.ONLINE
        return NodeHealth.STALE if silent <= OFFLINE_AFTER else NodeHealth.OFFLINE

    @property
    def cameras(self) -> list[CameraReport]:
        return [
            CameraReport(
                str(c.get("api_camera_id", "")),
                bool(c.get("connected")),
                c.get("fps"),
                c.get("lag_s"),
            )
            for c in self.last_report.get("cameras", [])
        ]

    @property
    def config_drift(self) -> bool | None:
        """True when the node runs a different config than it was given; None if unknown."""
        applied = self.last_report.get("config_version")
        return None if not applied else applied != self.config_version
