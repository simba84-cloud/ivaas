"""M7, T7.7: a node keeps counting through an outage on its signed snapshot.

Snapshots here are signed the way the API signs them (services/api's
adapters/entitlement_signing.py); the API's tests check its real output against
this module's verifier.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import stat
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ivaas_pipeline.adapters import entitlement, fleet

KEY = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
NODE = "node-1"
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def _canonical(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str).encode()


def served(cameras=1, issued=T0, key=KEY, public=PUBLIC):
    """A configuration as the API serves it, signed."""
    cfg = {
        "configured": True,
        "config_version": "v1",
        "cameras": [{"api_camera_id": f"cam-{i}", "uri": f"rtsp://m/{i}"} for i in range(cameras)],
        "lpr_cameras": [],
        "not_entitled": [],
    }
    snap = {
        "format": 1,
        "node_id": NODE,
        "tenant_id": "t",
        "plan": "standard",
        "limits": {"od_channels": 16, "lpr_channels": 1},
        "config_sha256": hashlib.sha256(_canonical(cfg)).hexdigest(),
        "issued_at": issued.isoformat(),
        "valid_until": (issued + timedelta(days=7)).isoformat(),
        "grace_until": (issued + timedelta(days=14)).isoformat(),
    }
    sig = base64.b64encode(key.sign(_canonical(snap))).decode()
    return {**cfg, "entitlement": {"snapshot": snap, "signature": sig, "public_key": public}}


def test_t7_7_offline_for_seven_days_the_node_keeps_counting_then_alerts_but_never_stops(
    tmp_path, caplog
):
    cache = entitlement.ConfigCache(tmp_path / "config-cache.json")
    assert entitlement.keep(served(), NODE, PUBLIC, cache) is not None
    assert stat.S_IMODE((tmp_path / "config-cache.json").stat().st_mode) == 0o600

    found = entitlement.fallback(NODE, PUBLIC, cache)  # restarted in an outage
    assert found is not None
    cfg, snap = found
    assert cfg["cameras"][0]["api_camera_id"] == "cam-0"  # counting, on the kept config
    assert entitlement.state(snap, T0 + timedelta(days=6, hours=23)) == "valid"
    assert entitlement.state(snap, T0 + timedelta(days=7)) == "grace"
    late = T0 + timedelta(days=30)
    assert entitlement.state(snap, late) == "expired"
    assert entitlement.report(snap, late)["state"] == "expired"
    # long past grace: still returned to run, with the alert said
    expired = served(issued=datetime.now(UTC) - timedelta(days=30))
    entitlement.keep(expired, NODE, PUBLIC, cache)
    with caplog.at_level(logging.ERROR):
        assert entitlement.fallback(NODE, PUBLIC, cache) is not None
    assert "ALERT: entitlement expired" in caplog.text and "counting carries on" in caplog.text


def test_a_kept_configuration_edited_to_run_more_is_not_run(tmp_path):
    cache = entitlement.ConfigCache(tmp_path / "c.json")
    entitlement.keep(served(), NODE, PUBLIC, cache)
    data = json.loads((tmp_path / "c.json").read_text())
    data["cameras"].append({"api_camera_id": "cam-extra", "uri": "rtsp://m/x"})
    (tmp_path / "c.json").write_text(json.dumps(data))
    assert entitlement.fallback(NODE, PUBLIC, cache) is None


def test_a_snapshot_signed_by_anyone_else_is_neither_kept_nor_run(tmp_path):
    cache = entitlement.ConfigCache(tmp_path / "c.json")
    forger = Ed25519PrivateKey.generate()
    forged_pub = base64.b64encode(
        forger.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    # forged, but carrying its own key: the node checks against the key it pinned
    assert entitlement.keep(served(key=forger, public=forged_pub), NODE, PUBLIC, cache) is None
    assert not (tmp_path / "c.json").exists()
    # forged and claiming the platform's key: the signature does not verify
    assert entitlement.keep(served(key=forger), NODE, PUBLIC, cache) is None


def test_a_node_enrolled_before_snapshots_takes_the_key_its_config_carries(tmp_path):
    cache = entitlement.ConfigCache(tmp_path / "c.json")
    assert entitlement.keep(served(), NODE, None, cache) is not None


def test_fetching_gives_up_after_its_tries_so_the_kept_config_can_run():
    slept = []

    def down(request):
        raise httpx.ConnectError("no route")

    client = httpx.Client(transport=httpx.MockTransport(down), base_url="http://api")
    with pytest.raises(fleet.Unreachable):
        fleet.fetch_config(client, wait_s=10, attempts=3, sleep=slept.append)
    assert slept == [10, 10]  # three tries, two waits between them


def test_a_restart_in_an_outage_runs_the_kept_config_and_a_new_node_still_waits(
    tmp_path, monkeypatch
):
    from ivaas_pipeline import __main__ as node_main

    identity = fleet.NodeIdentity("http://api", NODE, "cred", "edge", PUBLIC)
    cache = entitlement.ConfigCache(tmp_path / "c.json")

    def unreachable(client, **kw):
        if kw.get("attempts"):
            raise fleet.Unreachable("down")
        raise AssertionError("waited for the API although a config was kept")

    monkeypatch.setattr(node_main.fleet, "fetch_config", unreachable)
    entitlement.keep(served(), NODE, PUBLIC, cache)
    ent: dict = {"snapshot": None}
    cfg = node_main._start_config(identity, cache, ent)
    assert cfg["config_version"] == "v1" and ent["snapshot"]["plan"] == "standard"

    # never configured: nothing to run, so it waits for the API as before
    empty = entitlement.ConfigCache(tmp_path / "none.json")
    calls = []

    def later(client, **kw):
        calls.append(kw)
        if kw.get("attempts"):
            raise fleet.Unreachable("down")
        return served()

    monkeypatch.setattr(node_main.fleet, "fetch_config", later)
    assert node_main._start_config(identity, empty, {"snapshot": None})["configured"]
    assert calls[-1].get("attempts") is None  # the unbounded wait


def test_the_heartbeat_says_where_the_entitlement_stands():
    snap = served()["entitlement"]["snapshot"]
    beat = fleet.Heartbeat(
        httpx.Client(base_url="http://api"),
        config_version="v1",
        cameras=dict,
        camera_ids={},
        spool_pending=lambda: 0,
        on_new_config=lambda v: None,
        entitlement=lambda: entitlement.report(snap, T0),
    )
    assert beat.report()["entitlement"] == {
        "state": "valid",
        "plan": "standard",
        "valid_until": snap["valid_until"],
        "grace_until": snap["grace_until"],
    }
