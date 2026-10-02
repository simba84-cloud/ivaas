"""Cancel, reinstate, export, import and purge a tenant (M8, T8.4 and T8.5)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from ivaas.domain.lifecycle import (
    DeletionCertificate,
    LifecycleError,
    object_keys_in,
    purge_after,
    sign,
)
from ivaas.domain.tenancy import Tenant, TenantStatus
from ivaas.tenancy import OBJECT_PREFIX, system_context

FORMAT = "ivaas-tenant-export/1"


class Unavailable(LifecycleError):
    """Export and purge read every table generically: the Postgres store only."""


def _csv(rows: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    cols = sorted({c for r in rows for c in r})
    w = csv.DictWriter(out, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: json.dumps(v) if isinstance(v, (dict, list)) else v for c, v in r.items()})
    return out.getvalue()


@dataclass
class TenantLifecycle:
    tenants: Any
    data: Any  # PostgresTenantData, or None in memory
    objects: Any
    certificates: Any
    clock: Any
    retention: timedelta
    secret: str
    #: re-places a reinstated tenant where its billing says (Billing.refresh_status)
    refresh: Callable[[UUID], Awaitable[None]] | None = None

    async def _tenant(self, tenant_id: UUID) -> Tenant:
        with system_context():
            tenant = await self.tenants.get(tenant_id)
        if tenant is None:
            raise LifecycleError(f"no tenant {tenant_id}")
        return tenant

    def purge_after(self, tenant: Tenant) -> datetime | None:
        return purge_after(tenant.cancelled_at, self.retention)

    # --- cancel and reinstate ----------------------------------------------------------
    async def cancel(self, tenant_id: UUID, by: str, confirm: str) -> Tenant:
        tenant = await self._tenant(tenant_id)
        if confirm != tenant.slug:
            raise LifecycleError(f"type the tenant's short name, {tenant.slug}, to confirm")
        if tenant.status is TenantStatus.CANCELLED:
            raise LifecycleError("already cancelled")
        tenant.status = TenantStatus.CANCELLED
        tenant.cancelled_at, tenant.cancelled_by = self.clock.now(), by
        with system_context():
            await self.tenants.save(tenant)
        return tenant

    async def reinstate(self, tenant_id: UUID) -> Tenant:
        tenant = await self._tenant(tenant_id)
        if tenant.status is not TenantStatus.CANCELLED:
            raise LifecycleError("only a cancelled tenant can be reinstated")
        # back as a trial; billing then moves it on where a plan or a bill says so
        tenant.status, tenant.cancelled_at, tenant.cancelled_by = TenantStatus.TRIAL, None, None
        with system_context():
            await self.tenants.save(tenant)
        if self.refresh is not None:
            await self.refresh(tenant_id)  # a trial is a trial again, an unpaid bill past due
        return await self._tenant(tenant_id)

    # --- export and import -------------------------------------------------------------
    async def _objects_of(self, tenant_id: UUID, rows: dict[str, list[dict]]) -> list[str]:
        with system_context():
            prefixed = await self.objects.list_keys(f"{OBJECT_PREFIX}{tenant_id}/")
        return sorted(set(prefixed) | object_keys_in(rows))

    async def export(self, tenant_id: UUID, by: str, path: Path) -> dict[str, Any]:
        """Everything the tenant has, to a zip at `path`: each table as JSON and CSV, its
        objects, and a manifest of counts and checksums to check an import against."""
        if self.data is None:
            raise Unavailable("export needs the Postgres store")
        tenant = await self._tenant(tenant_id)
        rows, withheld = await self.data.dump(tenant_id)
        manifest: dict[str, Any] = {
            "format": FORMAT,
            "tenant": {"id": str(tenant.id), "slug": tenant.slug, "name": tenant.name},
            "exported_at": self.clock.now().isoformat(),
            "exported_by": by,
            "order": list(rows),
            "counts": {t: len(r) for t, r in rows.items()},
            "withheld": withheld,
            "objects": [],
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for table, found in rows.items():
                z.writestr(f"tables/{table}.json", json.dumps(found, default=str))
                z.writestr(f"tables/{table}.csv", _csv(found))
            for key in await self._objects_of(tenant_id, rows):
                try:
                    blob = await self.objects.read(key)
                except Exception:
                    manifest["objects"].append({"key": key, "missing": True})
                    continue
                z.writestr(f"objects/{key}", blob)
                manifest["objects"].append(
                    {"key": key, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
                )
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
        return manifest

    async def import_into(self, path: Path) -> dict[str, Any]:
        """A scratch environment's import: the rows into this database, the objects into
        this store, and what was loaded against what the manifest says was exported."""
        if self.data is None:
            raise Unavailable("import needs the Postgres store")
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("format") != FORMAT:
                raise LifecycleError(f"not an export this version reads: {manifest.get('format')}")
            rows = {t: json.loads(z.read(f"tables/{t}.json")) for t in manifest["order"]}
            loaded = await self.data.load(rows)
            restored = 0
            with system_context():
                for o in manifest["objects"]:
                    if o.get("missing"):
                        continue
                    blob = z.read(f"objects/{o['key']}")
                    if hashlib.sha256(blob).hexdigest() != o["sha256"]:
                        raise LifecycleError(f"{o['key']} does not match its checksum")
                    await self.objects.put(o["key"], blob, "application/octet-stream")
                    restored += 1
        counts = await self.data.counts(UUID(manifest["tenant"]["id"]))
        return {"manifest": manifest, "loaded": loaded, "counts": counts, "objects": restored}

    # --- purge -------------------------------------------------------------------------
    async def purge(self, tenant_id: UUID, by: str, confirm: str) -> DeletionCertificate:
        tenant = await self._tenant(tenant_id)
        if tenant.status is not TenantStatus.CANCELLED:
            raise LifecycleError("only a cancelled tenant is purged: cancel it first")
        due = self.purge_after(tenant)
        now = self.clock.now()
        if due is None or now < due:
            raise LifecycleError(f"its data is kept until {due:%Y-%m-%d}: purge after that")
        if confirm != tenant.slug:
            raise LifecycleError(f"type the tenant's short name, {tenant.slug}, to confirm")
        if self.data is None:
            raise Unavailable("purge needs the Postgres store")

        rows, _ = await self.data.dump(tenant_id)
        keys = await self._objects_of(tenant_id, rows)
        with system_context():
            for key in keys:
                await self.objects.delete(key)
        deleted = await self.data.purge(tenant_id)

        # the scan: nothing of the tenant may remain, or there is no certificate
        left_rows = {t: n for t, n in (await self.data.counts(tenant_id)).items() if n}
        with system_context():
            left_objects = [
                k for k in await self.objects.list_keys(f"{OBJECT_PREFIX}{tenant_id}/")
            ] + [k for k in keys if await self._exists(k)]
        if left_rows or left_objects:
            raise LifecycleError(
                f"the purge left data behind: rows {left_rows}, objects {left_objects[:5]}"
            )
        body = {
            "tenant": {"id": str(tenant.id), "slug": tenant.slug, "name": tenant.name},
            "cancelled_at": tenant.cancelled_at.isoformat() if tenant.cancelled_at else None,
            "cancelled_by": tenant.cancelled_by,
            "retention_days": self.retention.days,
            "purged_at": now.isoformat(),
            "purged_by": by,
            "rows_deleted": {t: n for t, n in deleted.items() if n},
            "objects_deleted": len(keys),
            "scan": {
                "tables_checked": len(deleted),
                "rows_remaining": 0,
                "object_prefix": f"{OBJECT_PREFIX}{tenant_id}/",
                "objects_remaining": 0,
            },
        }
        cert = DeletionCertificate(
            tenant.id, tenant.slug, tenant.name, now, by, body, sign(body, self.secret)
        )
        await self.certificates.save(cert)
        return cert

    async def _exists(self, key: str) -> bool:
        try:
            await self.objects.read(key)
        except Exception:
            return False
        return True
