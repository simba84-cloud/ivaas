"""Composition root for the edge node.

An enrolled node (see adapters/fleet.py; `python -m ivaas_pipeline enroll ...`)
fetches its configuration from the API, in the shape below, authenticates as itself,
reports a heartbeat, and restarts onto a new configuration when one is set.

A node that has not been enrolled is configured by a JSON file (IVAAS_PIPELINE_CONFIG,
default /config/pipeline.json) and authenticates with the shared IVAAS_API_KEY:

{
  "api_url": "http://api:8000",
  "bay_id": "<bay uuid from GET /api/v1/bays>",
  "model": {"path": "/models/stacks.onnx", "arch": "rtdetr"},  // labels come from stacks.json
  "layers_model": "/models/layers.onnx",  // learned layer counter; omit to use periodicity,
  "forward_means": "loading",
  "count": "stack",            // "stack": one crossing per stack x its layer count (default)
                               // "crate": one crossing per individually detected crate
  // per camera, EITHER a line (camera sees the stack pass a point) OR a zone (camera looks
  // into the truck and sees stacks only once inside): "zone": [x1, y1, x2, y2]
  "cameras": [
    {"key": "chokepoint-1", "api_camera_id": "<uuid>",
     "uri": "rtsp://mediamtx:8554/bay-poc/chokepoint-1",
     "line": [[960, 0], [960, 1080]], "stride": 2}
  ],
  // "frames": "latest" (default for a camera URL) processes the newest frame and skips
  // the rest when busy, so the count stays live; "all" (default for a file) queues
  // every frame in order. Either can be set per camera.
  "lpr_cameras": [
    {"key": "lpr-1", "api_camera_id": "<uuid>", "uri": "rtsp://mediamtx:8554/bay-poc/lpr-1"}
  ],
  // site security: zones and rules are drawn in the portal and fetched from the API
  "security": {
    "person_model": "/models/people-coco.onnx",           // required: finds people
    "hazard_model": "/models/fire-smoke.onnx",            // optional: fire and smoke
    "uniform_model": "/models/uniform.onnx",              // optional: in uniform or not
    "face_models": ["/models/face_detection_yunet_2023mar.onnx",
                    "/models/face_recognition_sface_2021dec.onnx"],  // optional
    "cameras": [{"key": "yard-1", "api_camera_id": "<uuid>",
                 "uri": "rtsp://mediamtx:8554/bay-poc/yard-1", "stride": 5}]
  }
}
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading

import httpx
from prometheus_client import Gauge

from ivaas_pipeline.adapters import entitlement, evidence, fleet, models
from ivaas_pipeline.adapters.delivery import SpooledDelivery
from ivaas_pipeline.adapters.http_sink import HttpCrossingSink, HttpPlateSink
from ivaas_pipeline.adapters.onnx_layers import OnnxLayerCounter
from ivaas_pipeline.adapters.onnx_rtdetr import OnnxRtDetrDetector
from ivaas_pipeline.adapters.onnx_yolo import OnnxYoloDetector
from ivaas_pipeline.adapters.opencv_source import LatestFrameSource, OpenCvFrameSource
from ivaas_pipeline.adapters.prometheus_metrics import PrometheusMetrics, serve
from ivaas_pipeline.ports import FrameSource, PipelineMetrics
from ivaas_pipeline.runner import CameraPipeline, FusingSink, LprPipeline
from ivaas_pipeline.stages.counting import Line, LineCrossingCounter
from ivaas_pipeline.stages.fusion import TimeWindowFuser
from ivaas_pipeline.stages.layers import PeriodicityLayerCounter
from ivaas_pipeline.stages.plates import PlateVoter
from ivaas_pipeline.stages.preprocess import OpenCvPreprocessor
from ivaas_pipeline.stages.presence_counting import PresenceZone, StackPresenceCounter
from ivaas_pipeline.stages.stack_counting import StackCrossingCounter
from ivaas_pipeline.stages.tracking import IouTracker

log = logging.getLogger("ivaas_pipeline")


def open_source(cam: dict, metrics: PipelineMetrics) -> FrameSource:
    live = "://" in cam["uri"]
    mode = cam.get("frames", "latest" if live else "all")
    kind = LatestFrameSource if mode == "latest" else OpenCvFrameSource
    return kind(cam["key"], cam["uri"], stride=cam.get("stride", 1), metrics=metrics)


def start_security(
    cfg, security, api_url, delivery, camera_ids, metrics, auth
) -> list[threading.Thread]:
    import httpx

    from ivaas_pipeline.security_runner import (
        HttpIncidentSink,
        SecurityConfigFeed,
        SecurityPipeline,
    )
    from ivaas_pipeline.stages.security import SecurityRules

    cams = security["cameras"]
    feed = SecurityConfigFeed(
        httpx.Client(base_url=api_url, timeout=5.0, headers=auth),
        cfg["bay_id"],
        {c["api_camera_id"]: c["key"] for c in cams},
        capabilities=tuple(
            name
            for name, key in (
                ("people", "person_model"),
                ("fire", "hazard_model"),
                ("uniform", "uniform_model"),
                ("faces", "face_models"),
            )
            if security.get(key)
        ),
    )
    feed.refresh()
    threads = [threading.Thread(target=feed.run_forever, name="security-config", daemon=True)]
    sink = HttpIncidentSink(delivery, cfg["bay_id"], camera_ids)
    for cam in cams:
        # one set of sessions per camera thread, as for counting
        hazards = uniform = faces = None
        if security.get("hazard_model"):
            hazards = OnnxRtDetrDetector(security["hazard_model"])
        if security.get("uniform_model"):
            from ivaas_pipeline.adapters.onnx_classifier import OnnxImageClassifier

            uniform = OnnxImageClassifier(security["uniform_model"])
        if security.get("face_models"):
            from ivaas_pipeline.adapters.opencv_faces import OpenCvFaces

            faces = OpenCvFaces(*security["face_models"])
        pipeline = SecurityPipeline(
            source=open_source(cam, metrics),
            people=OnnxRtDetrDetector(security["person_model"]),
            tracker=IouTracker(),
            rules=SecurityRules(),
            config=feed.current,
            sink=sink,
            hazards=hazards,
            uniform=uniform,
            faces=faces,
            metrics=metrics,
        )
        threads.append(
            threading.Thread(target=pipeline.run, name=f"security-{cam['key']}", daemon=True)
        )
        log.info(
            "security watch on %s: people%s%s%s",
            cam["key"],
            " + fire/smoke" if hazards else "",
            " + uniform" if uniform else "",
            " + faces" if faces else "",
        )
    for t in threads:
        t.start()
    return threads


# --- the signed entitlement snapshot (T7.7) -------------------------------------------
ENTITLEMENT_STATE = Gauge(
    "ivaas_entitlement_state",
    "1 for the node's entitlement state now: valid, grace, expired or none",
    ["state"],
)


def _fresh(identity, kept: entitlement.ConfigCache, ent: dict, cfg: dict) -> dict:
    """A configuration from the API: kept for offline use if its snapshot verifies."""
    snapshot = entitlement.keep(cfg, identity.node_id, identity.entitlement_public_key, kept)
    if snapshot is not None:
        ent["snapshot"] = snapshot
    return cfg


def _start_config(identity, kept: entitlement.ConfigCache, ent: dict) -> dict:
    """The API's configuration if it answers within a minute; else the kept one, so a
    node restarted in an outage counts again at once. With nothing kept (a node never
    configured), wait for the API as before: it cannot know what to count."""
    tries = int(os.environ.get("IVAAS_OFFLINE_AFTER_TRIES", "6"))
    try:
        cfg = fleet.fetch_config(identity.client(), wait_s=10, attempts=tries)
        return _fresh(identity, kept, ent, cfg)
    except fleet.Unreachable:
        found = entitlement.fallback(identity.node_id, identity.entitlement_public_key, kept)
        if found is not None:
            cfg, ent["snapshot"] = found
            return cfg
        log.warning("API unreachable and nothing kept to run: waiting for the API")
        return _fresh(identity, kept, ent, fleet.fetch_config(identity.client()))


def _renew_entitlement(identity, kept: entitlement.ConfigCache, ent: dict, stop) -> None:
    """Every 30 minutes: renew the snapshot while the API answers, so the node meets an
    outage with its full validity; say where it stands, loudly once expired."""
    every = float(os.environ.get("IVAAS_ENTITLEMENT_RENEW_S", "1800"))
    while not stop.is_set():
        try:
            _fresh(identity, kept, ent, fleet.fetch_config(identity.client(), attempts=1))
        except (fleet.Unreachable, fleet.EnrollmentError):
            pass  # offline: the kept snapshot stands; revoked: the heartbeat says so
        where = entitlement.report(ent["snapshot"])["state"]
        for name in ("valid", "grace", "expired", "none"):
            ENTITLEMENT_STATE.labels(state=name).set(1 if name == where else 0)
        if where == entitlement.GRACE:
            log.warning(
                "entitlement in its grace period until %s: reconnect the node",
                ent["snapshot"]["grace_until"],
            )
        elif where == entitlement.EXPIRED:
            log.error(
                "ALERT: entitlement expired %s; counting carries on, reconnect the node",
                ent["snapshot"]["grace_until"],
            )
        stop.wait(every)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if sys.argv[1:2] == ["enroll"]:
        return fleet.main(sys.argv[2:])

    identity = fleet.load_identity(os.environ.get("IVAAS_NODE_FILE", fleet.DEFAULT_NODE_FILE))
    kept = entitlement.ConfigCache(
        os.environ.get("IVAAS_CONFIG_CACHE", entitlement.DEFAULT_CACHE_FILE)
    )
    ent: dict = {"snapshot": None}
    if identity is not None:
        try:
            cfg = _start_config(identity, kept, ent)
        except fleet.EnrollmentError as exc:
            log.error("%s; enroll again with a new token", exc)
            return 2
        auth = identity.headers
        api_url = identity.api_url
        log.info("enrolled node %s, configuration %s", identity.name, cfg["config_version"])
    else:
        path = os.environ.get("IVAAS_PIPELINE_CONFIG", "/config/pipeline.json")
        try:
            with open(path) as fh:
                cfg = json.load(fh)
        except FileNotFoundError:
            log.error("pipeline config not found at %s (see module docstring for the format)", path)
            return 2
        auth = {"X-IVaaS-Key": os.environ["IVAAS_API_KEY"]}  # never in the config file
        api_url = os.environ.get("IVAAS_API_URL", cfg["api_url"])

    metrics = PrometheusMetrics()
    port = int(os.environ.get("IVAAS_METRICS_PORT", "9102"))
    serve(port)
    log.info("metrics on :%d/metrics", port)

    security = cfg.get("security") or {}
    all_cameras = cfg["cameras"] + cfg.get("lpr_cameras", []) + security.get("cameras", [])
    camera_ids = {c["key"]: c["api_camera_id"] for c in all_cameras}
    delivery = SpooledDelivery(
        api_url,
        None,
        os.environ.get("IVAAS_SPOOL_PATH", "/var/lib/ivaas/events.spool"),
        headers=auth,
        background=True,  # counting threads never wait on the network
    )
    crossing_sink = HttpCrossingSink(
        delivery, cfg["bay_id"], camera_ids, forward_means=cfg.get("forward_means", "loading")
    )
    # Evidence: an enrolled node near a media server cuts a clip around each count
    # from the evidence cameras' recording and uploads it (adapters/evidence.py).
    recorder = None
    playback_url = os.environ.get("IVAAS_PLAYBACK_URL")
    camera_uris = {c["key"]: c["uri"] for c in all_cameras if "://" in c.get("uri", "")}
    if identity is not None and playback_url:
        spool = os.environ.get("IVAAS_EVIDENCE_SPOOL", "/var/lib/ivaas/evidence")
        recorder = evidence.EvidenceRecorder(httpx.Client(base_url=playback_url, timeout=30), spool)
        uploader = evidence.EvidenceUploader(identity.client(timeout=120), spool, cfg["bay_id"])
        threading.Thread(
            target=evidence.run_forever,
            args=(recorder, uploader, threading.Event()),
            name="evidence",
            daemon=True,
        ).start()
        crossing_sink = evidence.EvidenceSink(
            crossing_sink, recorder, "crossing", camera_ids, camera_uris
        )
        log.info("evidence clips from %s, spooled in %s", playback_url, spool)
    sink = FusingSink(TimeWindowFuser(), crossing_sink)

    # Models: a local path, or a registered version fetched, verified and cached by
    # digest. Every model-backed part sits behind a Swappable, so a new model can be
    # switched in later without reopening a stream (adapters/models.py).
    cache = models.ModelCache(
        identity.client(timeout=120) if identity else None,
        os.environ.get("IVAAS_MODEL_CACHE", models.DEFAULT_CACHE),
    )
    arch = cfg["model"].get("arch", "rtdetr")

    def make_detector(path: str):
        if arch == "rtdetr":
            return OnnxRtDetrDetector(path)
        return OnnxYoloDetector(path, cfg["model"]["labels"])

    try:
        detector_path = cache.resolve(cfg["model"])
        layers_ref = models.layers_ref(cfg)
        layers_path = cache.resolve(layers_ref) if layers_ref else None
    except models.ModelFetchError as exc:
        log.error("cannot get this node's model: %s", exc)
        return 2
    detector_slots: list[models.Swappable] = []
    layer_slots: list[models.Swappable] = []

    threads = []
    for cam in cfg["cameras"]:
        if layers_path:
            layers = models.Swappable(OnnxLayerCounter(layers_path))
            layer_slots.append(layers)
        else:
            ratio = tuple(cam.get("pitch_to_width", (0.12, 0.45)))  # per-camera calibration
            layers = PeriodicityLayerCounter(pitch_to_width=ratio)
        if "zone" in cam:
            counter = StackPresenceCounter(PresenceZone(*cam["zone"]), layers)
        else:
            (ax, ay), (bx, by) = cam["line"]
            line = Line((ax, ay), (bx, by))
            if cfg.get("count", "stack") == "stack":
                counter = StackCrossingCounter(line, layers)
            else:
                counter = LineCrossingCounter(line)
        detector_slots.append(models.Swappable(make_detector(detector_path)))
        pipeline = CameraPipeline(
            source=open_source(cam, metrics),
            preprocessor=OpenCvPreprocessor(),
            # one session per camera thread: ONNX Runtime sessions are not shared across them
            detector=detector_slots[-1],
            tracker=IouTracker(),
            counter=counter,
            sink=sink,
            metrics=metrics,
        )
        t = threading.Thread(target=pipeline.run, name=cam["key"], daemon=True)
        t.start()
        threads.append(t)
        log.info("started pipeline for %s -> %s", cam["key"], cam["uri"])

    if cfg.get("lpr_cameras"):
        from ivaas_pipeline.adapters.fast_alpr_reader import FastAlprPlateReader

        plate_sink = HttpPlateSink(delivery, cfg["bay_id"], camera_ids)
        if recorder is not None:
            plate_sink = evidence.EvidenceSink(
                plate_sink, recorder, "plate", camera_ids, camera_uris
            )
        for cam in cfg["lpr_cameras"]:
            lpr = LprPipeline(
                source=open_source(cam, metrics),
                reader=FastAlprPlateReader(),
                voter=PlateVoter(),
                sink=plate_sink,
                metrics=metrics,
            )
            t = threading.Thread(target=lpr.run, name=cam["key"], daemon=True)
            t.start()
            threads.append(t)
            log.info("started LPR pipeline for %s -> %s", cam["key"], cam["uri"])

    if security.get("cameras"):
        threads += start_security(cfg, security, api_url, delivery, camera_ids, metrics, auth)

    if identity is None:
        for t in threads:
            t.join()
        return 0

    # An enrolled node reports in. A new model is switched in place; any other change
    # exits cleanly and the container restarts the node onto it. Events are spooled to
    # disk as they happen, so nothing counted is lost across a restart.
    stop, replaced = threading.Event(), threading.Event()
    heartbeat: fleet.Heartbeat | None = None
    applier = models.ConfigApplier(
        current=cfg,
        fetch=lambda: _fresh(identity, kept, ent, fleet.fetch_config(identity.client(), wait_s=10)),
        switcher=models.ModelSwitcher(
            cache,
            make_detector,
            OnnxLayerCounter if layers_path else None,
        ),
        detectors=detector_slots,
        layers=layer_slots,
        adopt=lambda version: heartbeat.adopt(version) if heartbeat else None,
        restart=replaced.set,
    )
    heartbeat = fleet.Heartbeat(
        identity.client(),
        config_version=cfg["config_version"],
        cameras=metrics.snapshot,
        camera_ids=camera_ids,
        spool_pending=lambda: delivery.pending,
        on_new_config=applier.on_new_version,
        status=applier.status,
        entitlement=lambda: entitlement.report(ent["snapshot"]),
    )
    threading.Thread(
        target=heartbeat.run_forever, args=(stop,), name="heartbeat", daemon=True
    ).start()
    threading.Thread(
        target=_renew_entitlement, args=(identity, kept, ent, stop), name="entitlement", daemon=True
    ).start()
    replaced.wait()
    stop.set()
    log.info("configuration changed; restarting onto it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
