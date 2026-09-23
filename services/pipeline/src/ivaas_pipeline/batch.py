"""Run the counting pipeline over recorded footage and write a per-load report.

    uv run python -m ivaas_pipeline.batch config.json out.json

Config is the same shape as the live pipeline's, plus per camera a list of clips.
A "load" is one continuous run of stack activity inside the zone; loads are split
when the zone has been empty for `idle_seconds`. This is the offline stand-in for
the LPR-driven session boundaries the live system uses.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2

from ivaas_pipeline.adapters.onnx_layers import OnnxLayerCounter
from ivaas_pipeline.adapters.onnx_rtdetr import OnnxRtDetrDetector
from ivaas_pipeline.stages.layers import PeriodicityLayerCounter
from ivaas_pipeline.stages.presence_counting import PresenceZone, StackPresenceCounter
from ivaas_pipeline.stages.tracking import IouTracker
from ivaas_pipeline.types import Crossing, Frame

log = logging.getLogger("batch")


def run_clip(
    clip: Path,
    *,
    detector: OnnxRtDetrDetector,
    layers,
    zone: tuple[float, float, float, float],
    stride: int = 2,
    min_seconds: float = 3.0,
    window: tuple[float, float] | None = None,
) -> tuple[list[Crossing], dict]:
    cap = cv2.VideoCapture(str(clip))
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    start_s, end_s = window or (0.0, float("inf"))
    if start_s:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_s * fps))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    zx1, zy1, zx2, zy2 = zone
    counter = StackPresenceCounter(
        PresenceZone(zx1 * W, zy1 * H, zx2 * W, zy2 * H), layers, min_seconds=min_seconds
    )
    tracker = IouTracker(0.2, int(fps * 1.5))
    base = datetime(2000, 1, 1, tzinfo=UTC)
    crossings: list[Crossing] = []
    i, started = int(start_s * fps), time.time()
    while i < end_s * fps:
        ok, img = cap.read()
        if not ok:
            break
        i += 1
        if i % stride:
            continue
        f = Frame(clip.stem, img, base + timedelta(seconds=i / fps))
        crossings += counter.update(f, tracker.update(detector.detect(f)))
    cap.release()
    return crossings, {
        "window": [start_s, min(end_s, i / fps)],
        "seconds": i / fps - start_s,
        "wall": time.time() - started,
    }


def clip_entries(entries: list) -> list[tuple[Path, tuple[float, float] | None]]:
    """A clip entry is a path string, or {"path": ..., "window": [start_s, end_s]}."""
    out = []
    for e in entries:
        if isinstance(e, dict):
            out.append((Path(e["path"]), tuple(e["window"]) if "window" in e else None))
        else:
            out.append((Path(e), None))
    return out


def group_loads(crossings: list[Crossing], idle_seconds: float) -> list[dict]:
    loads: list[dict] = []
    for c in sorted(crossings, key=lambda c: c.at):
        t = (c.at - datetime(2000, 1, 1, tzinfo=UTC)).total_seconds()
        if loads and t - loads[-1]["end_s"] <= idle_seconds:
            cur = loads[-1]
        else:
            cur = {"start_s": t, "end_s": t, "stacks": 0, "crates": 0, "low_confidence": 0}
            loads.append(cur)
        cur["end_s"] = t
        cur["stacks"] += 1
        cur["crates"] += c.crates
        cur["low_confidence"] += c.confidence == 0.0
    return loads


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument(
        "--idle", type=float, default=120.0, help="seconds of no stacks that end a load"
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = json.loads(args.config.read_text())

    detector = OnnxRtDetrDetector(
        cfg["model"]["path"], threshold=cfg["model"].get("threshold", 0.2)
    )
    layers = (
        OnnxLayerCounter(cfg["layers_model"])
        if cfg.get("layers_model")
        else PeriodicityLayerCounter()
    )

    report = []
    for cam in cfg["cameras"]:
        for clip, window in clip_entries(cam["clips"]):
            log.info("running %s %s", clip.name, window or "")
            crossings, meta = run_clip(
                clip,
                detector=detector,
                layers=layers,
                zone=cam["zone"],
                stride=cam.get("stride", 2),
                window=window,
            )
            loads = group_loads(crossings, args.idle)
            log.info(
                "  %.0f s of video in %.0f s: %d stacks in %d load(s)",
                meta["seconds"],
                meta["wall"],
                len(crossings),
                len(loads),
            )
            report.append({"camera": cam["key"], "clip": clip.name, **meta, "loads": loads})
            args.out.write_text(json.dumps(report, indent=1))  # progress survives a crash
    total = sum(ld["crates"] for r in report for ld in r["loads"])
    print(f"\n{sum(len(r['loads']) for r in report)} loads, {total} crates -> {args.out}")


if __name__ == "__main__":
    main()
