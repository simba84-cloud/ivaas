"""The node's side of the signed entitlement snapshot (proposal §5, M7 T7.7).

Every configuration the API sends carries a snapshot signed with the platform's
Ed25519 key: the plan's channel limits, a hash of that very configuration, and how
long it holds without the cloud. The node checks it against the key it pinned at
enrolment and keeps the configuration on disk with it.

A node that starts while the API is unreachable runs from that copy: normally while
the snapshot is valid, with a warning in its grace period, and with an alert once
that has passed. It never stops counting for an entitlement reason (operational
safety). What it will not run is a copy that does not verify: an edited
configuration (more cameras than the plan) breaks the hash inside the signature.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

log = logging.getLogger(__name__)

DEFAULT_CACHE_FILE = "/var/lib/ivaas/config-cache.json"
VALID, GRACE, EXPIRED = "valid", "grace", "expired"


class EntitlementError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def config_sha256(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical({k: v for k, v in config.items() if k != "entitlement"})
    ).hexdigest()


def verify(config: dict[str, Any], node_id: str, pinned_key: str | None) -> dict[str, Any]:
    """The snapshot, if the configuration carries a genuine one for this node and is
    the configuration it was signed for. Raises EntitlementError otherwise."""
    block = config.get("entitlement")
    if not isinstance(block, dict) or "snapshot" not in block:
        raise EntitlementError("the configuration carries no entitlement snapshot")
    snapshot, key = block["snapshot"], block.get("public_key")
    if pinned_key and key != pinned_key:
        raise EntitlementError("the snapshot is signed with a key other than the one enrolled")
    try:
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(key or pinned_key or ""))
        public.verify(base64.b64decode(block.get("signature", "")), _canonical(snapshot))
    except (InvalidSignature, ValueError) as exc:
        raise EntitlementError("the entitlement snapshot's signature does not verify") from exc
    if snapshot.get("node_id") != node_id:
        raise EntitlementError("the snapshot is another node's")
    if snapshot.get("config_sha256") != config_sha256(config):
        raise EntitlementError("the configuration is not the one the snapshot was signed for")
    return snapshot


def state(snapshot: dict[str, Any], now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if now < datetime.fromisoformat(snapshot["valid_until"]):
        return VALID
    if now < datetime.fromisoformat(snapshot["grace_until"]):
        return GRACE
    return EXPIRED


def report(snapshot: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any]:
    """For the heartbeat and the metrics: where the node stands, said plainly."""
    if snapshot is None:
        return {"state": "none"}
    return {
        "state": state(snapshot, now),
        "plan": snapshot.get("plan"),
        "valid_until": snapshot["valid_until"],
        "grace_until": snapshot["grace_until"],
    }


class ConfigCache:
    """The last verified configuration, on disk, readable by this node's user only."""

    def __init__(self, path: str | Path = DEFAULT_CACHE_FILE) -> None:
        self.path = Path(path)

    def save(self, config: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(config, fh)
        os.replace(tmp, self.path)  # whole or not at all: a power cut leaves the old copy

    def load(self) -> dict[str, Any] | None:
        try:
            with open(self.path) as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            log.error("the cached configuration at %s cannot be read: %s", self.path, exc)
            return None


def keep(config: dict[str, Any], node_id: str, pinned_key: str | None, cache: ConfigCache):
    """A configuration fresh from the API: kept if it verifies. -> its snapshot, or None.
    One that does not verify still runs (the API sent it) but is not kept."""
    try:
        snapshot = verify(config, node_id, pinned_key)
    except EntitlementError as exc:
        log.error("configuration not kept for offline use: %s", exc)
        return None
    cache.save(config)
    return snapshot


def fallback(node_id: str, pinned_key: str | None, cache: ConfigCache):
    """The cloud is unreachable: the kept configuration, if it verifies. -> (config,
    snapshot) or None. Expired is still run: counting does not stop."""
    config = cache.load()
    if config is None:
        return None
    try:
        snapshot = verify(config, node_id, pinned_key)
    except EntitlementError as exc:
        log.error("the kept configuration is not used: %s", exc)
        return None
    where = state(snapshot)
    if where == VALID:
        log.warning(
            "API unreachable: running the kept configuration (valid until %s)",
            snapshot["valid_until"],
        )
    elif where == GRACE:
        log.warning(
            "API unreachable: entitlement in its grace period until %s; reconnect the node",
            snapshot["grace_until"],
        )
    else:
        log.error(
            "ALERT: entitlement expired %s; counting carries on, reconnect the node",
            snapshot["grace_until"],
        )
    return config, snapshot
