# IVaaS Architecture

## 1. What the scope document asks for

One loading bay (4 m × 3 m), 16 × 4K cameras + 1 LPR camera, one GPU edge node.
Count crates per truck, link the count to the plate, reconcile against manual
counts. Success: >95% accuracy, no operational delay, 2 weeks continuous running.

## 2. System view

```
 cameras / NVR ──RTSP──▶ MediaMTX ──WebRTC──▶ Portal (React)
                            │ RTSP                 ▲  REST + WebSocket
                            ▼                      │
                  AI pipeline (edge GPU) ──HTTP──▶ Core API ──▶ PostgreSQL/Timescale
                  preprocess→detect→track         │
                  →count→fuse                     ├──▶ NATS JetStream (integrations, ERP)
                                                  └──▶ Prometheus ─▶ Grafana
                                     MinIO: evidence clips (planned, see §6)
```

The pipeline maps 1:1 onto the seven stages of Figure 3 in the scope:

| Scope stage | Code |
|---|---|
| 1 Camera inputs | `adapters/opencv_source.py` (RTSP or file, auto-reconnect) |
| 2 Pre-processing | `stages/preprocess.py` (undistort, CLAHE for night footage) |
| 3 Object detection | `adapters/onnx_rtdetr.py` (RT-DETR, Apache-2.0, trained by `ml/`); `adapters/onnx_yolo.py` kept for YOLO-format models |
| 4 Occlusion handling | `stages/fusion.py` — redundant chokepoint cameras cover for each other |
| 5 Tracking & counting | `stages/tracking.py`; `stages/stack_counting.py` + `stages/layers.py`: one crossing per **stack**, carrying its layer count (median over the track). `stages/counting.py` keeps the per-crate mode. |
| 6 Consolidation + LPR | `adapters/fast_alpr_reader.py` (MIT; YOLO plate detector + ViT OCR, ONNX) → `stages/plates.py` (format gate + cross-frame vote) → API `RecordPlateRead`, which opens the truck's session itself |
| 7 Accurate count | API reconciliation + portal dashboard |

## 2a. Any camera, any vendor

Nothing downstream of the media gateway knows what kind of camera it is looking at.

```
 Hikvision / Dahua / Uniview / Axis / NVR channel / encoder / phone / recorded file
   rtsp  rtsps  rtmp  srt  http(HLS, MJPEG)  udp(MPEG-TS)  whep      or PUSH to us
                               │
                        MediaMTX (StreamGateway port)
                     ┌─────────┴──────────┐
              RTSP, one format        WebRTC
              AI pipeline             portal live view
```

- A camera is a `StreamSource` value object: a URL in any supported scheme, or *push*.
  Validation and **password redaction** live in the domain, so credentials cannot reach
  an API response, a log line or the browser by accident (tested).
- `RegisterCamera` provisions the path on MediaMTX through its control API **before**
  saving, so a rejected stream leaves no orphan row. Verified against a real MediaMTX
  container: add, re-provision (patch), push paths, idempotent remove.
- **ONVIF discovery** (`adapters/streaming/onvif.py`) is implemented against the standard
  (WS-Discovery probe, then GetServices/GetProfiles/GetStreamUri with WS-Security digest),
  not a vendor SDK. The probe was verified live: it found two real cameras on the dev
  network. The authenticated stream lookup is tested against spec-shaped XML only; it has
  not yet been run against a physical camera with credentials.
- The streams endpoint makes the server call a client-supplied address, so it only accepts
  private LAN IPs (no hostnames, loopback, or link-local/cloud-metadata): SSRF guard, tested.
- Streams are pulled **on demand**: 16 idle 4K cameras cost no bandwidth.

Not covered: cameras that expose *only* a proprietary cloud/P2P protocol and no RTSP/ONVIF
(some consumer devices). Those need the vendor's NVR or a bridge in front of them.

## 2b. Analysis assistant

```
 portal chat ─▶ POST /assistant/chat ─▶ AskAssistant (max 5 steps)
                                          │  ▲
                        ChatModel port ◀──┘  └── AnalyticsTools (read-only)
                  OpenAI-compatible adapter        list_sessions, accuracy_report,
                  Ollama · vLLM · llama.cpp        totals_by_plate, daily_totals, camera_health
```

- **Open weights, self-hosted.** Default `qwen3:8b` (Apache-2.0) on Ollama; no data leaves
  the site. `ChatModel` is a port, so vLLM for scale, or a hosted model, is one adapter.
