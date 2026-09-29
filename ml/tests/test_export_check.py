"""The export check compares detections, not raw query order."""

import numpy as np

from ivaas_ml.export import detection_error


def _run(seed=0, queries=300, classes=80):
    rng = np.random.default_rng(seed)
    logits = rng.normal(-4, 1, (1, queries, classes)).astype(np.float32)
    logits[0, :5, 0] = [3.0, 2.5, 2.0, 1.5, 1.0]  # five confident detections
    boxes = rng.random((1, queries, 4)).astype(np.float32)
    return logits, boxes


def test_reordered_queries_are_the_same_detections():
    logits, boxes = _run()
    perm = np.random.default_rng(1).permutation(logits.shape[1])
    assert detection_error(logits, boxes, logits[:, perm], boxes[:, perm]) < 1e-6


def test_a_moved_detection_fails_the_check():
    logits, boxes = _run()
    moved = boxes.copy()
    moved[0, 0] += 0.05  # the most confident box shifts
    assert detection_error(logits, boxes, logits, moved) > 1e-3
    weaker = logits.copy()
    weaker[0, 0, 0] = 0.0  # the most confident detection loses its score
    assert detection_error(logits, boxes, weaker, boxes) > 1e-3
