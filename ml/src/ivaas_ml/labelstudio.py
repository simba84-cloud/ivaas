"""Label Studio interchange: build import tasks, read back finished annotations.

# 1. tasks for import (optionally seeded with model predictions to correct)
uv run python -m ivaas_ml.labelstudio tasks data/frames data/tasks.json [--predictions data/prelabels.json]
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

LABELS = ("stack",)  # one single-column stack of crates; layers are counted downstream

LABEL_CONFIG = """<View>
  <Header value="Box each single-column STACK of crates (not individual crates). Skip dense yard masses you cannot separate."/>
  <Image name="image" value="$image" zoom="true" zoomControl="true" rotateControl="false"/>
  <RectangleLabels name="label" toName="image" strokeWidth="3" opacity="0.12">
    <Label value="stack" background="#c8187d"/>
  </RectangleLabels>
</View>"""

# Label Studio serves files mounted at /label-studio/files through this URL prefix.
LOCAL_FILES_PREFIX = "/data/local-files/?d=frames/"


@dataclass(frozen=True)
class BoxPx:
    """Axis-aligned box in pixels."""

    label: str
    x: float
    y: float
    w: float
    h: float
    score: float | None = None


def to_ls_result(box: BoxPx, width: int, height: int, source: str = "label") -> dict:
    """Label Studio stores boxes as percentages of the image, not pixels."""
    return {
        "from_name": source,
        "to_name": "image",
        "type": "rectanglelabels",
        "original_width": width,
        "original_height": height,
        "value": {
            "x": box.x / width * 100,
            "y": box.y / height * 100,
            "width": box.w / width * 100,
            "height": box.h / height * 100,
            "rotation": 0,
            "rectanglelabels": [box.label],
        },
        **({"score": box.score} if box.score is not None else {}),
    }


def from_ls_result(result: dict) -> BoxPx | None:
    if result.get("type") != "rectanglelabels":
        return None
    v, w, h = result["value"], result["original_width"], result["original_height"]
    labels = v.get("rectanglelabels") or []
    if not labels:
        return None
    # clamp: annotators routinely drag a box slightly past the image edge
    x0, y0 = max(0.0, v["x"]), max(0.0, v["y"])
    x1, y1 = min(100.0, v["x"] + v["width"]), min(100.0, v["y"] + v["height"])
    if x1 <= x0 or y1 <= y0:
        return None
    return BoxPx(labels[0], x0 / 100 * w, y0 / 100 * h, (x1 - x0) / 100 * w, (y1 - y0) / 100 * h)


def build_tasks(frames_dir: Path, predictions: dict[str, dict] | None = None) -> list[dict]:
    """One task per frame in manifest.csv, carrying camera/clip so the split can use them."""
    tasks = []
    with open(frames_dir / "manifest.csv") as fh:
        for row in csv.DictReader(fh):
            task: dict = {
                "data": {
                    "image": LOCAL_FILES_PREFIX + row["file"],
                    "file": row["file"],
                    "camera": row["camera"],
                    "clip": row["clip"],
                }
            }
            pred = (predictions or {}).get(row["file"])
            if pred and pred["boxes"]:
                boxes = [BoxPx(**b) for b in pred["boxes"]]
                task["predictions"] = [
                    {
                        "model_version": pred.get("model", "prelabel"),
                        "score": sum(b.score or 0 for b in boxes) / len(boxes),
                        "result": [to_ls_result(b, pred["width"], pred["height"]) for b in boxes],
                    }
                ]
            tasks.append(task)
    return tasks


def build_predictions(prelabels: dict[str, dict], task_ids: dict[str, int]) -> list[dict]:
    """Payloads for POST /api/predictions, for tasks that already exist in a project."""
    out = []
    for file, pred in prelabels.items():
        if file not in task_ids or not pred["boxes"]:
            continue
        boxes = [BoxPx(**b) for b in pred["boxes"]]
        out.append(
            {
                "task": task_ids[file],
                "model_version": pred.get("model", "prelabel"),
                "score": round(sum(b.score or 0 for b in boxes) / len(boxes), 4),
                "result": [to_ls_result(b, pred["width"], pred["height"]) for b in boxes],
            }
        )
    return out


def render_overlay(frames_dir: Path, file: str, pred: dict, out: Path) -> None:
    import cv2

    image = cv2.imread(str(frames_dir / file))
    for b in pred["boxes"]:
        p1, p2 = (int(b["x"]), int(b["y"])), (int(b["x"] + b["w"]), int(b["y"] + b["h"]))
        cv2.rectangle(image, p1, p2, (125, 24, 200), 4)
        cv2.putText(
            image,
            f"{b['score']:.2f}",
            (p1[0] + 4, p1[1] + 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            3,
        )
    cv2.imwrite(str(out), cv2.resize(image, (1280, 720)), [cv2.IMWRITE_JPEG_QUALITY, 85])


def prelabels_as_export(prelabels: dict[str, dict], manifest_rows: list[dict]) -> list[dict]:
    """Dress model pre-labels up as a Label Studio export.

    FOR PIPELINE SMOKE TESTS ONLY. A model trained on this learns the zero-shot model's
    mistakes, and an accuracy measured on it is meaningless. It exists so the
    dataset -> train -> export -> adapter chain can be exercised before human labels exist.
    """
    by_file = {r["file"]: r for r in manifest_rows}
    out = []
    for file, pred in prelabels.items():
        row = by_file.get(file)
        if row is None:
            continue
        boxes = [BoxPx(**b) for b in pred["boxes"]]
        out.append(
            {
                "data": {"file": file, "camera": row["camera"], "clip": row["clip"]},
                "annotations": [
                    {
                        "was_cancelled": False,
                        "updated_at": "1970-01-01",
                        "result": [to_ls_result(b, pred["width"], pred["height"]) for b in boxes],
                    }
                ],
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tasks")
    t.add_argument("frames", type=Path)
    t.add_argument("out", type=Path)
    t.add_argument("--predictions", type=Path)
    sub.add_parser("config")
    pr = sub.add_parser("predictions", help="prelabels + {file: task_id} map -> API payloads")
    pr.add_argument("prelabels", type=Path)
    pr.add_argument("task_ids", type=Path)
    pr.add_argument("out", type=Path)
    sm = sub.add_parser("smoke-export", help="pre-labels -> fake export (smoke tests ONLY)")
    sm.add_argument("prelabels", type=Path)
    sm.add_argument("frames", type=Path)
    sm.add_argument("out", type=Path)
    ov = sub.add_parser("overlay", help="draw pre-labels on frames for a visual check")
    ov.add_argument("frames", type=Path)
    ov.add_argument("prelabels", type=Path)
    ov.add_argument("out", type=Path)
    ov.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    if args.cmd == "predictions":
        payload = build_predictions(
            json.loads(args.prelabels.read_text()), json.loads(args.task_ids.read_text())
        )
        args.out.write_text(json.dumps(payload))
        print(
            f"{len(payload)} predictions, {sum(len(p['result']) for p in payload)} boxes -> {args.out}"
        )
        return
    if args.cmd == "smoke-export":
        with open(args.frames / "manifest.csv") as fh:
            rows = list(csv.DictReader(fh))
        export = prelabels_as_export(json.loads(args.prelabels.read_text()), rows)
        args.out.write_text(json.dumps(export))
        print(f"{len(export)} fake annotations -> {args.out}  (SMOKE TEST DATA, not ground truth)")
        return
    if args.cmd == "overlay":
        args.out.mkdir(parents=True, exist_ok=True)
        preds = json.loads(args.prelabels.read_text())
        for file, pred in list(preds.items())[: args.limit]:
            render_overlay(args.frames, file, pred, args.out / file)
        print(f"overlays -> {args.out}")
        return

    if args.cmd == "config":
        print(LABEL_CONFIG)
        return
    preds = json.loads(args.predictions.read_text()) if args.predictions else None
    tasks = build_tasks(args.frames, preds)
    args.out.write_text(json.dumps(tasks))
    seeded = sum(1 for t in tasks if "predictions" in t)
    print(f"{len(tasks)} tasks -> {args.out} ({seeded} with pre-labels)")


if __name__ == "__main__":
    main()
