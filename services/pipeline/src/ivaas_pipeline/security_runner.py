"""The security pipeline: one per watched camera.

Each frame: find people (and fire/smoke, if that model is installed), track the
people, let the rules decide, and send each finding with a snapshot as evidence. The
zones come from the API, so drawing or re-arming a zone needs no restart here.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import httpx
import numpy as np

from ivaas_pipeline.adapters.delivery import SpooledDelivery
from ivaas_pipeline.ports import Detector, FrameSource, NullMetrics, PipelineMetrics, Tracker
from ivaas_pipeline.stages.security import Finding, SecurityRules, WatchZone
from ivaas_pipeline.types import Box, Frame, Track

log = logging.getLogger(__name__)

#: labels a uniform/PPE classifier may use for "compliant"; anything else is "not"
COMPLIANT = {"ppe", "uniform", "compliant"}
MIN_JUDGE_CONFIDENCE = 0.6


@dataclass
class SecurityConfig:
    """Zones and the enrolled-face gallery, fetched from the API and refreshed."""

    zones: dict[str, list[WatchZone]] = field(default_factory=dict)  # camera key -> zones
    gallery: list[tuple[str, np.ndarray]] = field(default_factory=list)


class SecurityConfigFeed:
    def __init__(
        self,
        client: httpx.Client,
        bay_id: str,
        camera_keys: dict[str, str],  # API camera id -> pipeline key
        *,
        capabilities: tuple[str, ...] = ("people",),
        every_s: float = 30.0,
    ) -> None:
        self._client, self._bay_id, self._keys, self._every = client, bay_id, camera_keys, every_s
        # told to the API on every fetch, so the portal shows what is really running
        self._capabilities = ",".join(capabilities)
        self._lock = threading.Lock()
        self._config = SecurityConfig()

    def current(self) -> SecurityConfig:
        with self._lock:
            return self._config

    def refresh(self) -> bool:
        try:
            r = self._client.get(
                "/api/v1/pipeline/security",
                params={"bay_id": self._bay_id, "capabilities": self._capabilities},
            )
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("could not refresh security zones (%s); keeping the last ones", exc)
            return False
        zones: dict[str, list[WatchZone]] = {}
        for z in body.get("zones", []):
            key = self._keys.get(z["camera_id"])
            if key is None:
                continue
            zones.setdefault(key, []).append(
                WatchZone(
                    id=z["id"],
                    name=z["name"],
                    polygon=tuple((float(x), float(y)) for x, y in z["polygon"]),
                    rules=frozenset(z["rules"]),
                    armed=bool(z["armed"]),
                    min_dwell_s=float(z["min_dwell_s"]),
                    exclude=bool(z["exclude"]),
                )
            )
        gallery = [
            (p["name"], np.asarray(p["embedding"], dtype=np.float32))
            for p in body.get("gallery", [])
        ]
        with self._lock:
            self._config = SecurityConfig(zones, gallery)
        return True

    def run_forever(self) -> None:
        while True:
            self.refresh()
            time.sleep(self._every)


def snapshot(frame: Frame, box: Box, *, width: int = 960) -> str:
    """JPEG evidence with the person or hazard outlined, base64 for the JSON spool."""
    image = frame.image.copy()
    cv2.rectangle(image, (int(box.x1), int(box.y1)), (int(box.x2), int(box.y2)), (60, 70, 230), 3)
    h, w = image.shape[:2]
    if w > width:
        image = cv2.resize(image, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(jpeg.tobytes()).decode() if ok else ""


class HttpIncidentSink:
    def __init__(self, delivery: SpooledDelivery, bay_id: str, camera_ids: dict[str, str]) -> None:
        self._delivery, self._bay_id, self._camera_ids = delivery, bay_id, camera_ids

    def emit(self, frame: Frame, finding: Finding) -> None:
        self._delivery.send(
            "/api/v1/ingest/incidents",
            {
                "bay_id": self._bay_id,
                "camera_id": self._camera_ids[frame.camera_id],
                "zone_id": finding.zone_id,
                "kind": finding.kind,
                "confidence": round(finding.confidence, 4),
                "detected_at": frame.captured_at.isoformat(),
                "snapshot_jpeg_b64": snapshot(frame, finding.box),
                "detail": finding.detail,
            },
        )


def _crop(frame: Frame, box: Box, *, top_only: bool = False) -> np.ndarray | None:
    h, w = frame.image.shape[:2]
    x1, y1 = max(0, int(box.x1)), max(0, int(box.y1))
    x2, y2 = min(w, int(box.x2)), min(h, int(box.y2))
    if top_only:  # a face is in the top part of a standing person
        y2 = y1 + max(1, (y2 - y1) * 2 // 5)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return frame.image[y1:y2, x1:x2]


@dataclass
class SecurityPipeline:
    source: FrameSource
    people: Detector
    tracker: Tracker
    rules: SecurityRules
    config: Callable[[], SecurityConfig]
    sink: HttpIncidentSink
    hazards: Detector | None = None
    uniform: object | None = None  # OnnxImageClassifier
    faces: object | None = None  # OpenCvFaces
    metrics: PipelineMetrics = field(default_factory=NullMetrics)

    def run(self, max_frames: int | None = None) -> int:
        emitted = 0
        frames = self.source.frames()
        try:
            for n, frame in enumerate(frames, start=1):
                started = time.perf_counter()
                emitted += self.step(frame)
                self.metrics.processed(
                    frame.camera_id,
                    time.perf_counter() - started,
                    time.time() - frame.captured_at.timestamp(),
                )
                if max_frames is not None and n >= max_frames:
                    break
        finally:
            close = getattr(frames, "close", None)
            if close:
                close()
        return emitted

    def step(self, frame: Frame) -> int:
        cfg = self.config()
        zones = cfg.zones.get(frame.camera_id, [])
        if not zones:
            return 0  # nothing drawn on this camera: no detection needed
        people = [d for d in self.people.detect(frame) if d.label == "person"]
        tracks = self.tracker.update(people)
        hazards = self.hazards.detect(frame) if self.hazards else []
        h, w = frame.image.shape[:2]
        findings = self.rules.update(
            at=frame.captured_at,
            size=(w, h),
            people=tracks,
            hazards=hazards,
            zones=zones,
            in_uniform=self._uniform_judge(frame) if self.uniform else None,
            known_face=self._face_judge(frame, cfg) if self.faces and cfg.gallery else None,
        )
        for finding in findings:
            self.sink.emit(frame, finding)
        return len(findings)

    def _uniform_judge(self, frame: Frame):
        def judge(person: Track) -> tuple[bool | None, float]:
            crop = _crop(frame, person.box)
            if crop is None:
                return None, 0.0
            label, conf = self.uniform.classify(crop)  # type: ignore[union-attr]
            if conf < MIN_JUDGE_CONFIDENCE:
                return None, conf
            return label in COMPLIANT, conf

        return judge

    def _face_judge(self, frame: Frame, cfg: SecurityConfig):
        def judge(person: Track) -> tuple[bool | None, float]:
            crop = _crop(frame, person.box, top_only=True)
            if crop is None:
                return None, 0.0
            found = self.faces.faces(crop)  # type: ignore[union-attr]
            if len(found) != 1:
                return None, 0.0  # no clear face this frame: cannot tell
            name, score = self.faces.match(self.faces.embedding(crop, found[0]), cfg.gallery)  # type: ignore[union-attr]
            return name is not None, score

        return judge
