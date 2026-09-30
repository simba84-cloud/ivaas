"""Evidence clips, the node's side (proposal M2): a few seconds of video around each count.

The media server keeps a rolling recording of the evidence cameras (chokepoint and
LPR). For every crossing or plate read the node asks for a window around that moment,
waits until the window is safely on disk, cuts it out through the playback server
(no re-encoding), and uploads it. Counts close together on one camera share a clip.

Clips wait in a spool directory until the API has them, like counted events do: a
WAN outage delays evidence, it does not lose it. The spool is bounded: past the limit
the oldest clip goes first, and the metrics say so.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from prometheus_client import Counter, Gauge

log = logging.getLogger(__name__)

RECORDED = Counter("ivaas_evidence_recorded", "Clips cut from the recording buffer", ["kind"])
UPLOADED = Counter("ivaas_evidence_uploaded", "Clips the API has stored")
DROPPED = Counter(
    "ivaas_evidence_dropped",
    "Clips lost: not recorded on this camera, rejected, or the spool was full",
    ["reason"],
)
SPOOLED = Gauge("ivaas_evidence_spooled_bytes", "Evidence waiting to be uploaded")


def stream_path(uri: str) -> str:
    """rtsp://mediamtx:8554/test-bay/choke-1 -> test-bay/choke-1"""
    return urlsplit(uri).path.lstrip("/")


@dataclass
class Window:
    camera_key: str
    api_camera_id: str
    path: str
    kind: str
    start: datetime
    end: datetime


