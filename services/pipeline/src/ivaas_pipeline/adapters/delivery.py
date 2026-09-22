"""Ordered, disk-spooled HTTP delivery to the core API. Shared by every sink.

Every item is appended to a JSONL spool *before* the HTTP attempt and removed only
after the API acknowledges it. If the edge node reboots mid-outage the spool is
replayed on the next start. While anything is queued, new items join the back of the
queue rather than overtaking it, so events reach the API in the order they happened.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


class SpooledDelivery:
    def __init__(
        self,
        api_url: str,
        api_key: str,
        spool_path: str | Path,
        *,
        max_spool: int = 100_000,
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=api_url, timeout=5.0, headers={"X-IVaaS-Key": api_key}
        )
        self._spool_path = Path(spool_path)
        self._max_spool = max_spool
        self._lock = threading.Lock()
        self._queue: deque[dict] = deque(self._load())
        if self._queue:
            log.warning("replaying %d spooled items from %s", len(self._queue), spool_path)

    @property
    def pending(self) -> int:
        return len(self._queue)

    def send(self, path: str, body: dict) -> None:
        with self._lock:
            self._queue.append({"path": path, "body": body})
            while len(self._queue) > self._max_spool:
                self._queue.popleft()  # keep the newest; better than unbounded disk use
            self._persist()
            self.flush()

    def flush(self) -> int:
        """Deliver in order; stop at the first transient failure. Returns delivered count."""
        delivered = 0
        while self._queue and self._post(self._queue[0]):
            self._queue.popleft()
            delivered += 1
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
        if r.status_code >= 400:
            # our bug or a config error: retrying cannot help, so log loudly and drop
            log.error(
                "API rejected %s %s: %s %s", item["path"], item["body"], r.status_code, r.text[:200]
            )
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
