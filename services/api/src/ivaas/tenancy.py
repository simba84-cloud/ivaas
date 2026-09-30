"""The tenant a unit of work runs for.

Set once, at the edge of the system: by the auth dependency for a request, by the
worker for a job, by a background loop for each tenant it sweeps. Everything below
reads it rather than being handed a tenant id, and the database refuses rows from
any other tenant (row-level security), so a repository that forgets to filter
cannot leak.

`system_context()` is the one way to act across tenants. It exists for the few
places that must: finding an account before its tenant is known, claiming the
next job from a shared queue, and the platform provisioning a tenant. Every use is
a deliberate, grep-able call.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

_tenant: ContextVar[UUID | None] = ContextVar("ivaas_tenant", default=None)
_system: ContextVar[bool] = ContextVar("ivaas_system", default=False)


class NoTenantError(RuntimeError):
    """Tenant-owned data was touched with no tenant in context: a bug, never a user error."""

    def __init__(self) -> None:
        super().__init__("no tenant in context")


def current_tenant() -> UUID | None:
    return _tenant.get()


def is_system() -> bool:
    return _system.get()


def require_tenant() -> UUID:
    tenant = _tenant.get()
    if tenant is None:
        raise NoTenantError()
    return tenant


def set_tenant(tenant_id: UUID | None) -> None:
    """For the auth dependency: the request's task ends with the request, so no reset."""
    _tenant.set(tenant_id)


#: Every object a tenant stores lives under its own prefix (proposal §3.2).
OBJECT_PREFIX = "tenants/"


def object_key(key: str) -> str:
    """`key` placed under the current tenant's prefix in object storage."""
    return f"{OBJECT_PREFIX}{require_tenant()}/{key}"


def tenant_of_object(key: str) -> UUID | None:
    """The tenant an object key belongs to, or None for keys written before tenancy."""
    if not key.startswith(OBJECT_PREFIX):
        return None
    try:
        return UUID(key[len(OBJECT_PREFIX) :].split("/", 1)[0])
    except ValueError:
        return None


@contextmanager
def tenant_context(tenant_id: UUID) -> Iterator[None]:
    t = _tenant.set(tenant_id)
    s = _system.set(False)  # entering a tenant always narrows, even from system
    try:
        yield
    finally:
        _system.reset(s)
        _tenant.reset(t)


@contextmanager
def system_context() -> Iterator[None]:
    s = _system.set(True)
    try:
        yield
    finally:
        _system.reset(s)
