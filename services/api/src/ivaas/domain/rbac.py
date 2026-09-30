"""Scoped role-based access control, proposal §4.

A role binding is (principal, role, scope). The scope is a place in the hierarchy
(platform, partner, tenant, site or bay) and the binding covers everything beneath
it: a site manager for Site X can act on every bay at Site X and on nothing at
Site Y. Access is denied unless a binding grants it.

The matrix is data, transcribed from §4.2, so a test can hold it to the document
cell by cell. Permissions the excerpt does not name but existing endpoints need are
marked as additions below; each is granted only to roles that already held the
equivalent power before tenancy existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from ivaas.domain.tenancy import ScopeType


class Permission(StrEnum):
    # §4.2 as written
    TENANT_CREATE = "tenant.create"
    TENANT_SUSPEND = "tenant.suspend"
    SUBSCRIPTION_MANAGE = "subscription.manage"
    INVOICE_READ = "invoice.read"
    USER_INVITE = "user.invite"
    DEVICE_REGISTER = "device.register"
    DEVICE_CALIBRATE = "device.calibrate"
    VIDEO_LIVE_VIEW = "video.live.view"
    COUNT_READ = "count.read"
    COUNT_OVERRIDE = "count.override"
    GROUNDTRUTH_ENTER = "groundtruth.enter"
    RECONCILIATION_RESOLVE = "reconciliation.resolve"
    REPORT_EXPORT = "report.export"
    APIKEY_MANAGE = "apikey.manage"
    AUDIT_READ = "audit.read"
    ASSISTANT_QUERY = "assistant.query"
    # additions: needed by endpoints the excerpt does not cover
    TOPOLOGY_READ = "topology.read"  # list sites, bays, cameras, zones
    SITE_MANAGE = "site.manage"  # create sites and bays
    SESSION_OPERATE = "session.operate"  # open/close loads, upload video, acknowledge alerts
    SETTINGS_MANAGE = "settings.manage"
    USER_MANAGE = "user.manage"  # change roles, enable/disable, reset passwords
    SECURITY_MANAGE = "security.manage"  # enrol faces: sensitive personal data
    INGEST_WRITE = "ingest.write"  # the edge pipeline posting what it saw


class Role(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    PLATFORM_SUPPORT = "platform_support"
    PLATFORM_BILLING = "platform_billing"
    PARTNER_ADMIN = "partner_admin"
    PARTNER_INSTALLER = "partner_installer"
    TENANT_OWNER = "tenant_owner"
    TENANT_ADMIN = "tenant_admin"
    SITE_MANAGER = "site_manager"
    BAY_OPERATOR = "bay_operator"
    AUDITOR = "auditor"
    INTEGRATION = "integration"


P = Permission

#: §4.2, one row per role. Platform Support holds nothing by default: tenant data is
#: reached only through break-glass access (M8), never through a standing grant.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(
        {P.TENANT_CREATE, P.TENANT_SUSPEND, P.SUBSCRIPTION_MANAGE, P.INVOICE_READ, P.AUDIT_READ}
    ),
    Role.PLATFORM_SUPPORT: frozenset(),
    Role.PLATFORM_BILLING: frozenset({P.SUBSCRIPTION_MANAGE, P.INVOICE_READ}),
    Role.PARTNER_ADMIN: frozenset(
        {
            P.TENANT_CREATE,
            P.SUBSCRIPTION_MANAGE,
            P.INVOICE_READ,
            P.USER_INVITE,
            P.DEVICE_REGISTER,
            P.DEVICE_CALIBRATE,
        }
    ),
    Role.PARTNER_INSTALLER: frozenset(
        {P.DEVICE_REGISTER, P.DEVICE_CALIBRATE, P.VIDEO_LIVE_VIEW, P.TOPOLOGY_READ}
    ),
    Role.TENANT_OWNER: frozenset(
        {
            P.SUBSCRIPTION_MANAGE,
            P.INVOICE_READ,
            P.USER_INVITE,
            P.VIDEO_LIVE_VIEW,
            P.COUNT_READ,
            P.RECONCILIATION_RESOLVE,
            P.REPORT_EXPORT,
            P.APIKEY_MANAGE,
            P.AUDIT_READ,
            P.ASSISTANT_QUERY,
            P.TOPOLOGY_READ,
            P.SITE_MANAGE,
            P.SETTINGS_MANAGE,
            P.USER_MANAGE,
        }
    ),
    Role.TENANT_ADMIN: frozenset(
        {
            P.USER_INVITE,
            P.DEVICE_REGISTER,
            P.DEVICE_CALIBRATE,
            P.VIDEO_LIVE_VIEW,
            P.COUNT_READ,
            P.REPORT_EXPORT,
            P.APIKEY_MANAGE,
            P.AUDIT_READ,
            P.ASSISTANT_QUERY,
            P.TOPOLOGY_READ,
            P.SITE_MANAGE,
            P.SETTINGS_MANAGE,
            P.USER_MANAGE,
            P.SECURITY_MANAGE,
        }
    ),
    Role.SITE_MANAGER: frozenset(
        {
            P.VIDEO_LIVE_VIEW,
            P.COUNT_READ,
            P.COUNT_OVERRIDE,
            P.GROUNDTRUTH_ENTER,
            P.RECONCILIATION_RESOLVE,
            P.REPORT_EXPORT,
            P.ASSISTANT_QUERY,
            P.TOPOLOGY_READ,
            P.SESSION_OPERATE,
        }
    ),
    Role.BAY_OPERATOR: frozenset(
        {
            P.VIDEO_LIVE_VIEW,
            P.COUNT_READ,
            P.COUNT_OVERRIDE,
            P.GROUNDTRUTH_ENTER,
            P.ASSISTANT_QUERY,
            P.TOPOLOGY_READ,
            P.SESSION_OPERATE,
        }
    ),
    Role.AUDITOR: frozenset(
        {
            P.INVOICE_READ,
            P.COUNT_READ,
            P.REPORT_EXPORT,
            P.AUDIT_READ,
            P.ASSISTANT_QUERY,
            P.TOPOLOGY_READ,
        }
    ),
    Role.INTEGRATION: frozenset({P.COUNT_READ, P.REPORT_EXPORT, P.TOPOLOGY_READ, P.INGEST_WRITE}),
}

#: The scopes each role may be bound at (§4.1 "Scope" column).
ALLOWED_SCOPES: dict[Role, frozenset[ScopeType]] = {
    Role.PLATFORM_ADMIN: frozenset({ScopeType.PLATFORM}),
    Role.PLATFORM_SUPPORT: frozenset({ScopeType.PLATFORM}),
    Role.PLATFORM_BILLING: frozenset({ScopeType.PLATFORM}),
    Role.PARTNER_ADMIN: frozenset({ScopeType.PARTNER}),
    Role.PARTNER_INSTALLER: frozenset({ScopeType.PARTNER, ScopeType.TENANT, ScopeType.SITE}),
    Role.TENANT_OWNER: frozenset({ScopeType.TENANT}),
    Role.TENANT_ADMIN: frozenset({ScopeType.TENANT}),
    Role.SITE_MANAGER: frozenset({ScopeType.TENANT, ScopeType.SITE}),
    Role.BAY_OPERATOR: frozenset({ScopeType.TENANT, ScopeType.SITE, ScopeType.BAY}),
    Role.AUDITOR: frozenset({ScopeType.TENANT}),
    Role.INTEGRATION: frozenset({ScopeType.TENANT, ScopeType.SITE}),
}

#: Roles a tenant can hand out to its own people. Platform and partner roles are
#: granted by the platform, never by a customer.
TENANT_ROLES = frozenset(
    {
        Role.TENANT_OWNER,
        Role.TENANT_ADMIN,
        Role.SITE_MANAGER,
        Role.BAY_OPERATOR,
        Role.AUDITOR,
        Role.INTEGRATION,
    }
)

#: Roles that keep a tenant administrable. The last enabled holder cannot lose them.
ADMINISTERING = frozenset({Role.TENANT_OWNER, Role.TENANT_ADMIN})

#: What the three roles that existed before tenancy became (migration 0012).
LEGACY_ROLES: dict[str, tuple[Role, ...]] = {
    "admin": (Role.TENANT_ADMIN, Role.SITE_MANAGER),
    "operator": (Role.BAY_OPERATOR,),
    "viewer": (Role.AUDITOR,),
}


class BindingError(ValueError):
    pass


@dataclass(frozen=True)
class RoleBinding:
    role: Role
    scope_type: ScopeType
    #: the tenant, site or bay id; None for platform scope
    scope_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.scope_type not in ALLOWED_SCOPES[self.role]:
            raise BindingError(
                f"{self.role.value} cannot be granted at {self.scope_type.value} scope"
            )
        if (self.scope_type is ScopeType.PLATFORM) != (self.scope_id is None):
            raise BindingError("only a platform binding has no scope id")

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]


@dataclass(frozen=True)
class Scope:
    """Where the thing being acted on lives. Unknown levels are None."""

    partner_id: UUID | None = None
    tenant_id: UUID | None = None
    site_id: UUID | None = None
    bay_id: UUID | None = None


def covers(binding: RoleBinding, scope: Scope) -> bool:
    """Does this binding reach down to `scope`? Downward inheritance, §4.1."""
    match binding.scope_type:
        case ScopeType.PLATFORM:
            return True
        case ScopeType.PARTNER:
            return scope.partner_id == binding.scope_id
        case ScopeType.TENANT:
            return scope.tenant_id == binding.scope_id
        case ScopeType.SITE:
            return scope.site_id == binding.scope_id
        case ScopeType.BAY:
            return scope.bay_id == binding.scope_id
    return False


def authorize(
    bindings: tuple[RoleBinding, ...] | list[RoleBinding],
    permission: Permission,
    scope: Scope | None = None,
) -> bool:
    """Deny by default. With no scope, asks whether the permission is held anywhere."""
    for b in bindings:
        if permission in b.permissions and (scope is None or covers(b, scope)):
            return True
    return False


def permissions_of(bindings: tuple[RoleBinding, ...] | list[RoleBinding]) -> frozenset[Permission]:
    return frozenset(p for b in bindings for p in b.permissions)