- **Grounded by construction.** The model can only call five typed, read-only queries.
  There is no SQL tool, no write tool, no code execution. Every reply lists the tools it
  used; the portal flags any reply that used none.
- **Treats its inputs as hostile.** The browser owns the transcript, so only plain
  user/assistant text is kept: forged `system`/`tool` messages are dropped. Tool arguments
  are clamped (days ≤ 90, rows ≤ 50), unknown tool names are refused, the loop is bounded.
- Verified with a real model (`qwen3:1.7b`, CPU, in Docker): correct tool choice and
  correct figures on accuracy, variance and camera-health questions; declined a delete
  request. Latency was 6-33 s on CPU: the edge node's GPU is needed for a usable feel.

Next step for "intelligent video": a vision-language model (e.g. Qwen-VL, open weights)
behind the same port, so the assistant can answer questions about a *clip* ("what happened
at the bay at 04:48?"). Needs evidence-clip capture first (gap 8).

## 2c. Security model

```
 browser ──OIDC code+PKCE──▶ Keycloak (or any OIDC provider) ──▶ bearer token
                                                                   │
 portal ──Authorization: Bearer / ?token= (WebSocket)──▶ API ── TokenVerifier port
 pipeline ──X-IVaaS-Key──────────────────────────────────▶ API ── ApiKeyVerifier
```

- **Roles**: `viewer` (read, assistant) < `operator` (+ sessions) < `admin` (+ cameras,
  discovery). `service` is separate: the pipeline can only ingest and heartbeat; it cannot
  browse, and a human admin cannot ingest. Every route declares its role; anonymous gets 401.
- **Two verifiers behind one port.** `OidcTokenVerifier` validates RS256/ES256 against the
  issuer's JWKS (cached, refetched once on an unknown key id for rotation), checking
  issuer and audience. `LocalTokenVerifier` (HS256, demo users) is for development; it is
  never selected unless `IVAAS_AUTH_MODE=local`. Tested against a fake provider, including
  key rotation, wrong audience and wrong issuer.
- **Portal** discovers the mode from `/auth/config`: password form in local mode, redirect
  to the provider in OIDC mode (oidc-client-ts, MIT). Controls the user's role cannot use
  are hidden; the API enforces regardless.
- **Camera credentials at rest** are Fernet-encrypted in Postgres with keys from
  `IVAAS_SECRETS_KEYS` (newest first; rotation supported; the API refuses to start on
  Postgres without a key). Redaction on the way out was already in place.
- **Pipeline delivery** is spooled to disk before each POST and removed after the API
  acknowledges: an outage or an edge-node reboot loses no counts, and order is preserved.

Verified on the real stack (2026-09-22): Keycloak login via the portal (PKCE), the API
validating Keycloak's RS256 tokens, sessions persisted in Postgres, camera credentials
encrypted in the row and provisioned on MediaMTX, every event in NATS JetStream in
order, role/key refusals. Two bugs surfaced only there: Keycloak publishes an
RSA-OAEP *encryption* key in its JWKS (now skipped), and Keycloak 26 refuses users
without an email (realm users now have one).

Not done: rate limiting, audit log of who reconciled what, token refresh in the portal
(an expired token sends the user back to login), HTTPS termination (put Caddy or Traefik
in front for the POC network).

## 3. SOLID, concretely

Both services use **hexagonal (ports & adapters)** layout: `domain` ← `application`
← `ports` ← `adapters`, wired in one composition root. Dependencies point inward only.

- **S** — one use case per class (`OpenSession`, `ReconcileSession`, …); one pipeline stage per module.
- **O** — new event sink = one more entry in `FanoutEventPublisher`; new detector = new adapter. No edits to use cases or the runner.
- **L** — in-memory and Postgres repositories are interchangeable; the same HTTP test suite runs on either.
- **I** — `SessionReader` / `SessionWriter` / `CameraReader` … are split; nothing depends on methods it doesn't call.
- **D** — use cases and `CameraPipeline` take `Protocol`s; only `config/container.py` and `__main__.py` name concrete classes.

## 4. Key design decisions

- **Count at the chokepoint, not in the stack.** A crate is counted once, when its
  track crosses a line at the truck door, with direction (a crate carried back
  out decrements). This is far more tractable than counting inside an occluded
  4 m stack, and it is the step the scope's Figure 3 actually counts on (stage 5).
- **Two ways to count a stack, per camera.** A `line` for a camera that sees the stack pass
  a point; a `zone` for one that looks into the truck and sees stacks only once inside
  (sustained presence, one count per track). Site footage showed the door camera needs
  the zone: with a line, one stack counted three times and others never.
- **The counted object is the stack, not the crate.** Site footage shows crates only ever
  move as tilted stacks of ~13-15 dragged by a worker. Tracking one stack is robust;
  tracking 15 identical crates inside it is not. A crossing therefore carries `crates=N`
  (API field, 1-40), and N comes from a swappable `LayerCounter`. Unverified against
  manual counts so far: see `ml/README.md`.
- **A plate is a vote, not a read.** On site footage the OCR reads real plates at 0.95+
  but also produced one confident misread (`ABD5679` for `ABD 5670`) and a stream of
  0.4-0.7 junk from logos and signs. So: national format (`ABC 1234`) + confidence floor,
  then ≥3 agreeing reads within 5 s, then a 2-minute cooldown per plate. Verified end to
  end on 6 minutes of CC 2 footage: exactly one event, the right plate, session auto-opened.
- **Two chokepoint cameras are fused, not summed.** `TimeWindowFuser` pairs twin
  crossings one-to-one within 400 ms and tolerates out-of-order arrival. If one
  camera is occluded the other's crossing still counts.
- **ONNX Runtime**, not a framework-specific runtime: same model file on the RTX
  edge node, a CPU box, or a laptop.
- **Domain rules live in the entity** (`LoadingSession`): cannot count into a closed
  session, cannot reconcile an open one, count never negative. Tested without any I/O.

## 5. Scaling path

| Now (POC, 1 bay) | Next |
|---|---|
| Pipeline → API over HTTP | Publish crossings to NATS; API consumes. Buffers through API restarts. |
| One API process | API is stateless except the WebSocket hub → run N replicas, hub subscribes to NATS |
| Thread per camera, one ONNX session each | Batch frames across cameras into one GPU session (or NVIDIA DeepStream / Triton) |
| Single site seeded in code | Site/bay/camera CRUD + multi-tenant auth (Keycloak, OIDC) |
| `create_all` on boot | Alembic migrations (dependency already declared) |

## 6. Known gaps — read before the POC

1. **Stack model `stacks-v2`: usable for first pipeline runs, not yet validated for
   counting.** AP50 0.87 / P 1.0 / R 0.78 on two held-out clips (9 boxes: indicative
   only). Clean on people, vehicles and crate tops. The dense yard is still unlabelled.
   See `ml/README.md`.
2. **3D volumetric reconstruction is not implemented.** The scope's 14 stack-facing
   cameras are registered and streamable, but only chokepoint counting is built.
   Recommendation: prove the >95% target at the chokepoint first; treat stack
   estimation as an independent cross-check in a later phase.
3. **LPR is proven on the existing cameras, not on the BOM's LPR camera.** Reads came
   from CC 2's wide view (plates ~250 px wide); a dedicated plate camera at the bay entry
   will read earlier and more reliably, and just registers as a camera with role `lpr`.
   Cooldown means a truck that leaves and returns within 2 minutes is one session.
4. **Sessions open and close automatically.** A confirmed plate at an idle bay opens a
   session in the bay's default direction (`IVAAS_AUTO_OPEN_DIRECTION`). A session whose
   plate has not been re-read for `IVAAS_AUTO_CLOSE_IDLE_MINUTES` (default 10; the LPR
   voter re-reads a parked truck every ~2 min) is closed by a background sweep and logged.
   Sessions opened by hand with no plate are left to the operator. Verified live in dev.
5. **Lost-update risk under concurrency.** `RecordCrateCrossing` is read-modify-write.
   Safe today (one pipeline, sequential posts per bay); before adding writers, move
   to an append-only `crossings` table (Timescale hypertable) with the count derived,
   which also gives a full audit trail per crate.
6. **Crossing delivery is spooled to disk** (done); the NATS path from §5 remains the
   better long-term design for multiple writers.
7. **No authentication** on the API or portal. This now matters more: the API can
   register cameras, probe the LAN for devices, and drive an LLM. Must sit behind the site network
   until OIDC is added. Default passwords in `docker-compose.yml` are for local use only.
8. **Camera credentials are stored in plain text** in `cameras.source_url` (redacted on
   the way out, but not encrypted at rest). Use pgcrypto or a KMS-wrapped key before production.
9. **Camera status is heartbeat-only.** Nothing yet probes a registered stream to confirm
   it actually plays; a wrong URL is accepted and simply never goes online.
9. **MinIO is provisioned but unused** — evidence-clip capture per session is not built.
