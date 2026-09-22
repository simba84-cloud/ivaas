"""Detection metrics, dependency-free so they can be unit-tested exactly."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ivaas_ml.prelabel import iou_matrix


@dataclass(frozen=True)
class Detections:
    boxes: np.ndarray  # (N, 4) xyxy pixels
    scores: np.ndarray  # (N,)


@dataclass(frozen=True)
class Evaluation:
    ap50: float
    precision: float  # at `threshold`
    recall: float
    threshold: float
    n_truth: int
    n_pred: int


def match(pred: np.ndarray, truth: np.ndarray, iou: float = 0.5) -> list[bool]:
    """Greedy one-to-one matching in prediction order (callers sort by score first).
    Returns, per prediction, whether it hit an as-yet-unmatched ground-truth box."""
    if len(truth) == 0:
        return [False] * len(pred)
    if len(pred) == 0:
        return []
    ious = iou_matrix(pred, truth)
    taken = np.zeros(len(truth), dtype=bool)
    hits = []
    for row in ious:
        row = np.where(taken, -1.0, row)
        j = int(row.argmax())
        if row[j] >= iou:
            taken[j] = True
            hits.append(True)
        else:
            hits.append(False)
    return hits


def evaluate(
    preds: list[Detections], truths: list[np.ndarray], *, threshold: float = 0.5, iou: float = 0.5
) -> Evaluation:
    """AP@IoU (COCO-style all-point interpolation, single class) plus P/R at a threshold."""
    scores, hits = [], []
    n_truth = sum(len(t) for t in truths)
    tp_at = fp_at = 0
    for d, t in zip(preds, truths, strict=True):
        order = np.argsort(-d.scores)
        h = match(d.boxes[order], t, iou)
        scores += list(d.scores[order])
        hits += h
        keep = d.scores[order] >= threshold
        tp_at += sum(1 for hit, k in zip(h, keep, strict=True) if hit and k)
        fp_at += sum(1 for hit, k in zip(h, keep, strict=True) if not hit and k)

    if n_truth == 0 or not scores:
        return Evaluation(0.0, 0.0, 0.0, threshold, n_truth, len(scores))
    order = np.argsort(-np.array(scores), kind="stable")
    tp = np.cumsum(np.array(hits, dtype=float)[order])
    fp = np.cumsum(1 - np.array(hits, dtype=float)[order])
    recall = tp / n_truth
    precision = tp / np.maximum(tp + fp, 1e-9)
    # precision envelope, then area under the P-R curve
    for i in range(len(precision) - 2, -1, -1):
        precision[i] = max(precision[i], precision[i + 1])
    r_prev, ap = 0.0, 0.0
    for r, p in zip(recall, precision, strict=True):
        ap += (r - r_prev) * p
        r_prev = r
    return Evaluation(
        float(ap),
        tp_at / max(tp_at + fp_at, 1),
        tp_at / n_truth,
        threshold,
        n_truth,
        len(scores),
    )
