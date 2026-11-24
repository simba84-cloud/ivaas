"""Standard-column check: how well does the layer counter answer "is this column exactly N high?"

Controlled bays (docs/adr/0004-controlled-bays.md) validate each column against a standard
height instead of counting freely. This measures that question on the crops layers-v3 was
validated on: whole clips held out of training (CC 2_13, CC 3_18, CC 4_29, one per camera
type; 71 crops). For each height N the model's answer is "exactly N" when its count equals N.

    uv run --extra train python -m ivaas_ml.standard_check
    uv run --extra train python -m ivaas_ml.standard_check --heights 14,8 --min-samples 10

Read the output with two caveats, which it also prints:
- The held-out clips also chose layers-v3's best epoch, so the figures lean optimistic.
- Crops from one clip are correlated (the same stacks seen again), so the sample count
  overstates the evidence. The per-clip breakdown shows where each height's positives come from.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

# layers-v3 was trained with these three clips held out (169 train / 71 val, ml/README.md).
LAYERS_V3_VAL_CLIPS = ("CC 2_13", "CC 3_18", "CC 4_29")
DEFAULT_MIN_SAMPLES = 10


def exactly_n(truth: list[int], pred: list[int], n: int) -> dict:
    """Score the yes/no question "is this column exactly n high?".

    A false accept is a column that is not n but the model says n (a short or over column
    would pass). A false reject is a column that is n but the model says otherwise (a good
    column is sent to a supervisor). A rate whose denominator is zero is None, not 0.
    """
    if len(truth) != len(pred):
        raise ValueError("truth and pred differ in length")
    tp = sum(1 for t, p in zip(truth, pred) if t == n and p == n)
    fn = sum(1 for t, p in zip(truth, pred) if t == n and p != n)
    fp = sum(1 for t, p in zip(truth, pred) if t != n and p == n)
    tn = sum(1 for t, p in zip(truth, pred) if t != n and p != n)
    pos, neg = tp + fn, fp + tn

    def rate(a: int, b: int) -> float | None:
        return a / b if b else None

    return {
        "n": n,
        "samples": pos + neg,
        "positives": pos,
        "negatives": neg,
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "accuracy": rate(tp + tn, pos + neg),
        "false_accept_rate": rate(fp, neg),
        "false_reject_rate": rate(fn, pos),
    }


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for k successes in n trials; None when n is 0."""
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _fmt(value: float | None, count: int, denom: int, min_samples: int) -> str:
    if value is None:
        return f"n/a ({count}/0)"
    if denom < min_samples:
        return f"{value:4.0%} ({count}/{denom}) too few"
    lo, hi = wilson(count, denom)
    return f"{value:4.0%} ({count}/{denom}) [{lo:.0%}-{hi:.0%}]"


def _predict(model: Path, crops: Path, items: list[dict]) -> list[int]:
    import numpy as np
    import onnxruntime as ort

    from ivaas_ml.layers import _load

    sess = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    out = []
    for i in range(0, len(items), 16):
        x = np.stack([_load(crops / it["file"], augment=False) for it in items[i : i + 16]])
        out += [round(float(v)) for v in sess.run(None, {name: x})[0].flatten()]
    return out


def main() -> None:
    from ivaas_ml.layers import read_export, split_by_clip

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--export", type=Path, default=Path("data/crop_export.json"))
    ap.add_argument("--crops", type=Path, default=Path("data/crops_all"))
    ap.add_argument("--model", type=Path, default=Path("../models/layers-v3.onnx"))
    ap.add_argument("--val-clips", default=",".join(LAYERS_V3_VAL_CLIPS))
    ap.add_argument("--heights", help="comma-separated N; default 14 plus every labelled height")
    ap.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    ap.add_argument("--json", type=Path, help="also write the figures here")
    a = ap.parse_args()

    items = [i for i in read_export(json.loads(a.export.read_text())) if i["layers"] > 0]
    _, val = split_by_clip(items, val_clips=set(a.val_clips.split(",")))
    if not val:
        raise SystemExit("no held-out crops matched --val-clips")
    truth = [i["layers"] for i in val]
    pred = _predict(a.model, a.crops, val)

    errs = [abs(p - t) for p, t in zip(pred, truth)]
    exact = sum(e == 0 for e in errs)
    within1 = sum(e <= 1 for e in errs)
    print(f"model {a.model}  held-out clips {a.val_clips}")
    print(
        f"{len(val)} crops: MAE {sum(errs) / len(errs):.2f}, exact {exact}/{len(val)}"
        f" ({exact / len(val):.0%}), within ±1 {within1}/{len(val)} ({within1 / len(val):.0%})"
    )

    heights = sorted({int(h) for h in a.heights.split(",")}) if a.heights else sorted({14, *truth})
    rows = [exactly_n(truth, pred, n) for n in heights]
    print(
        f"\n[a-b] is a 95% Wilson interval; 'too few' marks a rate whose denominator is"
        f" under {a.min_samples}, where no interval is worth quoting.\n"
    )
    print(f"{'N':>3}  {'accuracy':<25}{'false accept':<25}{'false reject':<25}positives by clip")
    for r in rows:
        clips = collections.Counter(
            i["clip"].split("_S")[0] for i, t in zip(val, truth) if t == r["n"]
        )
        print(
            f"{r['n']:>3}  "
            f"{_fmt(r['accuracy'], r['tp'] + r['tn'], r['samples'], a.min_samples):<25}"
            f"{_fmt(r['false_accept_rate'], r['fp'], r['negatives'], a.min_samples):<25}"
            f"{_fmt(r['false_reject_rate'], r['fn'], r['positives'], a.min_samples):<25}"
            f"{dict(clips)}"
        )
    confusion = collections.Counter(zip(truth, pred))
    print("\ntrue -> predicted (count):")
    for t in sorted(set(truth)):
        got = ", ".join(f"{p}:{c}" for (tt, p), c in sorted(confusion.items()) if tt == t)
        print(f"  {t:>2} -> {got}")
    print(
        "\nCaveats: these clips also chose the best epoch (optimistic), and crops from one"
        " clip are correlated (the evidence is thinner than the count suggests)."
    )
    if a.json:
        a.json.write_text(json.dumps({"crops": len(val), "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
