"""The signed entitlement snapshot an edge node keeps (proposal §5, M7 T7.7).

With every configuration the API sends a node, it signs what the node is entitled to
run: its plan's channel limits, a hash of the very configuration it was given, and
how long that holds while the node cannot reach the API. The node keeps the pair on
disk. If it starts while the cloud is unreachable, it runs from them; and because the
configuration's hash is inside the signature, an edited copy (more cameras than the
plan) does not verify and does not run.

Ed25519, not an HMAC: the node holds only the public key, so it can check a snapshot
but never make one. The key pair is derived from IVAAS_ENTITLEMENT_SEED, one per
installation (a silo has its own).

Counting never stops for an entitlement reason: past its grace period a node raises
an alert and carries on (T7.7, operational safety).
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

FORMAT = 1


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def config_sha256(config: dict[str, Any]) -> str:
    """The configuration as served, without its own entitlement block."""
    return hashlib.sha256(
        canonical({k: v for k, v in config.items() if k != "entitlement"})
    ).hexdigest()


class EntitlementSigner:
    def __init__(self, seed: str, valid: timedelta, grace: timedelta) -> None:
        self._key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed.encode()).digest())
        raw = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.public_key = base64.b64encode(raw).decode()
        self.valid, self.grace = valid, grace

    def snapshot(
        self,
        *,
        node_id: str,
        tenant_id: str,
        plan: str | None,
        limits: dict[str, int] | None,
        config: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        """The block a node's configuration carries: the snapshot, its signature, and
        the key it verifies with."""
        valid_until = now + self.valid
        body = {
            "format": FORMAT,
            "node_id": node_id,
            "tenant_id": tenant_id,
            #: none: no plan, so nothing is limited
            "plan": plan,
            "limits": limits,
            "config_sha256": config_sha256(config),
            "issued_at": now.isoformat(),
            "valid_until": valid_until.isoformat(),
            "grace_until": (valid_until + self.grace).isoformat(),
        }
        signature = base64.b64encode(self._key.sign(canonical(body))).decode()
        return {"snapshot": body, "signature": signature, "public_key": self.public_key}
