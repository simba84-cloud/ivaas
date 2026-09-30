# Evidence storage sizing (proposal M2 deliverable)

How much disk the evidence clips need, on the edge node and in the cloud, for the
90-day retention confirmed for Bakers Inn.

There is **one measured figure** here: the size of a real clip. Everything else is an
input to replace once it is known from the bay. The inputs are marked **assumed**, and
the formulas are given so the answer can be recomputed.

## What is stored

- **Edge buffer.** MediaMTX records only the evidence cameras (chokepoint and LPR:
  `IVAAS_EVIDENCE_ROLES`) as a rolling buffer, deleted after 30 minutes
  (`recordDeleteAfter` in `deploy/mediamtx/mediamtx.yml`).
- **Clips.** For every counted crossing and plate read, the node cuts a window from
  3 s before to 5 s after. Counts close together on one camera share a clip, up to
  60 s. Clips are copied without re-encoding, so a clip's bitrate is the camera's.
- **Cloud.** Each clip is kept for the tenant's retention (90 days by default) and then
  deleted, video first.

## Measured

| | Value |
|---|---|
| One crossing clip, dev stack, 2026-09-30 | 8.0 s, 1280×720 H.264 at 10 fps, **2.25 MB**, about 2.25 Mbit/s |

This clip came from the simulator's re-encoded test footage, not a site camera. It
shows the pipeline cuts, stores and serves clips as intended. It is **not** the bitrate
the bay's cameras will produce.

## Formulas

With
- `C` = evidence cameras (2 chokepoint + 1 LPR = 3),
- `B` = a camera's bitrate in Mbit/s,
- `W` = the edge buffer in seconds (1800),
- `N` = clips per day,
- `S` = average clip length in s,
- `R` = retention in days:

```
edge buffer (GB)  = C × B × W / 8 / 1000
cloud per day (GB) = N × S × B / 8 / 1000
cloud total (GB)   = cloud per day × R
```

`N` is roughly: trucks per day × crossings per truck × chokepoint cameras seeing each
crossing, plus one plate clip per truck. Counts less than 5 s apart on one camera share
a clip, so a steady stream of dollies produces fewer, longer clips (up to 60 s each).
The total video stays about the same.

## Worked example (every input assumed)

| Input | Assumed | Why |
|---|---|---|
| `B` | 12 Mbit/s | typical for a 4K H.264 IP camera main stream at 15 fps; H.265 roughly halves it |
| Trucks per day | 40 | placeholder; take it from Bakers Inn's dispatch figures |
| Crossings per truck | 80 | placeholder; depends on crates per dolly or stack (M0 tally template) |
| Chokepoint cameras per crossing | 2 | the scope's two chokepoint cameras both see each crossing |
| `S` | 8 s | the default window |

Results:
- **Edge buffer:** 3 × 12 × 1800 / 8 / 1000 ≈ **8.1 GB**. Rolling, so it does not grow.
- **Cloud per day:** (40 × 80 × 2 + 40) × 8 × 12 / 8 / 1000 ≈ **77 GB/day**.
- **Cloud for 90 days:** about **6.9 TB** at 4K main-stream bitrate.

Levers, if that is too much:

| Change | Effect |
|---|---|
| Keep evidence from the camera's **sub-stream** (e.g. 1080p at 4 Mbit/s) rather than 4K | ÷ 3 → about 2.3 TB |
| H.265 cameras | about ÷ 2 |
| One chokepoint camera's clips instead of both (set `IVAAS_EVIDENCE_ROLES`, or mark one camera as another role) | ÷ 2 |
| Shorter window (e.g. 2 s before, 3 s after) | ÷ 1.6 |

## To replace the assumptions

1. The camera's configured bitrate and codec: from the camera, or from `ffprobe` on
   its stream once it is installed.
2. Trucks per day and crates per dolly or stack: from Bakers Inn, via the M0 tally
   template.
3. Measured clips per truck and average clip size: from the first days of the POC,
   using the `ivaas_evidence_recorded_total` metric and the sizes on each load's
   evidence list.
