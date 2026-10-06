"""The end of a tenant (proposal §3.3, M8 T8.4 and T8.5).

Cancelled, a tenant's data is kept for the retention window (90 days, the evidence
window confirmed for Bakers Inn), during which the owner can export it and Cassava can
reinstate it. Once the window has elapsed, it can be purged. The purge leaves a
deletion certificate: what was deleted, a scan showing nothing of the tenant remains,
and an HMAC signature so the certificate cannot be quietly edited afterwards.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

DEFAULT_RETENTION = timedelta(days=90)
#: row columns that name an object in the store: how objects written before keys
#: carried the tenant's prefix are still found
OBJECT_KEY_COLUMNS = frozenset({"object_key", "csv_key", "pdf_key", "xlsx_key", "snapshot_key"})


class LifecycleError(ValueError):
    pass


def canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()


def sign(body: dict[str, Any], secret: str) -> str:
    return hmac.new(secret.encode(), canonical(body), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class DeletionCertificate:
    purged_tenant_id: UUID
    tenant_slug: str
    tenant_name: str
    purged_at: datetime
    purged_by: str
    #: what was deleted, and the scan afterwards; the signed part
    body: dict[str, Any]
    signature: str
    id: UUID = field(default_factory=uuid4)

    def verify(self, secret: str) -> bool:
        return hmac.compare_digest(self.signature, sign(self.body, secret))


def purge_after(cancelled_at: datetime | None, retention: timedelta) -> datetime | None:
    return cancelled_at + retention if cancelled_at else None


def object_keys_in(rows: dict[str, list[dict[str, Any]]]) -> set[str]:
    """Every object a tenant's rows point at."""
    return {
        r[c]
        for found in rows.values()
        for r in found
        for c in OBJECT_KEY_COLUMNS
        if isinstance(r.get(c), str) and r[c]
    }
