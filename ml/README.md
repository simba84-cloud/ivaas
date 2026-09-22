# IVaaS model workflow

Goal: a **stack** detector exported to `models/stacks.onnx`, which is the one thing the
edge pipeline still needs before it can count real crates.

Everything here is permissively licensed (Apache-2.0 / MIT / BSD). Ultralytics YOLO is
deliberately avoided: it is AGPL-3.0, which is a poor fit for a commercial hosted service.

## What the footage showed (see `data/frames/` after step 1)

| Camera | View | Use |
|---|---|---|
| CC 2 | Straight into the truck's rear door; stacked crates clearly separable | **Best counting view**: this is the chokepoint |
| CC 4 | Rear/side of truck at the bay, stacks wheeled past | Second chokepoint candidate |
| CC 3 | Yard, night, tall stacks being dragged by workers | Detector variety: night, tilt, scale |
| CC 1 | Wide yard, trucks arriving, few crates | Mostly negatives (useful: teaches "not a crate") |
| CRATES | Dense storage yard, hundreds of crates | Hard positives; not a counting view |

Crates always move as **tilted stacks of ~13-15, dragged by one worker**. The labelling
unit is therefore the **stack**; see *Findings* below for why, and the labelling rules.

## Steps

```bash
cd ml && uv sync

# 1. Sample frames. Skips static scenes, covers each clip end to end.   (done: 462 frames)
uv run python -m ivaas_ml.extract_frames "../../AI Project" data/frames --every 2 --max-per-clip 60

# 2. Build the import file and start the labelling tool
uv run python -m ivaas_ml.labelstudio tasks data/frames data/tasks.json
docker compose -f labeling/docker-compose.yml up -d        # http://localhost:8090
```

In Label Studio (first visit: create your local account):
1. **Create project** "Bakery crates" → *Labeling Setup* → *Custom template* → paste the
   output of `uv run python -m ivaas_ml.labelstudio config`.
2. *Settings → Cloud Storage → Add Source Storage*: a 4-step wizard. Pick **Local Files** →
   title anything, path `/label-studio/files/frames` → **Test Connection** (must say
   "Connection Verified") → leave the import settings at their defaults → **Save**, *not*
   "Save & Sync". The storage only authorises Label Studio to serve the images; the tasks
   come from the import below. (The default JSON-only filter means an accidental sync
   imports nothing rather than duplicating tasks.)
3. *Import* → upload `data/tasks.json`.

Labelling rules (consistency matters more than speed):
- See *Labelling rules for `stack`* below.
- Start in the saved view **"Truck-door cameras (start here)"** (336 frames, pre-labelled).
- Aim for **~150 frames** first. Then train, pre-label the rest with your
  own model, and only *correct* boxes from there on. That is 3-5x faster than drawing.

```bash
# 3. Export from Label Studio as JSON -> data/export.json, then build the dataset.
#    Train/val is split BY CLIP so near-identical frames never sit on both sides.
uv run python -m ivaas_ml.dataset data/export.json data/frames data/dataset --val 0.2
```

```bash
# 4. Train (RT-DETR r18, Apache-2.0; ~50 s/epoch on an M-series Mac, far faster on the edge GPU)
uv run --extra train python -m ivaas_ml.train data/dataset runs/stacks --epochs 20
#    prints AP@0.5 / precision / recall on the clip-held-out validation split every epoch

# 5. Export to ONNX. Refuses to write a file whose outputs differ from torch.
uv run --extra train python -m ivaas_ml.export runs/stacks/best ../models/stacks.onnx
#    -> stacks.onnx + stacks.json (labels, input size), consumed by
#       services/pipeline/.../adapters/onnx_rtdetr.py
```

The chain was exercised end to end on 2026-09-22 using the zero-shot pre-labels as
stand-in data (`labelstudio.py smoke-export`): dataset -> 3 epochs -> verified export ->
adapter detecting on a real frame at ~180 ms/frame on CPU. **That model is not usable**
(AP50 0.12, trained on machine guesses); it proves the plumbing, nothing else.

## Findings so far (2026-09-21)

**Zero-shot pre-labelling of individual crates does not work on this footage.** Grounding
DINO (`ivaas_ml.prelabel`) was tried whole-frame and tiled 4x3: it returns whole *stacks*,
fragments, and the occasional truck, e.g. 7 boxes on a frame holding ~250 crates. Nothing
was imported into Label Studio. The tool is kept because its box clean-up is tested and it
becomes useful again once a first fine-tuned model exists.

**So the unit is the stack (agreed 2026-09-21).** Dense frames hold 100-250 crates: tens
of thousands of boxes for a human, and at run time tracking ~15 identical crates through a
tilted moving stack invites ID switches. What crosses the truck door is a *stack*.

1. **Detect and track stacks** (big, few per frame: ~1-6 boxes to label). Zero-shot
   Grounding DINO with a tall-and-narrow filter gives **high-precision, moderate-recall**
   pre-labels: on 18 mixed truck-door frames essentially every box was a real stack
   (including every loading moment), empty frames stayed empty, and it missed small/far
   stacks. Those pre-labels are imported for the 336 CC 2/3/4 frames: accept, add, fix.
2. **Count layers per stack** as it crosses the line:
   `services/pipeline/.../stages/layers.py` (periodicity) feeding
   `stages/stack_counting.py` (median over the track, one crossing per stack).
   On three real stacks it read 13.0 / 9.7 / 13.7 against ~12 / ~8 / ~13 visible.

   What that does and does not show: the *visible* counts are eyeballed, a stack cut off
   by the frame edge is undercounted by construction, and +-1 crate on a 13-crate stack is
   7.7%: not yet 95%-grade unless errors are unbiased and average out. It needs measuring
   against real manual counts. If it is not good enough, the fallback is a small learned
   counter on stack crops (its label is a number per crop), behind the same `LayerCounter` port.

   It also cannot tell a stack from paving or a ribbed truck panel (both periodic); only
   detector boxes may be passed to it. A left/right coherence gate was tried and removed:
   it rejected real stacks (perspective skews the rims) and accepted paving.

### Labelling rules for `stack`
- One box per **single column** of crates, top rim to floor, including a tilted one being
  dragged. Box it even when a worker partly hides it.
- A row of clearly separate columns = one box each. A dense yard mass you cannot separate
  into columns = skip it entirely.
- Foreground crates seen from above (just a big lattice filling the corner) = skip.
- No stacks in frame = submit empty. CC 1 frames are mostly this; they teach "a truck is
  not a stack", which is exactly the mistake the zero-shot model made.

## First model: stacks-v1 (2026-09-22)

Trained on 115 human-reviewed frames (61 train / 54 val, split so the box-rich CC 2 clip
trains and the other CC 2 clip validates): 24 training boxes, 8 validation boxes. That
is a tiny dataset, and the numbers reflect it:

- **AP50 0.71 on the held-out clip**, precision 1.0 / recall 0.25 at threshold 0.5.
  It rarely fires wrongly but misses stacks; use threshold ~0.3 for pre-labelling.
- Visually checked on frames from all three cameras: every box on a real stack, including
  yard columns it had almost no examples of. One false positive on a truck door edge.
- ONNX at `models/stacks-v1.onnx`, ~180 ms/frame on CPU.

It is good enough to **pre-label the remaining 220 frames** (CC 3 yard, CC 4) so the
next labelling pass is correction, not drawing. It is not good enough to count with.

## Tests

```bash
uv run pytest    # 26 tests: frame sampling, label round-trip, split, pre-label clean-up, AP metrics
```
