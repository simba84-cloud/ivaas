"""Composition root for the edge node.

Configured by a JSON file (IVAAS_PIPELINE_CONFIG, default /config/pipeline.json):

{
  "api_url": "http://api:8000",
  "bay_id": "<bay uuid from GET /api/v1/bays>",
  "model": {"path": "/models/stacks.onnx", "arch": "rtdetr"}   // labels come from stacks.json,
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
  "lpr_cameras": [
    {"key": "lpr-1", "api_camera_id": "<uuid>", "uri": "rtsp://mediamtx:8554/bay-poc/lpr-1"}
  ]
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
from ivaas_pipeline.adapters.onnx_rtdetr import OnnxRtDetrDetector
from ivaas_pipeline.adapters.onnx_yolo import OnnxYoloDetector
from ivaas_pipeline.adapters.opencv_source import OpenCvFrameSource
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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    path = os.environ.get("IVAAS_PIPELINE_CONFIG", "/config/pipeline.json")
    try:
        with open(path) as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        log.error("pipeline config not found at %s (see module docstring for the format)", path)
        return 2

    all_cameras = cfg["cameras"] + cfg.get("lpr_cameras", [])
    camera_ids = {c["key"]: c["api_camera_id"] for c in all_cameras}
    delivery = SpooledDelivery(
        os.environ.get("IVAAS_API_URL", cfg["api_url"]),
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
        ratio = tuple(cam.get("pitch_to_width", (0.12, 0.45)))  # per-camera calibration
        if "zone" in cam:
            counter = StackPresenceCounter(
                PresenceZone(*cam["zone"]), PeriodicityLayerCounter(pitch_to_width=ratio)
            )
        else:
            (ax, ay), (bx, by) = cam["line"]
            line = Line((ax, ay), (bx, by))
            if cfg.get("count", "stack") == "stack":
                counter = StackCrossingCounter(line, PeriodicityLayerCounter(pitch_to_width=ratio))
            else:
                counter = LineCrossingCounter(line)
        pipeline = CameraPipeline(
            source=OpenCvFrameSource(cam["key"], cam["uri"], stride=cam.get("stride", 1)),
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
                source=OpenCvFrameSource(cam["key"], cam["uri"], stride=cam.get("stride", 1)),
                reader=FastAlprPlateReader(),
                voter=PlateVoter(),
                sink=plate_sink,
            )
            t = threading.Thread(target=lpr.run, name=cam["key"], daemon=True)
            t.start()
            threads.append(t)
            log.info("started LPR pipeline for %s -> %s", cam["key"], cam["uri"])

    for t in threads:
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
