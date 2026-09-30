"""Where a site, bay, camera or session sits in the hierarchy, for scope checks.

Row-level security already keeps each tenant to its own rows, so anything these
look up belongs to the caller's tenant. What they add is the level below: a role
held at one site must not reach another site of the same tenant. A resource
outside the caller's scope is reported as not found, exactly like one that does
not exist.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ivaas.adapters.http.auth import check_scope
from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import Permission, Scope
from ivaas.ports.auth import Principal
from ivaas.tenancy import current_tenant


def site_scope(site_id: UUID) -> Scope:
    return Scope(tenant_id=current_tenant(), site_id=site_id)


async def bay_scope(c: Any, bay_id: UUID) -> Scope:
    bay = await c.bays.get(bay_id)
    if bay is None:
        raise NotFoundError(f"bay {bay_id} not found")
    return Scope(tenant_id=current_tenant(), site_id=bay.site_id, bay_id=bay.id)


async def require_bay(c: Any, principal: Principal, permission: Permission, bay_id: UUID) -> None:
    check_scope(principal, permission, await bay_scope(c, bay_id))


async def require_site(c: Any, principal: Principal, permission: Permission, site_id: UUID) -> None:
    if await c.sites.get(site_id) is None:
        raise NotFoundError(f"site {site_id} not found")
    check_scope(principal, permission, site_scope(site_id))


async def require_session(
    c: Any, principal: Principal, permission: Permission, session_id: UUID
) -> None:
    session = await c.sessions.get(session_id)
    if session is None:
        raise NotFoundError(f"session {session_id} not found")
    await require_bay(c, principal, permission, session.bay_id)


async def require_camera(
    c: Any, principal: Principal, permission: Permission, camera_id: UUID
) -> Any:
    camera = await c.cameras.get(camera_id)
    if camera is None:
        raise NotFoundError(f"camera {camera_id} not found")
    await require_bay(c, principal, permission, camera.bay_id)
    return camera


async def require_bay_camera(
    c: Any, principal: Principal, permission: Permission, bay_id: UUID, camera_id: UUID
) -> None:
    """The bay is in scope and the camera is one of its own: an edge node reports only
    what its own cameras saw, at its own site."""
    await require_bay(c, principal, permission, bay_id)
    camera = await c.cameras.get(camera_id)
    if camera is None or camera.bay_id != bay_id:
        raise NotFoundError(f"camera {camera_id} not found")


async def visible_bays(c: Any, principal: Principal, permission: Permission) -> list[Any]:
    """The tenant's bays the caller holds `permission` for."""
    return [
        b
        for b in await c.bays.list_all()
        if principal.can(
            permission, Scope(tenant_id=current_tenant(), site_id=b.site_id, bay_id=b.id)
        )
    ]
