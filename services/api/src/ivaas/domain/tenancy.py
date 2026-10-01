"""Who owns what: the commercial hierarchy above a site.

    Platform (Cassava) → Partner (LITZIM) → Tenant (Bakers Inn) → Site → Bay → Camera

Cassava runs the platform, a partner resells and installs it, and a tenant is the
customer whose counts these are. Every row of operational data belongs to exactly
one tenant, and no request, job or event may see another tenant's rows. The
database enforces that with row-level security; this module only names the parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5


def stable_id(name: str) -> UUID:
    """Deterministic ids for seeded records, so every environment agrees on them."""
    return uuid5(NAMESPACE_DNS, f"ivaas.{name}")


LITZIM_ID = stable_id("partner.litzim")
BAKERS_INN_ID = stable_id("tenant.bakers-inn")
#: A second tenant that exists only so isolation can be demonstrated and tested.
ISOLATION_TEST_ID = stable_id("tenant.isolation-test")


class TenantStatus(StrEnum):
    """Proposal §3.3. Only the states M1 needs are reachable yet; billing drives the rest."""

    PROVISIONING = "provisioning"
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


#: Counting never stops for a billing reason: edge operations continue while suspended.
OPERATING = frozenset(
    {TenantStatus.TRIAL, TenantStatus.ACTIVE, TenantStatus.PAST_DUE, TenantStatus.SUSPENDED}
)


class ScopeType(StrEnum):
    """Where a role binding applies. Each covers everything beneath it."""

    PLATFORM = "platform"
    PARTNER = "partner"
    TENANT = "tenant"
    SITE = "site"
    BAY = "bay"


_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class TenancyError(ValueError):
    pass


def validate_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not _SLUG.match(slug):
        raise TenancyError(
            "a slug is 1-64 lowercase letters, digits and hyphens, "
            "and cannot start or end with a hyphen"
        )
    return slug


@dataclass
class Partner:
    id: UUID
    slug: str
    name: str


@dataclass
class Tenant:
    id: UUID
    slug: str
    name: str
    partner_id: UUID | None
    status: TenantStatus = TenantStatus.TRIAL
    created_at: datetime | None = None
    #: its partner has held it: suspended until the partner lifts it, whatever is paid
    on_hold: bool = False


@dataclass
class ProvisioningStep:
    """One step of proposal §3.3's provisioning workflow, and whether it has run.

    Steps that belong to later milestones are recorded as pending rather than
    pretended: a tenant with no identity-provider organisation must say so.
    """

    name: str
    done: bool
    detail: str = ""


@dataclass
class ProvisioningRecord:
    idempotency_key: str
    tenant_id: UUID
    owner_username: str
    steps: list[ProvisioningStep] = field(default_factory=list)
    created_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)
