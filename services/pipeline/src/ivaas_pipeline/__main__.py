"""Composition root for the edge node.

Configured by a JSON file (IVAAS_PIPELINE_CONFIG, default /config/pipeline.json):

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


def start_security(cfg, security, api_url, delivery, camera_ids, metrics) -> list[threading.Thread]:
    import httpx

    from ivaas_pipeline.security_runner import (
        HttpIncidentSink,
        SecurityConfigFeed,
        SecurityPipeline,
    )
    from ivaas_pipeline.stages.security import SecurityRules

    cams = security["cameras"]
    feed = SecurityConfigFeed(
        httpx.Client(
            base_url=api_url, timeout=5.0, headers={"X-IVaaS-Key": os.environ["IVAAS_API_KEY"]}
        ),
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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    path = os.environ.get("IVAAS_PIPELINE_CONFIG", "/config/pipeline.json")
    try:
        with open(path) as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        log.error("pipeline config not found at %s (see module docstring for the format)", path)
        return 2

    metrics = PrometheusMetrics()
    port = int(os.environ.get("IVAAS_METRICS_PORT", "9102"))
    serve(port)
    log.info("metrics on :%d/metrics", port)

    security = cfg.get("security") or {}
    all_cameras = cfg["cameras"] + cfg.get("lpr_cameras", []) + security.get("cameras", [])
    camera_ids = {c["key"]: c["api_camera_id"] for c in all_cameras}
    api_url = os.environ.get("IVAAS_API_URL", cfg["api_url"])
    delivery = SpooledDelivery(
        api_url,
        os.environ["IVAAS_API_KEY"],  # never in the config file
        os.environ.get("IVAAS_SPOOL_PATH", "/var/lib/ivaas/events.spool"),
    )
    sink = FusingSink(
        TimeWindowFuser(),
        HttpCrossingSink(
            delivery, cfg["bay_id"], camera_ids, forward_means=cfg.get("forward_means", "loading")
        ),
    )

    threads = []
    for cam in cfg["cameras"]:
        if cfg.get("layers_model"):
            layers = OnnxLayerCounter(cfg["layers_model"])
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
        pipeline = CameraPipeline(
            source=open_source(cam, metrics),
            preprocessor=OpenCvPreprocessor(),
            # one session per camera thread: ONNX Runtime sessions are not shared across them
            detector=(
                OnnxRtDetrDetector(cfg["model"]["path"])
                if cfg["model"].get("arch", "rtdetr") == "rtdetr"
                else OnnxYoloDetector(cfg["model"]["path"], cfg["model"]["labels"])
            ),
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
        threads += start_security(cfg, security, api_url, delivery, camera_ids, metrics)

    for t in threads:
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