class EvidenceRecorder:
    def __init__(
        self,
        playback: httpx.Client,
        spool_dir: str | Path,
        *,
        before_s: float = 3.0,
        after_s: float = 5.0,
        settle_s: float = 4.0,
        max_clip_s: float = 60.0,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._playback = playback
        self._dir = Path(spool_dir)
        self._before = timedelta(seconds=before_s)
        self._after = timedelta(seconds=after_s)
        # parts are flushed every second; wait a little longer before asking for them
        self._settle = timedelta(seconds=settle_s)
        self._max = timedelta(seconds=max_clip_s)
        self._now = clock
        self._lock = threading.Lock()
        self._open: dict[str, Window] = {}  # camera key -> the window still growing
        self._ready: list[Window] = []  # closed windows waiting to be cut
        self._missing: set[str] = set()  # cameras whose video is not being recorded

    def request(
        self, camera_key: str, api_camera_id: str, uri: str, kind: str, at: datetime
    ) -> None:
        """A count happened at `at`: make sure a clip covers it."""
        start, end = at - self._before, at + self._after
        with self._lock:
            w = self._open.get(camera_key)
            if w is not None and start <= w.end and end - w.start <= self._max:
                w.end = max(w.end, end)  # one clip for counts close together
                return
            if w is not None:
                self._ready.append(w)
            self._open[camera_key] = Window(
                camera_key, api_camera_id, stream_path(uri), kind, start, end
            )

    def due(self) -> list[Window]:
        """Windows whose video is now on disk and can be cut."""
        now = self._now()
        with self._lock:
            for key, w in list(self._open.items()):
                if w.end + self._settle <= now:
                    self._ready.append(w)
                    del self._open[key]
            ready = [w for w in self._ready if w.end + self._settle <= now]
            self._ready = [w for w in self._ready if w.end + self._settle > now]
        return ready

    def cut(self, w: Window) -> Path | None:
        """Fetch the window from the playback server into the spool. None if unavailable."""
        if w.path in self._missing:
            return None
        seconds = (w.end - w.start).total_seconds()
        try:
            r = self._playback.get(
                "/get",
                params={
                    "path": w.path,
                    "start": w.start.isoformat(),
                    "duration": f"{seconds:.1f}",
                    "format": "mp4",
                },
            )
        except httpx.HTTPError as exc:
            log.warning("playback server unreachable (%s); clip for %s lost", exc, w.camera_key)
            DROPPED.labels("unavailable").inc()
            return None
        if r.status_code == 404:
            # this camera is not recorded (not an evidence role): stop asking
            self._missing.add(w.path)
            log.info("no recording for %s; evidence is not kept for this camera", w.path)
            DROPPED.labels("not_recorded").inc()
            return None
        if r.status_code != 200 or r.content[4:8] != b"ftyp":
            log.warning("playback returned %s for %s; clip lost", r.status_code, w.path)
            DROPPED.labels("unavailable").inc()
            return None
        self._dir.mkdir(parents=True, exist_ok=True)
        clip_id = uuid.uuid4().hex
        video = self._dir / f"{clip_id}.mp4"
        video.write_bytes(r.content)
        # the metadata last: a clip without it is incomplete and is not uploaded
        (self._dir / f"{clip_id}.json").write_text(
            json.dumps(
                {
                    "api_camera_id": w.api_camera_id,
                    "kind": w.kind,
                    "started_at": w.start.isoformat(),
                    "ended_at": w.end.isoformat(),
                    "event_id": str(uuid.uuid4()),  # fixed now: a retried upload is the same
                }
            )
        )
        RECORDED.labels(w.kind).inc()
        return video


class EvidenceUploader:
    """Delivers spooled clips in order of recording; keeps them until the API has them."""

    def __init__(
        self,
        api: httpx.Client,
        spool_dir: str | Path,
        bay_id: str,
        *,
        max_bytes: int = 5 * 1024**3,
    ) -> None:
        self._api = api
        self._dir = Path(spool_dir)
        self._bay = bay_id
        self._max = max_bytes

    def pending(self) -> list[Path]:
        if not self._dir.is_dir():
            return []
        return sorted((p for p in self._dir.glob("*.json")), key=lambda p: p.stat().st_mtime)

    def enforce_limit(self) -> None:
        """Past the limit the oldest clip goes: newer evidence is worth more."""
        clips = self.pending()
        total = sum(
            p.with_suffix(".mp4").stat().st_size for p in clips if p.with_suffix(".mp4").exists()
        )
        while clips and total > self._max:
            oldest = clips.pop(0)
            size = oldest.with_suffix(".mp4").stat().st_size
            oldest.with_suffix(".mp4").unlink(missing_ok=True)
            oldest.unlink(missing_ok=True)
            total -= size
            DROPPED.labels("spool_full").inc()
            log.error("evidence spool over %d bytes; dropped the oldest clip", self._max)
        SPOOLED.set(total)

    def upload_one(self, meta_path: Path) -> bool:
        """True if the clip is finished with (stored, or rejected for good)."""
        video = meta_path.with_suffix(".mp4")
        meta = json.loads(meta_path.read_text())
        try:
            with open(video, "rb") as fh:
                r = self._api.post(
                    "/api/v1/ingest/evidence",
                    data={"bay_id": self._bay, "camera_id": meta.pop("api_camera_id"), **meta},
                    files={"file": ("clip.mp4", fh, "video/mp4")},
                )
        except httpx.HTTPError as exc:
            log.warning("evidence upload failed (%s); kept for later", type(exc).__name__)
            return False
        if r.status_code >= 500 or r.status_code == 401:
            return False  # the API or this node's credential: retry later, keep the clip
        if r.status_code >= 400:
            log.error("API rejected evidence %s: %s %s", video.name, r.status_code, r.text[:200])
            DROPPED.labels("rejected").inc()
        else:
            UPLOADED.inc()
        video.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return True

    def drain(self) -> bool:
        """Upload in order until empty or a failure. True if the spool emptied."""
        self.enforce_limit()
        for meta in self.pending():
            if not self.upload_one(meta):
                return False
        SPOOLED.set(0)
        return True


def run_forever(
    recorder: EvidenceRecorder,
    uploader: EvidenceUploader,
    stop: threading.Event,
    *,
    every_s: float = 2.0,
    max_backoff_s: float = 60.0,
) -> None:
    """Cut what is due, then upload; back off while the API is unreachable."""
    backoff = every_s
    while not stop.is_set():
        for window in recorder.due():
            recorder.cut(window)
        backoff = every_s if uploader.drain() else min(backoff * 2, max_backoff_s)
        stop.wait(backoff)


class EvidenceSink:
    """Sink decorator: every event it passes on also asks for a clip of that moment.

    Asking is a few microseconds under a lock; the cutting and uploading happen on
    the evidence thread. A failure here must never cost a count, so it is contained.
    """

    def __init__(
        self,
        downstream,
        recorder: EvidenceRecorder,
        kind: str,
        camera_ids: dict[str, str],
        camera_uris: dict[str, str],
    ) -> None:
        self._downstream = downstream
        self._recorder = recorder
        self._kind = kind
        self._ids, self._uris = camera_ids, camera_uris

    def emit(self, event) -> None:
        key = event.camera_id
        try:
            if key in self._uris:
                self._recorder.request(key, self._ids[key], self._uris[key], self._kind, event.at)
        except Exception:
            log.exception("could not request evidence for %s", key)
        self._downstream.emit(event)
