"""Accounts that can sign in, and the rules that protect them.

Passwords never appear here in the clear: the domain holds a hash and nothing else,
and no method, log line or API response can hand one back. An administrator can
reset a password but can never read one.

Password rules follow NIST 800-63B rather than the older folklore: length is what
matters, composition rules ("one capital, one symbol") push people towards
predictable substitutions, and forced rotation makes passwords worse, not better.
So: a long minimum, a check against obvious choices, and no expiry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from ivaas.domain.rbac import ADMINISTERING, TENANT_ROLES, Role, RoleBinding
from ivaas.domain.tenancy import ScopeType

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 200  # hashing is deliberately slow; do not let a request choose how slow

#: Not a breach corpus, just the handful this deployment will actually attract.
OBVIOUS_PASSWORDS = frozenset(
    {
        "password",
        "password123",
        "passw0rd",
        "administrator",
        "letmein",
        "changeme",
        "welcome1",
        "qwertyuiop",
        "123456789012",
        "ivaas",
        "bakersinn",
        "bakery",
        "liquid",
    }
)


class WeakPasswordError(ValueError):
    pass


class UserError(Exception):
    pass


class LastAdminError(UserError):
    def __init__(self) -> None:
        super().__init__(
            "this is the last enabled administrator of this tenant; it would be locked out"
        )


class SelfLockoutError(UserError):
    def __init__(self, what: str) -> None:
        super().__init__(f"you cannot {what} your own account")


def validate_password(password: str, *, username: str | None = None) -> None:
    """Raise WeakPasswordError unless the password is acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPasswordError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")
    folded = password.strip().lower()
    if folded in OBVIOUS_PASSWORDS:
        raise WeakPasswordError("that password is too easy to guess; choose something else")
    if username and folded == username.strip().lower():
        raise WeakPasswordError("the password cannot be the username")
    if len(set(password)) < 5:
        raise WeakPasswordError("the password repeats too few characters")


@dataclass
class User:
    username: str
    display_name: str
    password_hash: str
    #: the tenant this account belongs to; None for platform and partner staff
    tenant_id: UUID | None = None
    bindings: list[RoleBinding] = field(default_factory=list)
    disabled: bool = False
    #: set after an administrator resets the password; blocks everything until changed
    must_change_password: bool = False
    #: true while the account still has the password it was seeded with
    password_is_default: bool = False
    created_at: datetime | None = None
    #: tokens issued before this moment are refused, so a reset ends open sessions
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None

    @property
    def roles(self) -> set[Role]:
        """Every role held, at any scope."""
        return {b.role for b in self.bindings}

    @property
    def administers(self) -> bool:
        """Can manage this tenant: the thing the last-admin rule protects."""
        return not self.disabled and any(
            b.role in ADMINISTERING and b.scope_type is ScopeType.TENANT for b in self.bindings
        )

    def set_password(self, password_hash: str, at: datetime, *, temporary: bool = False) -> None:
        self.password_hash = password_hash
        self.password_changed_at = at
        self.must_change_password = temporary
        self.password_is_default = False

    def assign_roles(self, roles: set[Role]) -> None:
        """Replace the account's roles with these, each across the whole tenant."""
        if self.tenant_id is None:
            raise UserError("only an account in a tenant can hold tenant roles")
        self.set_bindings([RoleBinding(r, ScopeType.TENANT, self.tenant_id) for r in sorted(roles)])

    def set_bindings(self, bindings: list[RoleBinding]) -> None:
        if not bindings:
            raise UserError("a user must have at least one role")
        if self.tenant_id is not None:
            foreign = [b for b in bindings if b.role not in TENANT_ROLES]
            if foreign:
                raise UserError(f"a tenant cannot grant {foreign[0].role.value}")
            elsewhere = [
                b
                for b in bindings
                if b.scope_type is ScopeType.TENANT and b.scope_id != self.tenant_id
            ]
            if elsewhere:
                raise UserError("a role cannot reach outside the account's own tenant")
        self.bindings = list(dict.fromkeys(bindings))
