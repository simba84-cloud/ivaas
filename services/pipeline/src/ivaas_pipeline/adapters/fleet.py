"""The edge node's side of fleet management (proposal M2).

    python -m ivaas_pipeline enroll --api https://ivaas.example --token ivaas-enr-...

Enrolment trades a single-use token for this node's credential and keeps it in
IVAAS_NODE_FILE (default /var/lib/ivaas/node.json, mode 0600). From then on the node
fetches its configuration from the API, reports a heartbeat every 30 s, and restarts
itself onto a new configuration when the API says there is one.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

NODE_HEADER = "X-IVaaS-Node"
DEFAULT_NODE_FILE = "/var/lib/ivaas/node.json"
VERSION = "0.2.0"


class EnrollmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class NodeIdentity:
    api_url: str
    node_id: str
    credential: str
    name: str

    @property
    def headers(self) -> dict[str, str]:
        return {NODE_HEADER: self.credential}

    def client(self, timeout: float = 10.0) -> httpx.Client:
        return httpx.Client(base_url=self.api_url, timeout=timeout, headers=self.headers)


def enroll(
    api_url: str,
    token: str,
    path: str | Path,
    *,
    hostname: str | None = None,
    client: httpx.Client | None = None,
) -> NodeIdentity:
    """Spend the token; keep the credential where only this node's user can read it."""
    http = client or httpx.Client(base_url=api_url, timeout=10.0)
    try:
        r = http.post(
            "/api/v1/edge/enroll",
            json={"token": token, "hostname": hostname or socket.gethostname(), "version": VERSION},
        )
    except httpx.HTTPError as exc:
        raise EnrollmentError(f"could not reach {api_url}: {type(exc).__name__}") from exc
    if r.status_code != 201:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise EnrollmentError(f"enrollment refused ({r.status_code}): {detail}")
    body = r.json()
    identity = NodeIdentity(api_url, body["node_id"], body["credential"], body["name"])
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # created 0600 from the start: never world-readable, not even for a moment
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(identity.__dict__, fh)
    return identity


def load_identity(path: str | Path) -> NodeIdentity | None:
    try:
        with open(path) as fh:
            return NodeIdentity(**json.load(fh))
    except FileNotFoundError:
        return None


def fetch_config(
    client: httpx.Client, *, wait_s: float = 30.0, sleep: Callable[[float], None] = time.sleep
) -> dict:
    """The configuration the API holds for this node. Waits, saying why, until there is one:
    a node with no cameras configured must not start and count nothing."""
    while True:
        try:
            r = client.get("/api/v1/edge/config")
            if r.status_code == 401:
                raise EnrollmentError("the API refused this node's credential (revoked?)")
            r.raise_for_status()
            cfg = r.json()
            if cfg.get("configured"):
                return cfg
            log.warning("no configuration for this node yet; set it in the portal")
        except httpx.HTTPError as exc:
            log.warning("could not fetch configuration (%s); retrying", type(exc).__name__)
        sleep(wait_s)


class Heartbeat:
    """Tells the API this node is alive, what it runs, and how its cameras are doing.

    The API answers with the configuration version the node should be running; a
    different one means a new configuration, and `on_new_config` is called.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        config_version: str,
        cameras: Callable[[], dict[str, dict]],
        camera_ids: dict[str, str],
        spool_pending: Callable[[], int],
        on_new_config: Callable[[str], None],
        every_s: float = 30.0,
    ) -> None:
        self._client = client
        self._version = config_version
        self._cameras, self._ids = cameras, camera_ids
        self._pending = spool_pending
        self._on_new = on_new_config
        self._every = every_s
        self._started = time.monotonic()

    def report(self) -> dict:
        seen = self._cameras()  # one snapshot, so the two halves below agree
        return {
            "version": VERSION,
            "config_version": self._version,
            "uptime_s": round(time.monotonic() - self._started, 1),
            "spool_pending": self._pending(),
            "cameras": [
                {"api_camera_id": self._ids[key], **state}
                for key, state in seen.items()
                if key in self._ids
            ]
            # configured cameras that never produced a metric are down, not absent
            + [
                {"api_camera_id": api_id, "connected": False, "fps": 0.0, "lag_s": None}
                for key, api_id in self._ids.items()
                if key not in seen
            ],
        }

    def beat(self) -> bool:
        try:
            r = self._client.post("/api/v1/edge/heartbeat", json=self.report())
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("heartbeat not delivered (%s)", type(exc).__name__)
            return False
        wanted = r.json().get("config_version")
        if wanted and wanted != self._version:
            log.info("configuration %s replaces %s", wanted, self._version)
            self._on_new(wanted)
        return True

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.beat()
            stop.wait(self._every)


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m ivaas_pipeline enroll")
    parser.add_argument("--api", required=True, help="the IVaaS API, e.g. https://ivaas.example")
    parser.add_argument("--token", required=True, help="the enrollment token from the portal")
    parser.add_argument("--node-file", default=os.environ.get("IVAAS_NODE_FILE", DEFAULT_NODE_FILE))
    args = parser.parse_args(argv)
    try:
        identity = enroll(args.api.rstrip("/"), args.token.strip(), args.node_file)
    except EnrollmentError as exc:
        print(f"error: {exc}")
        return 1
    print(f"enrolled as {identity.name} ({identity.node_id}); credential in {args.node_file}")
    return 0
