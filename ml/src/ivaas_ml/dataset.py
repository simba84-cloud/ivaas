"""Turn a Label Studio export into a COCO dataset with a leakage-free split.

    uv run python -m ivaas_ml.dataset data/export.json data/frames data/dataset --val 0.2

Frames from one clip are near-duplicates of each other. A random per-frame split
would put almost the same image in train and validation, and the validation
score would look excellent while telling us nothing. So we split by *clip*:
every frame of a clip lands on the same side.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path

from ivaas_ml.labelstudio import LABELS, BoxPx, from_ls_result

MIN_BOX_PX = 4.0


def read_export(export: list[dict]) -> list[dict]:
    """-> [{file, clip, camera, width, height, boxes}] for every *annotated* task.

    A task submitted with zero boxes is a valid negative example (an empty yard);
    a task never opened has no annotation at all and is skipped.
    """
    items = []
    for task in export:
        done = [a for a in task.get("annotations", []) if not a.get("was_cancelled")]
        if not done:
            continue
        latest = max(done, key=lambda a: a.get("updated_at") or a.get("created_at") or "")
        boxes: list[BoxPx] = []
        width = height = None
        for result in latest.get("result", []):
            width = width or result.get("original_width")
            height = height or result.get("original_height")
            box = from_ls_result(result)
            if box and box.label in LABELS and box.w >= MIN_BOX_PX and box.h >= MIN_BOX_PX:
                boxes.append(box)
        data = task["data"]
        items.append(
            {
                "file": data["file"],
                "clip": data.get("clip", data["file"]),
                "camera": data.get("camera", "unknown"),
                "width": width,
                "height": height,
                "boxes": boxes,
            }
        )
    return items


def split_by_clip(items: list[dict], val_fraction: float, seed: str = "ivaas") -> dict[str, str]:
    """clip -> 'train' | 'val'. Deterministic, and stratified per camera so every
    camera angle that has at least two clips is represented in validation."""
    by_camera: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for it in items:
        by_camera[it["camera"]][it["clip"]] += 1

    assignment: dict[str, str] = {}
    for clips in by_camera.values():
        ordered = sorted(clips, key=lambda c: hashlib.sha256(f"{seed}:{c}".encode()).hexdigest())
        if len(ordered) < 2:
            assignment[ordered[0]] = "train"  # cannot validate on a camera without leaking
            continue
        total, target, taken = sum(clips.values()), sum(clips.values()) * val_fraction, 0
        for clip in ordered:
            # always leave at least one clip for training
            remaining_train = sum(1 for c in ordered if assignment.get(c, "train") == "train") - 1
            if taken < target and remaining_train >= 1 and taken + clips[clip] < total:
                assignment[clip] = "val"
                taken += clips[clip]
            else:
                assignment[clip] = "train"
    return assignment


def to_coco(items: list[dict]) -> dict:
    coco = {
        "images": [],
        "annotations": [],
        "categories": [{"id": i, "name": n} for i, n in enumerate(LABELS)],
    }
    ann_id = 1
    for image_id, it in enumerate(items, start=1):
        coco["images"].append(
            {"id": image_id, "file_name": it["file"], "width": it["width"], "height": it["height"]}
        )
        for b in it["boxes"]:
            coco["annotations"].append(
                {
                    "id": ann_id,
                    "image_id": image_id,
                    "category_id": LABELS.index(b.label),
                    "bbox": [round(b.x, 2), round(b.y, 2), round(b.w, 2), round(b.h, 2)],
                    "area": round(b.w * b.h, 2),
                    "iscrowd": 0,
                }
            )
            ann_id += 1
    return coco


def build(export_path: Path, frames: Path, out: Path, val_fraction: float) -> dict[str, dict]:
    items = read_export(json.loads(export_path.read_text()))
    needs_size = [it for it in items if it["width"] is None]
    if needs_size:  # negatives carry no result rows, hence no recorded size
        import cv2

        for it in needs_size:
            h, w = cv2.imread(str(frames / it["file"])).shape[:2]
            it["width"], it["height"] = w, h

    assignment = split_by_clip(items, val_fraction)
    stats = {}
    for split in ("train", "val"):
        chosen = [it for it in items if assignment[it["clip"]] == split]
        target = out / split
        target.mkdir(parents=True, exist_ok=True)
        for it in chosen:
            shutil.copy2(frames / it["file"], target / it["file"])
        (target / "_annotations.coco.json").write_text(json.dumps(to_coco(chosen)))
        stats[split] = {
            "images": len(chosen),
            "boxes": sum(len(it["boxes"]) for it in chosen),
            "empty_images": sum(1 for it in chosen if not it["boxes"]),
            "cameras": sorted({it["camera"] for it in chosen}),
        }
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("export", type=Path, help="Label Studio JSON export")
    ap.add_argument("frames", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--val", type=float, default=0.2)
    args = ap.parse_args()
    for split, s in build(args.export, args.frames, args.out, args.val).items():
        print(
            f"{split:5s} {s['images']:4d} images  {s['boxes']:6d} boxes  "
            f"{s['empty_images']:3d} empty  cameras={','.join(s['cameras'])}"
        )


if __name__ == "__main__":
    main()
