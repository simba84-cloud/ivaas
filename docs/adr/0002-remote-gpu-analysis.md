# ADR 0002: Uploaded-video analysis may detect on the cluster GPU

- **Status:** accepted; code in place, off until `IVAAS_DETECTOR_URL` is set
- **Date:** 2026-11-11

## Context

Uploaded-video analysis runs `ivaas_pipeline.analyse` in the `worker` container on
CPU. A 36-minute clip saturates every core for over an hour.

Detection is moving to RF-DETR Base. A colleague has deployed RF-DETR Base on the
cluster GPU (`cpt-gpu002`, one MIG 1g.18gb slice). It runs Hugging Face Transformers
behind FastAPI and answers `POST /detect` in about 50 ms per image once warm. Access
goes through the portal proxy, which checks a LiteLLM API key.

The design so far keeps inference on the edge node, and the proposal says all data
stays in Zimbabwe (`docs/IVaaS_Solution_Proposal.md`, data residency). Sending frames to the cluster is an
exception to both, so it is recorded here.

## Decision

1. **Uploaded-video analysis may use the remote detector.** When `IVAAS_DETECTOR_URL`
   is set, the worker sends each analysed frame through `HttpDetector`
   (`services/pipeline/.../adapters/http_detector.py`):
   - Frames are sent as JPEG, with the longest side at most 1280 px.
   - Requests carry a Bearer key read from `.env`.
   - With no URL set, the worker uses the local ONNX model, which is the previous
     behaviour.
2. **Live cameras stay on the edge.** The edge node runs RF-DETR locally as ONNX.
   Sending sixteen 4K streams frame by frame over the WAN is not practical, and the
   edge design stands.
3. **A frame that cannot be answered fails the job.** Network errors, 5xx and 429 are
   retried with backoff. After that, or straight away on any other 4xx, `detect()`
   raises. It never returns an empty frame, because an empty frame counts as zero
   crates and would quietly lower the figure this system reports.
4. **The adapter is chosen in the container only** (`analysis_detector` in
   `config/container.py`). `analyse_video` depends on the `Detector` port.

## Consequences

- Frames from uploaded videos leave the site and go to the cluster. **Not yet
  confirmed:** where `cpt-gpu002` physically runs (the name suggests Cape Town), and
  whether that meets the data-residency commitment for each tenant. Confirm both
  before setting `IVAAS_DETECTOR_URL` in a tenant deployment.
- The remote service must serve weights fine-tuned for `stack`. Stock COCO weights
  return no `stack` detections, so every load would count zero. Check with one `curl`
  on a bay frame before switching it on.
- Analysis now depends on the cluster and the network being up. When either is down,
  jobs fail and can be retried; they do not produce a wrong count.
- Throughput is now bound by network round trips. Measure it on a real clip; overlap
  requests only if waiting on the network dominates.
