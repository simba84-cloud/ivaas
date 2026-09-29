"""Provision a tenant: proposal §3.3's workflow, as far as M1 can take it.

The same request sent twice must not create two tenants: a network retry from the
partner portal is the normal case, not an edge case. So the caller supplies an
idempotency key, and a key already used returns the record it produced.

Steps that belong to later milestones (the identity-provider organisation, the
storage key, the plan and the edge enrollment tokens) are recorded as pending,
never reported as done. A tenant must not look more set up than it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from ivaas.application.users import UserExistsError, generate_temporary_password
from ivaas.domain.rbac import Role, RoleBinding
from ivaas.domain.tenancy import (
    ProvisioningRecord,
    ProvisioningStep,
    ScopeType,
    TenancyError,
    Tenant,
    TenantStatus,
    validate_slug,
)
from ivaas.domain.users import User
from ivaas.ports.repositories import Clock
from ivaas.ports.tenancy import TenantStore
from ivaas.ports.users import PasswordHasher, UserStore
from ivaas.tenancy import system_context, tenant_context


class TenantExistsError(TenancyError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"a tenant with slug {slug!r} already exists")


class UnknownPartnerError(TenancyError):
    def __init__(self, partner_id: UUID) -> None:
        super().__init__(f"no partner {partner_id}")


@dataclass
class ProvisionResult:
    record: ProvisioningRecord
    tenant: Tenant
    #: the owner's temporary password, returned on the first call only
    temporary_password: str | None
    created: bool


@dataclass
class ProvisionTenant:
    tenants: TenantStore
    users: UserStore
    hasher: PasswordHasher
    clock: Clock

    async def __call__(
        self,
        *,
        idempotency_key: str,
        slug: str,
        name: str,
        partner_id: UUID | None,
        owner_username: str,
        owner_display_name: str = "",
        status: TenantStatus = TenantStatus.TRIAL,
    ) -> ProvisionResult:
        key = idempotency_key.strip()
        if not key:
            raise TenancyError("an idempotency key is required")
        with system_context():
            done = await self.tenants.get_provisioning(key)
            if done is not None:
                tenant = await self.tenants.get(done.tenant_id)
                assert tenant is not None
                # the password was shown once, on the first call; never again
                return ProvisionResult(done, tenant, None, created=False)

            slug = validate_slug(slug)
            if await self.tenants.get_by_slug(slug) is not None:
                raise TenantExistsError(slug)
            if partner_id is not None and await self.tenants.get_partner(partner_id) is None:
                raise UnknownPartnerError(partner_id)
            owner = owner_username.strip().lower()
            if not owner:
                raise TenancyError("an owner username is required")
            if await self.users.get(owner) is not None:
                raise UserExistsError(owner)

            now = self.clock.now()
            tenant = Tenant(uuid4(), slug, name.strip() or slug, partner_id, status, now)
            await self.tenants.save(tenant)

        temporary = generate_temporary_password()
        with tenant_context(tenant.id):
            user = User(
                username=owner,
                display_name=owner_display_name.strip() or owner,
                password_hash=self.hasher.hash(temporary),
                tenant_id=tenant.id,
                must_change_password=True,
                created_at=now,
                password_changed_at=now,
            )
            user.set_bindings([RoleBinding(Role.TENANT_OWNER, ScopeType.TENANT, tenant.id)])
            await self.users.save(user)
            record = ProvisioningRecord(
                idempotency_key=key,
                tenant_id=tenant.id,
                owner_username=owner,
                created_at=now,
                steps=[
                    ProvisioningStep("tenant_record", True),
                    ProvisioningStep("idp_organisation", False, "M8: per-tenant SSO"),
                    ProvisioningStep(
                        "default_roles", True, "the §4 roles; the owner holds tenant_owner"
                    ),
                    ProvisioningStep("owner_invited", True, "temporary password, shown once"),
                    ProvisioningStep("storage_prefix_and_key", False, "M2: per-tenant key"),
                    ProvisioningStep("plan_and_entitlements", False, "M7"),
                    ProvisioningStep("edge_enrollment_tokens", False, "M2"),
                    ProvisioningStep("audit_entry", True),
                ],
            )
            await self.tenants.save_provisioning(record)
        return ProvisionResult(record, tenant, temporary, created=True)
