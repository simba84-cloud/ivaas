"""The stream simulator's control side (proposal M2, T2.3): stand up a bay's worth of
cameras from recorded footage, before the real cameras exist.

    python -m ivaas_pipeline.sim setup --api http://api:8000 --bay <bay id> \\
        --node <node id> --out /sim/streams.txt
    python -m ivaas_pipeline.sim teardown --api http://api:8000 --bay <bay id>

`setup` registers the POC's camera array (16 volumetric + 1 LPR, section 4.1 of the
scope) as push cameras named "Sim ...", writes their stream paths for the publisher
(deploy/simulator/publish.sh), and, given an enrolled node, sets that node's
configuration to process every one of them. `teardown` removes the "Sim" cameras.

Sign-in comes from IVAAS_ADMIN_USER / IVAAS_ADMIN_PASSWORD (a tenant admin), so no
password appears on a command line.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

PREFIX = "Sim "
#: the scope's camera array: 4 overhead, 4 side-high, 4 side-mid, 2 side-low, 2 chokepoint
VOLUMETRIC = [
    ("overhead", 4),
    ("side_high", 4),
    ("side_mid", 4),
    ("side_low", 2),
    ("chokepoint", 2),
]


def camera_plan(volumetric: int, lpr: int) -> list[tuple[str, str]]:
    """-> [(name, role)]: the scope's layout, cut or cycled to `volumetric` cameras."""
    roles = [role for role, n in VOLUMETRIC for _ in range(n)]
    plan = []
    for i in range(volumetric):
        role = roles[i % len(roles)]
        plan.append((f"{PREFIX}{role.replace('_', ' ')} {i + 1:02d}", role))
    plan += [(f"{PREFIX}lpr {i + 1:02d}", "lpr") for i in range(lpr)]
    return plan


def node_config(cameras: list[dict], *, model: str, layers: str | None, stride: int) -> dict:
    """Every volumetric camera counts in a full-frame zone; the LPR cameras read plates.
    For load testing the point is that every stream is decoded and inferred on."""
    counting = [c for c in cameras if c["role"] != "lpr"]
    plates = [c for c in cameras if c["role"] == "lpr"]
    cfg: dict = {
        "model": {"path": model, "arch": "rtdetr"},
        "cameras": [
            {"api_camera_id": c["id"], "zone": [0, 0, 100000, 100000], "stride": stride}
            for c in counting
        ],
        "lpr_cameras": [{"api_camera_id": c["id"], "stride": max(stride, 5)} for c in plates],
    }
    if layers:
        cfg["layers_model"] = layers
    return cfg


def _signed_in(api: str) -> httpx.Client:
    user, password = os.environ.get("IVAAS_ADMIN_USER"), os.environ.get("IVAAS_ADMIN_PASSWORD")
    if not user or not password:
        raise SystemExit("set IVAAS_ADMIN_USER and IVAAS_ADMIN_PASSWORD (a tenant admin)")
    client = httpx.Client(base_url=api, timeout=15.0)
    r = client.post("/api/v1/auth/login", json={"username": user, "password": password})
    if r.status_code != 200:
        raise SystemExit(f"sign-in failed: {r.status_code} {r.text[:200]}")
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return client


def _check(r: httpx.Response, what: str) -> dict | list:
    if r.status_code >= 400:
        raise SystemExit(f"{what} failed: {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


def setup(client: httpx.Client, args: argparse.Namespace) -> list[dict]:
    existing = {
        c["name"]: c
        for c in _check(client.get(f"/api/v1/bays/{args.bay}/cameras"), "listing cameras")
    }
    cameras = []
    for name, role in camera_plan(args.cameras, args.lpr):
        cam = existing.get(name) or _check(
            client.post(
                f"/api/v1/bays/{args.bay}/cameras",
                json={"name": name, "role": role, "source_url": None},  # the simulator pushes
            ),
            f"registering {name}",
        )
        cameras.append(cam)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(c["stream_path"] + "\n" for c in cameras))
    print(f"{len(cameras)} simulated cameras; stream paths in {args.out}")
    if args.node:
        cfg = node_config(cameras, model=args.model, layers=args.layers, stride=args.stride)
        out = _check(
            client.put(f"/api/v1/edge/nodes/{args.node}/config", json=cfg), "configuring the node"
        )
        print(f"node {args.node} now runs configuration {out['config_version']}")
    return cameras


def teardown(client: httpx.Client, args: argparse.Namespace) -> int:
    removed = 0
    for cam in _check(client.get(f"/api/v1/bays/{args.bay}/cameras"), "listing cameras"):
        if cam["name"].startswith(PREFIX):
            _check(client.delete(f"/api/v1/cameras/{cam['id']}"), f"removing {cam['name']}")
            removed += 1
    print(f"removed {removed} simulated cameras")
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ivaas_pipeline.sim")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "teardown"):
        p = sub.add_parser(name)
        p.add_argument("--api", required=True)
        p.add_argument("--bay", required=True, help="the bay to add the cameras to")
    s = sub.choices["setup"]
    s.add_argument("--cameras", type=int, default=16, help="volumetric cameras (scope: 16)")
    s.add_argument("--lpr", type=int, default=1, help="plate cameras (scope: 1)")
    s.add_argument("--node", help="an enrolled node to configure for every simulated camera")
    s.add_argument("--model", default="/models/stacks-v2.onnx")
    s.add_argument("--layers", default="/models/layers-v3.onnx")
    s.add_argument("--stride", type=int, default=1, help="process every n-th frame")
    s.add_argument("--out", default="/sim/streams.txt")
    args = parser.parse_args(argv)
    client = _signed_in(args.api.rstrip("/"))
    if args.command == "setup":
        setup(client, args)
    else:
        teardown(client, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
