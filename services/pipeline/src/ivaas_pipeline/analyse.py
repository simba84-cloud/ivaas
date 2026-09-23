"""Offline analysis of one video file: everything the live pipeline does, plus a
timeline and a captured frame per counted stack, for a report.

Used by the API's job worker (through the VideoAnalyser port) and runnable alone:

    uv run python -m ivaas_pipeline.analyse video.mp4 out/ --model ../../models/stacks-v2.onnx ...
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from ivaas_pipeline.adapters.onnx_layers import OnnxLayerCounter
from ivaas_pipeline.adapters.onnx_rtdetr import OnnxRtDetrDetector
from ivaas_pipeline.stages.layers import PeriodicityLayerCounter
from ivaas_pipeline.stages.plates import PlateVoter
from ivaas_pipeline.stages.settle_counting import StackSettleCounter
from ivaas_pipeline.stages.tracking import IouTracker
from ivaas_pipeline.types import Frame

log = logging.getLogger(__name__)
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass
class Load:
    start_s: float
    end_s: float
    stacks: int = 0
    crates: int = 0
    low_confidence: int = 0
    plate: str | None = None


@dataclass
class Event:
    at_s: float
    kind: str
    detail: str
    frame_key: str | None = None


@dataclass
class AnalysisResult:
    duration_s: float
    loads: list[Load] = field(default_factory=list)
    timeline: list[Event] = field(default_factory=list)


SaveFrame = Callable[[str, np.ndarray], str]  # (suggested name, BGR image) -> stored key


def annotate(image: np.ndarray, box, label: str) -> np.ndarray:
    out = image.copy()
    x1, y1, x2, y2 = (int(v) for v in (box.x1, box.y1, box.x2, box.y2))
    cv2.rectangle(out, (x1, y1), (x2, y2), (125, 24, 200), 6)
    cv2.putText(
        out, label, (x1 + 8, max(40, y1 - 12)), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 4
    )
    cv2.putText(
        out, label, (x1 + 8, max(40, y1 - 12)), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (125, 24, 200), 2
    )
    return out


def analyse_video(
    path: str,
    *,
    detector: OnnxRtDetrDetector,
    layers,
    plate_reader=None,
    save_frame: SaveFrame | None = None,
    stride: int = 2,
    idle_seconds: float = 120.0,
    progress: Callable[[float], None] | None = None,
) -> AnalysisResult:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    tracker = IouTracker(0.2, int(fps * 1.5))
    counter = StackSettleCounter(layers, settle_seconds=3.0)
    voter = PlateVoter() if plate_reader else None

    result = AnalysisResult(duration_s=total / fps)
    loads: list[Load] = []
    i = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        i += 1
        if i % stride:
            continue
        t = i / fps
        frame = Frame("upload", img, EPOCH + timedelta(seconds=t))
        tracks = tracker.update(detector.detect(frame))

        for c in counter.update(frame, tracks):
            if loads and t - loads[-1].end_s <= idle_seconds:
                load = loads[-1]
            else:
                load = Load(start_s=t, end_s=t)
                loads.append(load)
                result.timeline.append(Event(t, "load_started", f"Load {len(loads)} started"))
            load.end_s, load.stacks, load.crates = t, load.stacks + 1, load.crates + c.crates
            load.low_confidence += c.confidence == 0.0
            key = None
            if save_frame is not None:
                track = next((tr for tr in tracks if tr.track_id == c.track_id), None)
                if track is not None:
                    key = save_frame(
                        f"stack-{len(result.timeline):04d}-{int(t)}s.jpg",
                        annotate(img, track.box, f"{c.crates} crates"),
                    )
            result.timeline.append(Event(t, "stack_counted", f"Stack of {c.crates} crates", key))

        if voter is not None and i % (stride * 5) == 0:  # plates need fewer frames
            for ev in voter.update(frame, plate_reader.read(frame)):
                result.timeline.append(
                    Event(t, "plate_read", f"Plate {ev.plate} ({ev.reads} reads)")
                )
                if loads and t - loads[-1].end_s <= idle_seconds and loads[-1].plate is None:
                    loads[-1].plate = ev.plate
                elif not loads or t - loads[-1].end_s > idle_seconds:
                    loads.append(Load(start_s=t, end_s=t, plate=ev.plate))
                    result.timeline.append(
                        Event(t, "load_started", f"Load {len(loads)} started ({ev.plate})")
                    )

        if progress is not None and total and i % (stride * 50) == 0:
            progress(i / total)
    cap.release()
    for n, load in enumerate(loads, start=1):
        result.timeline.append(
            Event(
                load.end_s,
                "load_ended",
                f"Load {n} ended: {load.stacks} stacks, {load.crates} crates",
            )
        )
    result.timeline.sort(key=lambda e: e.at_s)
    result.loads = loads
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("out", type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--layers-model")
    ap.add_argument("--lpr", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    a.out.mkdir(parents=True, exist_ok=True)

    def save(name: str, img: np.ndarray) -> str:
        cv2.imwrite(str(a.out / name), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return name

    reader = None
    if a.lpr:
        from ivaas_pipeline.adapters.fast_alpr_reader import FastAlprPlateReader

        reader = FastAlprPlateReader()
    res = analyse_video(
        a.video,
        detector=OnnxRtDetrDetector(a.model, threshold=0.2),
        layers=OnnxLayerCounter(a.layers_model) if a.layers_model else PeriodicityLayerCounter(),
        plate_reader=reader,
        save_frame=save,
        progress=lambda p: print(f"  {p:.0%}", flush=True),
    )
    (a.out / "analysis.json").write_text(json.dumps(asdict(res), indent=1))
    print(
        f"{len(res.loads)} loads, {sum(ld.crates for ld in res.loads)} crates, {len(res.timeline)} events -> {a.out}"
    )


if __name__ == "__main__":
    main()
