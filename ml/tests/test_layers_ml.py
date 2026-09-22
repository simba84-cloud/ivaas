from ivaas_ml.layers import read_export, split_by_clip


def task(file, clip, value, cancelled=False):
    return {
        "data": {"file": file, "clip": clip, "camera": "cc2"},
        "annotations": [
            {
                "result": [{"type": "number", "value": {"number": value}}],
                "was_cancelled": cancelled,
                "updated_at": "1",
            }
        ],
    }


def test_read_export_takes_numbers_and_skips_cancelled():
    items = read_export(
        [task("a.jpg", "c1", 8), task("b.jpg", "c1", 0), task("c.jpg", "c2", 12, cancelled=True)]
    )
    assert [(i["file"], i["layers"]) for i in items] == [("a.jpg", 8), ("b.jpg", 0)]


def test_split_keeps_clips_together():
    items = [{"file": f"{c}{k}", "clip": c, "layers": 8} for c in "abcd" for k in range(5)]
    tr, va = split_by_clip(items, 0.25)
    assert len(va) == 5 and not {i["clip"] for i in tr} & {i["clip"] for i in va}
    tr, va = split_by_clip(items, val_clips={"b", "d"})
    assert {i["clip"] for i in va} == {"b", "d"} and len(tr) == 10
