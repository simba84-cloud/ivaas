"""Ordered, disk-spooled HTTP delivery to the core API. Shared by every sink.

Every item is appended to a JSONL spool *before* the HTTP attempt and removed only
after the API acknowledges it. If the edge node reboots mid-outage the spool is
replayed on the next start. While anything is queued, new items join the back of the
queue rather than overtaking it, so events reach the API in the order they happened.

With `background=True` (what the edge node runs) a sender thread does the HTTP work,
so the camera threads only ever append to the spool. A WAN outage that black-holes
packets would otherwise hold every counting thread for the full HTTP timeout on every
event, and a stalled counter skips frames: the outage would lose crates, not just
delay them.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import deque
from pathlib import Path

import httpx
from prometheus_client import Counter, Gauge

log = logging.getLogger(__name__)

# What the outage and load checks read: queued - delivered - dropped = pending, and
# after a reconnect pending must drain to zero with nothing dropped.
QUEUED = Counter("ivaas_delivery_queued", "Events accepted into the spool", ["path"])
DELIVERED = Counter("ivaas_delivery_delivered", "Events the API acknowledged", ["path"])
DROPPED = Counter(
    "ivaas_delivery_dropped", "Events discarded: rejected by the API, or spool overflow", ["reason"]
)
PENDING = Gauge("ivaas_delivery_pending", "Events waiting in the spool")


class SpooledDelivery:
    def __init__(
        self,
        api_url: str,
        api_key: str | None,
        spool_path: str | Path,
        *,
        max_spool: int = 100_000,
        client: httpx.Client | None = None,
        headers: dict[str, str] | None = None,
        background: bool = False,
        max_backoff_s: float = 30.0,
    ) -> None:
        # an enrolled node authenticates as itself; an unenrolled one with the shared key
        auth = headers if headers is not None else {"X-IVaaS-Key": api_key or ""}
        self._client = client or httpx.Client(base_url=api_url, timeout=5.0, headers=auth)
        self._spool_path = Path(spool_path)
        self._max_spool = max_spool
        self._lock = threading.Lock()
        self._queue: deque[dict] = deque(self._load())
        if self._queue:
            log.warning("replaying %d spooled items from %s", len(self._queue), spool_path)
        PENDING.set(len(self._queue))
        self._background = background
        self._max_backoff = max_backoff_s
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._sender: threading.Thread | None = None
        if background:
            self._wake.set()  # deliver anything replayed from disk straight away
            self._sender = threading.Thread(target=self._run, name="delivery", daemon=True)
            self._sender.start()

    @property
    def pending(self) -> int:
        return len(self._queue)

    def send(self, path: str, body: dict) -> None:
        # The id is fixed here, before the first attempt, and spooled with the event:
        # a resend after a crash carries the same id, so the API can skip it.
        body = body if "event_id" in body else {**body, "event_id": str(uuid.uuid4())}
        with self._lock:
            self._queue.append({"path": path, "body": body})
            QUEUED.labels(path).inc()
            while len(self._queue) > self._max_spool:
                self._queue.popleft()  # keep the newest; better than unbounded disk use
                DROPPED.labels("overflow").inc()
            self._persist()
            PENDING.set(len(self._queue))
            if not self._background:
                self.flush()
        if self._background:
            self._wake.set()

    def _run(self) -> None:
        """The sender: drain in order, back off while the API is unreachable."""
        backoff = 1.0
        while not self._stopping.is_set():
            self._wake.wait(timeout=backoff)
            self._wake.clear()
            if self._stopping.is_set():
                return
            if self._drain():
                backoff = 1.0
            elif self._queue:
                backoff = min(backoff * 2, self._max_backoff)

    def close(self, timeout: float = 5.0) -> None:
        """Stop the sender. Anything undelivered stays in the spool for the next start."""
        self._stopping.set()
        self._wake.set()
        if self._sender is not None:
            self._sender.join(timeout)

    def _drain(self) -> bool:
        """Deliver until empty or a transient failure. The HTTP call runs outside the
        lock, so `send` never waits on the network. True if the queue emptied."""
        while True:
            with self._lock:
                if not self._queue:
                    return True
                item = self._queue[0]
            if not self._post(item):
                return False
            with self._lock:
                # overflow may have dropped it while it was in flight; pop only if still first
                if self._queue and self._queue[0] is item:
                    self._queue.popleft()
                self._persist()
                PENDING.set(len(self._queue))

    def flush(self) -> int:
        """Deliver in order; stop at the first transient failure. Returns delivered count."""
        delivered = 0
        while self._queue and self._post(self._queue[0]):
            self._queue.popleft()
            delivered += 1
        PENDING.set(len(self._queue))
        if delivered:
            self._persist()
        return delivered

    def _post(self, item: dict) -> bool:
        """True if the API accepted it or definitively rejected it (nothing to retry)."""
        try:
            r = self._client.post(item["path"], json=item["body"])
        except httpx.HTTPError as exc:
            log.warning("API unreachable (%s); item spooled", type(exc).__name__)
            return False
        if r.status_code >= 500:
            log.warning("API error %s; item spooled", r.status_code)
            return False
        if r.status_code == 401:
            # Not a bad event: this node's credential was refused (revoked, or the node
            # was re-enrolled). Keep it; enrolling again replays the spool as the new node.
            log.error("API refused this node's credential; item spooled until it is enrolled again")
            return False
        if r.status_code >= 400:
            # our bug or a config error: retrying cannot help, so log loudly and drop
            log.error(
                "API rejected %s %s: %s %s", item["path"], item["body"], r.status_code, r.text[:200]
            )
            DROPPED.labels("rejected").inc()
            return True
        DELIVERED.labels(item["path"]).inc()
        return True

    def _load(self) -> list[dict]:
        try:
            with open(self._spool_path) as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.error("spool %s unreadable, starting empty: %s", self._spool_path, exc)
            return []

    def _persist(self) -> None:
        try:
            self._spool_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._spool_path.with_suffix(".tmp")
            with open(tmp, "w") as fh:
                fh.writelines(json.dumps(item) + "\n" for item in self._queue)
            tmp.replace(self._spool_path)  # atomic on POSIX
        except OSError as exc:
            log.error("cannot persist spool: %s", exc)
