import numpy as np
import pytest

from ivaas_pipeline.adapters.onnx_rtdetr import decode


def logit(p):
    return float(np.log(p / (1 - p)))


def test_decode_scales_normalised_boxes_to_pixels_and_filters_by_score():
    logits = np.array([[logit(0.9)], [logit(0.2)], [logit(0.6)]])
    boxes = np.array([[0.5, 0.5, 0.2, 0.4], [0.1, 0.1, 0.1, 0.1], [0.9, 0.5, 0.2, 0.2]])
    dets = decode(logits, boxes, ["stack"], 1000, 500, threshold=0.5)
    assert [d.confidence for d in dets] == pytest.approx([0.9, 0.6])
    b = dets[0].box
    assert (b.x1, b.y1, b.x2, b.y2) == pytest.approx((400, 150, 600, 350))
    assert dets[1].box.x2 == 1000  # clipped to the frame edge
    assert all(d.label == "stack" for d in dets)


def test_decode_multiclass_picks_argmax():
    logits = np.array([[logit(0.3), logit(0.8)]])
    boxes = np.array([[0.5, 0.5, 0.5, 0.5]])
    (d,) = decode(logits, boxes, ["stack", "person"], 100, 100, threshold=0.5)
    assert d.label == "person" and d.confidence == pytest.approx(0.8)


def test_decode_empty():
    assert decode(np.zeros((0, 1)), np.zeros((0, 4)), ["stack"], 100, 100, 0.5) == []
