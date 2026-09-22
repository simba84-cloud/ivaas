import numpy as np

from ivaas_ml.prelabel import clean, iou_matrix, select_rows


def xyxy(*rows):
    return np.array(rows, dtype=float)


def test_iou_matrix():
    m = iou_matrix(xyxy([0, 0, 10, 10]), xyxy([0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]))
    assert np.allclose(m, [[1.0, 1 / 3, 0.0]])


def test_drops_giant_boxes_and_slivers():
    boxes = xyxy([0, 0, 1900, 1000], [100, 100, 300, 600], [500, 500, 505, 700])
    assert clean(boxes, np.array([0.9, 0.5, 0.8]), 1920, 1080) == [1]


def test_nms_keeps_the_higher_scoring_duplicate():
    boxes = xyxy([100, 100, 300, 600], [104, 102, 302, 604], [600, 100, 800, 600])
    assert sorted(clean(boxes, np.array([0.4, 0.7, 0.5]), 1920, 1080)) == [1, 2]


def test_stack_box_wrapping_individual_crates_is_removed():
    stacks = [[100 + 220 * k, 100, 300 + 220 * k, 700] for k in range(3)]
    group = [95, 95, 745, 1000]  # tall enough to pass the aspect test, but wraps all three
    boxes = xyxy(*stacks, group)
    kept = clean(boxes, np.array([0.5, 0.5, 0.5, 0.9]), 1920, 1080, max_area_frac=0.5)
    assert sorted(kept) == [0, 1, 2]


def test_box_containing_one_smaller_box_survives():
    # a crate with a detected handle/logo inside it is still a crate
    boxes = xyxy([100, 100, 400, 700], [150, 150, 200, 250])
    assert sorted(clean(boxes, np.array([0.8, 0.3]), 1920, 1080)) == [0, 1]


def test_wide_boxes_are_not_stacks():
    truck, row_of_stacks, stack = [80, 60, 380, 270], [700, 100, 1300, 700], [1400, 100, 1560, 700]
    assert clean(xyxy(truck, row_of_stacks, stack), np.array([0.5, 0.7, 0.4]), 1920, 1080) == [2]


def test_empty_input():
    assert clean(np.zeros((0, 4)), np.zeros(0), 1920, 1080) == []


def test_select_rows_filters_by_camera_list(tmp_path):
    m = tmp_path / "manifest.csv"
    m.write_text("file,camera\na.jpg,cc1\nb.jpg,cc2\nc.jpg,cc4\n")
    assert [r["file"] for r in select_rows(m, "cc2, cc4")] == ["b.jpg", "c.jpg"]
    assert len(select_rows(m, None)) == 3
    assert select_rows(m, "nope") == []
