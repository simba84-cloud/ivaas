"""Onboarding a tenant from the partner portal (M8, T8.1): from creation to the first
edge node, without the partner holding standing access to the tenant's data.

Provisioning makes the tenant and its owner. What the install then needs (a site and
a bay to put the node in, and a token to enrol it) lives inside the tenant, where a
partner admin has no account. So the partner gets exactly those steps and no more:
the first site, while the tenant has none, and enrollment tokens for its sites. After
that the tenant's own admins run their topology, and the partner sees only whether
the install got as far as a node that reports in.

Progress is read from what exists, never recorded as ticked boxes: a step is done
because its thing is there, and has a time only where one was recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from ivaas.domain.edge import EdgeNode, EnrollmentToken
from ivaas.domain.models import Bay, NotFoundError, Site
from ivaas.domain.rbac import Role
from ivaas.domain.tenancy import TenancyError, Tenant
from ivaas.tenancy import system_context, tenant_context

#: T8.1: creation to first enrolled edge node
TARGET = timedelta(minutes=30)


class OnboardingError(TenancyError):
    pass


@dataclass(frozen=True)
class Step:
    name: str
    done: bool
    #: when it happened, where that was recorded; None is "not recorded", not "now"
    at: datetime | None = None
    detail: str = ""


@dataclass(frozen=True)
class Progress:
    tenant: Tenant
    steps: list[Step]
    sites: list[Site]
    bays: list[Bay]
    nodes: list[EdgeNode]
    #: creation to the first node enrolling; None until a node has enrolled
    to_first_node: timedelta | None

    @property
    def within_target(self) -> bool | None:
        return None if self.to_first_node is None else self.to_first_node <= TARGET


@dataclass
class Onboarding:
    tenants: Any
    users: Any
    sites: Any
    bays: Any
    edge: Any
    billing_store: Any
    clock: Any

    async def _tenant(self, tenant_id: UUID) -> Tenant:
        with system_context():
            tenant = await self.tenants.get(tenant_id)
        if tenant is None:
            raise NotFoundError(f"tenant {tenant_id} not found")
        return tenant

    async def progress(self, tenant_id: UUID) -> Progress:
        tenant = await self._tenant(tenant_id)
        with tenant_context(tenant_id):
            owners = [u for u in await self.users.list_all() if Role.TENANT_OWNER in u.roles]
            sub = await self.billing_store.subscription()
            sites = await self.sites.list_all()
            bays = await self.bays.list_all()
            tokens = await self.edge.list_tokens()
            nodes = await self.edge.list_nodes()

        signed_in = [u for u in owners if not u.must_change_password]
        signed_at = [u.password_changed_at for u in signed_in if u.password_changed_at]
        enrolled = sorted(n.enrolled_at for n in nodes)
        heard = sorted((n.last_seen_at for n in nodes if n.last_seen_at), reverse=True)
        steps = [
            Step("tenant_created", True, tenant.created_at),
            Step(
                "owner_signed_in",
                bool(signed_in),
                min(signed_at, default=None),
                ", ".join(u.username for u in owners) or "no owner",
            ),
            Step(
                "plan_set",
                sub is not None and bool(sub.segments),
                sub.segments[0].starts if sub and sub.segments else None,
                sub.segments[-1].plan if sub and sub.segments else "nothing is billed yet",
            ),
            Step("site_and_bay", bool(sites) and bool(bays), None, _names(sites, bays)),
            Step(
                "enrollment_token",
                bool(tokens),
                tokens[0].created_at if tokens else None,
                f"{len(tokens)} made, {sum(t.used_at is not None for t in tokens)} used",
            ),
            Step("node_enrolled", bool(enrolled), enrolled[0] if enrolled else None),
            Step(
                "node_reporting",
                bool(heard),
                heard[0] if heard else None,
                "last heard" if heard else "never heard from",
            ),
        ]
        to_first = enrolled[0] - tenant.created_at if enrolled and tenant.created_at else None
        return Progress(tenant, steps, sites, bays, nodes, to_first)

    async def first_site(
        self, tenant_id: UUID, site_name: str, timezone: str, bay_name: str
    ) -> tuple[Site, Bay]:
        """The site and bay the first node goes in. Once: after that, the tenant's own
        admins add sites, and the partner has no say in them."""
        tenant = await self._tenant(tenant_id)
        _not_suspended(tenant)
        if not site_name.strip() or not bay_name.strip():
            raise OnboardingError("name the site and its first bay")
        with tenant_context(tenant_id):
            if await self.sites.list_all():
                raise OnboardingError("this tenant already has a site: its own admins add any more")
            site = Site(id=uuid4(), name=site_name.strip(), timezone=timezone)
            await self.sites.save(site)
            bay = Bay(id=uuid4(), site_id=site.id, name=bay_name.strip())
            await self.bays.save(bay)
        return site, bay

    async def enrollment_token(
        self,
        tenant_id: UUID,
        site_id: UUID,
        bay_id: UUID | None,
        name: str,
        ttl: timedelta,
        by: str,
    ) -> tuple[EnrollmentToken, str]:
        tenant = await self._tenant(tenant_id)
        _not_suspended(tenant)
        with tenant_context(tenant_id):
            site = await self.sites.get(site_id)
            if site is None:
                raise NotFoundError(f"site {site_id} not found")
            if bay_id is not None:
                bay = await self.bays.get(bay_id)
                if bay is None or bay.site_id != site_id:
                    raise NotFoundError(f"bay {bay_id} not found at this site")
            record, token = EnrollmentToken.issue(
                site_id=site_id, bay_id=bay_id, name=name, ttl=ttl, by=by, now=self.clock.now()
            )
            await self.edge.save_token(record)
        return record, token


def _names(sites: list[Site], bays: list[Bay]) -> str:
    if not sites:
        return "no site yet"
    return f"{sites[0].name}, {len(bays)} bay{'s' if len(bays) != 1 else ''}"


def _not_suspended(tenant: Tenant) -> None:
    if tenant.status.value == "suspended":
        raise OnboardingError("this tenant is suspended: settle it before installing")
