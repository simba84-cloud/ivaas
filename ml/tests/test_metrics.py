import numpy as np
import pytest

from ivaas_ml.metrics import Detections, evaluate, match


def b(*rows):
    return np.array(rows, dtype=float).reshape(-1, 4)


def test_match_is_one_to_one_and_ordered():
    truth = b([0, 0, 10, 10])
    pred = b([0, 0, 10, 10], [1, 1, 11, 11])  # both overlap the same truth
    assert match(pred, truth) == [True, False]
    assert match(b(), truth) == [] and match(pred, b()) == [False, False]


def test_perfect_detector_scores_one():
    truths = [b([0, 0, 10, 10], [50, 50, 80, 90]), b([5, 5, 20, 20])]
    preds = [Detections(t, np.full(len(t), 0.9)) for t in truths]
    e = evaluate(preds, truths)
    assert (e.ap50, e.precision, e.recall) == (1.0, 1.0, 1.0)


def test_misses_and_false_alarms_are_counted():
    truths = [b([0, 0, 10, 10], [50, 50, 80, 90])]
    preds = [Detections(b([0, 0, 10, 10], [200, 200, 220, 220]), np.array([0.9, 0.8]))]
    e = evaluate(preds, truths, threshold=0.5)
    assert e.recall == 0.5 and e.precision == 0.5
    assert e.ap50 == pytest.approx(0.5)  # P=1 at R=0.5, then nothing


def test_threshold_only_affects_precision_recall_not_ap():
    truths = [b([0, 0, 10, 10])]
    preds = [Detections(b([0, 0, 10, 10]), np.array([0.3]))]
    lo, hi = evaluate(preds, truths, threshold=0.2), evaluate(preds, truths, threshold=0.5)
    assert lo.ap50 == hi.ap50 == 1.0
    assert lo.recall == 1.0 and hi.recall == 0.0


def test_empty_images_on_both_sides():
    e = evaluate([Detections(b(), np.array([]))], [b()])
    assert e.n_truth == 0 and e.ap50 == 0.0
