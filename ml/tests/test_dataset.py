import json

import cv2
import numpy as np
import pytest

from ivaas_ml.dataset import build, read_export, split_by_clip, to_coco
from ivaas_ml.labelstudio import (
    BoxPx,
    build_predictions,
    build_tasks,
    from_ls_result,
    to_ls_result,
)


def test_box_round_trips_through_label_studio_percentages():
    box = BoxPx("stack", 256.0, 144.0, 512.0, 288.0)
    back = from_ls_result(to_ls_result(box, 2560, 1440))
    assert (back.x, back.y, back.w, back.h) == pytest.approx((256, 144, 512, 288))


def test_box_dragged_past_the_edge_is_clamped_and_degenerate_boxes_dropped():
    r = to_ls_result(BoxPx("stack", 2400, 1300, 400, 400), 2560, 1440)
    b = from_ls_result(r)
    assert b.x + b.w == pytest.approx(2560) and b.y + b.h == pytest.approx(1440)
    assert from_ls_result(to_ls_result(BoxPx("stack", 3000, 100, 50, 50), 2560, 1440)) is None


def task(file, clip, camera, boxes, *, cancelled=False, annotated=True):
    result = [to_ls_result(BoxPx("stack", *b), 1000, 500) for b in boxes]
    anns = [{"result": result, "was_cancelled": cancelled, "updated_at": "2026-09-22"}]
    return {
        "data": {"file": file, "clip": clip, "camera": camera},
        "annotations": anns if annotated else [],
    }


def test_export_keeps_negatives_skips_untouched_and_cancelled():
    items = read_export(
        [
            task("a.jpg", "c1", "cc2", [(10, 10, 100, 50)]),
            task("b.jpg", "c1", "cc2", []),  # reviewed, genuinely empty
            task("c.jpg", "c1", "cc2", [(1, 1, 9, 9)], annotated=False),
            task("d.jpg", "c1", "cc2", [(1, 1, 9, 9)], cancelled=True),
            task("e.jpg", "c1", "cc2", [(10, 10, 2, 2)]),  # sliver: a mis-click, not a crate
        ]
    )
    assert [(i["file"], len(i["boxes"])) for i in items] == [
        ("a.jpg", 1),
        ("b.jpg", 0),
        ("e.jpg", 0),
    ]


def test_split_never_puts_one_clip_on_both_sides_and_is_deterministic():
    items = [
        {"clip": f"cam{c}-clip{k}", "camera": f"cam{c}"}
        for c in range(3)
        for k in range(5)
        for _ in range(10)
    ]
    a = split_by_clip(items, 0.2)
    assert a == split_by_clip(list(reversed(items)), 0.2)
    for cam in range(3):
        sides = {a[f"cam{cam}-clip{k}"] for k in range(5)}
        assert sides == {"train", "val"}  # every camera represented in both
    val_share = sum(1 for it in items if a[it["clip"]] == "val") / len(items)
    assert 0.15 <= val_share <= 0.4


def test_single_clip_camera_goes_wholly_to_train():
    items = [{"clip": "only", "camera": "solo"}] * 8
    assert split_by_clip(items, 0.2) == {"only": "train"}


def test_coco_output_shape():
    coco = to_coco(
        [
            {
                "file": "a.jpg",
                "width": 1000,
                "height": 500,
                "boxes": [BoxPx("stack", 10, 20, 100, 50)],
            }
        ]
    )
    assert coco["categories"] == [{"id": 0, "name": "stack"}]
    assert coco["annotations"][0]["bbox"] == [10, 20, 100, 50]
    assert coco["annotations"][0]["area"] == 5000


def test_build_end_to_end(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    export = []
    for clip in ("x", "y", "z"):
        for n in range(4):
            name = f"{clip}{n}.jpg"
            cv2.imwrite(str(frames / name), np.zeros((500, 1000, 3), np.uint8))
            export.append(task(name, clip, "cc2", [(10, 10, 100, 50)] if n else []))
    (tmp_path / "export.json").write_text(json.dumps(export))
    stats = build(tmp_path / "export.json", frames, tmp_path / "ds", 0.3)
    assert stats["train"]["images"] + stats["val"]["images"] == 12
    assert stats["val"]["images"] in (4, 8) and stats["train"]["empty_images"] >= 1
    coco = json.loads((tmp_path / "ds/val/_annotations.coco.json").read_text())
    assert all(i["width"] == 1000 for i in coco["images"])  # negatives got their size from disk
    train_files = {p.name for p in (tmp_path / "ds/train").glob("*.jpg")}
    val_files = {p.name for p in (tmp_path / "ds/val").glob("*.jpg")}
    assert not {f[0] for f in train_files} & {f[0] for f in val_files}  # no clip on both sides


def test_tasks_carry_clip_and_predictions(tmp_path):
    (tmp_path / "manifest.csv").write_text(
        "file,camera,clip,frame_index,seconds,change\na.jpg,cc2,clipA.mp4,0,0,1\nb.jpg,cc2,clipA.mp4,40,2,0.1\n"
    )
    preds = {
        "a.jpg": {
            "width": 1000,
            "height": 500,
            "model": "gdino",
            "boxes": [{"label": "stack", "x": 0, "y": 0, "w": 100, "h": 50, "score": 0.6}],
        }
    }
    tasks = build_tasks(tmp_path, preds)
    assert tasks[0]["data"]["clip"] == "clipA.mp4"
    assert tasks[0]["data"]["image"].endswith("?d=frames/a.jpg")
    assert tasks[0]["predictions"][0]["result"][0]["value"]["width"] == pytest.approx(10)
    assert "predictions" not in tasks[1]


def test_predictions_target_existing_tasks_and_skip_empty_or_unknown():
    box = {"label": "stack", "x": 100, "y": 50, "w": 200, "h": 100, "score": 0.5}
    prelabels = {
        "a.jpg": {
            "width": 1000,
            "height": 500,
            "model": "gdino",
            "boxes": [box, {**box, "score": 0.3}],
        },
        "empty.jpg": {"width": 1000, "height": 500, "boxes": []},
        "not-imported.jpg": {"width": 1000, "height": 500, "boxes": [box]},
    }
    out = build_predictions(prelabels, {"a.jpg": 41, "empty.jpg": 42})
    assert [(p["task"], p["model_version"], p["score"], len(p["result"])) for p in out] == [
        (41, "gdino", 0.4, 2)
    ]
    assert out[0]["result"][0]["value"]["x"] == pytest.approx(10)
