"""Row-level security for the in-memory stores: one store per tenant.

Postgres hides other tenants' rows with policies. The in-memory adapters get the
same guarantee structurally: each tenant has its own instance, and a call reaches
the instance for the tenant in context. Nothing a store does can see another
tenant's data, so the fast tests exercise the same isolation the database gives.

With no tenant in context a call raises, where Postgres would return nothing and
refuse the write. Raising is stricter, which is what a test double should be.
Inside `system_context()` with no tenant, calls reach a platform partition: the
rows Postgres would store with a NULL tenant_id (platform audit entries).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from ivaas.tenancy import NoTenantError, current_tenant, is_system


class PerTenant:
    def __init__(self, factory: Callable[[], Any], seed: dict[UUID, Any] | None = None) -> None:
        self._factory = factory
        self._stores: dict[UUID | None, Any] = dict(seed or {})

    def partition(self, tenant_id: UUID | None) -> Any:
        store = self._stores.get(tenant_id)
        if store is None:
            store = self._stores[tenant_id] = self._factory()
        return store

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        tenant = current_tenant()
        if tenant is None and not is_system():
            raise NoTenantError()
        return getattr(self.partition(tenant), name)
