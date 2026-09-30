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
- **Camera status comes from the gateway**, not from the camera: every 10 s the API asks
  MediaMTX which paths are receiving video and marks cameras online/offline. Works the
  same for a pulled Hikvision, a pushed encoder, or a laptop webcam (`deploy/webcam.sh`).
- **WebRTC through Docker** needs MediaMTX to advertise a host the browser can reach:
  `IVAAS_MEDIA_HOST` (the edge node's LAN IP on site). Found the hard way: MediaMTX does
  not expand `${VAR}` in its YAML; use its `MTX_*` environment variables.

Not covered: cameras that expose *only* a proprietary cloud/P2P protocol and no RTSP/ONVIF
(some consumer devices). Those need the vendor's NVR or a bridge in front of them.

## 2a'. Edge nodes: enrollment, fleet health, pulled configuration (M2)

The machine at a site that decodes the cameras and counts is an *enrolled node*, not
a container holding a shared key.

- **Enrollment.** An admin creates a token in the portal (Configure → Edge Nodes) for
  one site and optionally one bay. It is single-use, expires within 72 h, and is shown
  once; only its SHA-256 digest is stored. On the node:
  `python -m ivaas_pipeline enroll --api <url> --token <token>`. The node receives its
  own credential, also shown once and kept in `IVAAS_NODE_FILE` (mode 0600). Expired,
  used and forged tokens all get the same 401.
- **A node speaks only for its site.** `X-IVaaS-Node` authenticates it as the
  `integration` role bound to that site, looked up on every request, so revoking takes
  effect at once. Ingest checks that the bay is in scope and the camera belongs to it:
  a node at one depot cannot post counts, plates or incidents for another (404, nothing
  recorded). The shared `IVAAS_SERVICE_API_KEYS` still works, tenant-wide, for nodes
  not yet enrolled.
- **Fleet health comes from heartbeats** (every 30 s): version, uptime, spool backlog,
  and per camera whether the node is reading it. Health is derived, never assumed:
  `never_seen`, `online` (≤90 s), `stale`, `offline` (>5 min), `revoked`. The portal
  raises an alert for an offline or never-seen node and for a camera a running node
  cannot read.
- **Configuration is pulled.** The desired pipeline config lives on the node record,
  validated to its site and one bay, and is served in `pipeline.json`'s shape with each
  camera's stream address filled in. The node learns of a new version from the
  heartbeat's answer, exits cleanly and is restarted onto it by Docker; events are
  spooled to disk, so nothing counted is lost across the restart. An unenrolled node
  still runs from `pipeline.json`.
- **Models over the air (T2.6).** A model version is registered once
  (`POST /api/v1/models`): the ONNX file is hashed as it streams into object storage,
  and its labels and input size are kept with it. Versions are immutable. A node config
  names a version, and the node fetches it through a node-only endpoint.
  - The node checks the SHA-256 and size before using a byte, and caches the model by
    digest.
  - It loads the new model and runs it once on a blank frame beside the old one, for
    every camera. Only if all of them succeed is it swapped in, between two frames. No
    stream reopens.
  - A download, checksum, load or inference failure is refused: the node keeps
    counting with the model it had and reports why, and the portal raises an alert.
  - The API keeps each node's previous config. `POST /api/v1/edge/nodes/{id}/rollback`
    restores it; a second rollback undoes the first.
  - Measured on the dev stack with 2 simulated cameras: a switch took 9–10 s including
    an 80 MB download, a rollback 46 s end to end (5.8 s of it the swap; the rest is the
    wait for the next heartbeat), all with 0 stream reconnects.
- **Evidence clips.** MediaMTX keeps a rolling 30-minute recording of the evidence
  cameras only (chokepoint and LPR, `IVAAS_EVIDENCE_ROLES`). For each counted crossing
  or plate read, the node cuts 3 s before to 5 s after from that recording through the
  playback server, without re-encoding. Counts close together share a clip.
  - Clips wait in a disk-bounded spool until the API has them, then are filed under the
    load that covered them. They are kept for the tenant's "Evidence clips are kept for"
    setting (90 days by default), then deleted, video first. The portal plays them from
    each load on Reconciliation, over signed links.
  - Sizing: [evidence-sizing.md](evidence-sizing.md).
  - Paths added through MediaMTX's API live only in its memory, so the API puts back
    any camera path, and its recording flag, that the media server has lost. This runs
    every minute and at startup.

## 2a''. Which truck, and people's corrections (M5, first slice)

- **Fleet register.** Each tenant registers its trucks: plate, fleet number and
  operator, added one at a time or imported from CSV. A plate is unique per tenant
  under any spelling, with confusable characters folded together.
- **Matching plate reads.** A plate read is matched against the register, forgiving
  spacing and OCR confusion (`A8C I234` becomes truck ABC 1234). A score below 0.85,
  or a tie between two trucks, is not a match. The load keeps the raw read beside the
  register's spelling, and says honestly how it was identified:
  - `registered`: matched a truck in the register;
  - `unregistered`: a plate was read, but it isn't in the register;
  - `unidentified`: no plate was read;
  - `unchecked`: there is no register to check against.
- **Unidentified loads (T5.2).** Crates crossing at an idle bay open a load in the
  direction they crossed, when auto-open is on. Before, a missed plate read lost the
  whole load's count. An operator can say which truck it was (audited, before and
  after). A later camera read confirms an operator's identification rather than
  replacing it.
- **Corrections (T5.5).** A person records a corrected count with a reason (and a
  note if the reason is "other"). The correction becomes the count of record. The AI
  count, and the accuracy measured on it, never change, so a correction explains a
  load without improving the AI's score.

## 2a-iii. Balances, manifests and exceptions (M5, second slice)

- **Balances (T5.3).** Crates dispatched, returned and still outstanding, per truck,
  route or day (in site time), from each load's count of record. Loads still at the
  bay are shown as in progress and never added in.
- **Dispatch manifests (T5.4).** A CSV of what each truck was meant to carry
  (`POST /api/v1/manifests/import`). Each line is matched one-to-one with the load
  that day, in the same direction, for the same truck (OCR-forgiving).
- **Exceptions.** Three kinds are raised for a person, each with the load's evidence
  clips at hand:
  - the count differs from the manifest by more than a tolerance (a setting, 0 crates
    by default);
  - a manifest truck never came, once its day is over;
  - a truck came that no manifest expected, on a day that had manifests.
- **Keeping exceptions current.** Matching reruns on every import and on the idle
  sweep. Nothing is raised twice. A correction or a late truck closes the exception it
  caused, and says so. Resolving needs a note and never changes a count.

## 2a-iv. Daily reports (M6)

- **What the report holds.** One site's day, in site time:
  - loads, crates dispatched, returned and outstanding, from counts of record;
  - accuracy against the tally sheets, measured on the AI count, never on a correction;
  - manifest exceptions;
  - what people corrected;
  - every load.
  It is built by `domain/reports.py` from the records the portal shows. A day with no
  reconciled tally sheet says it has no accuracy figure, rather than 0%.
- **Formats.** The CSV has one row per load, for spreadsheets and ERP imports. The
  PDF (ReportLab) is for people. The renderers live in `adapters/reports.py` and are
  injected into the use case.
- **Filed every morning.** After 06:00 site time, the idle sweep files yesterday's
  report once per site. Both files go to object storage under the tenant's prefix and
  are listed at `GET /api/v1/reports`, with signed links.
- **On demand.** Any day is available at `GET /api/v1/reports/daily?site_id&day&format`.
  Both routes need `report.export`, which operators do not have. Another tenant's site
  is a 404.

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

## 2b'. Uploaded-video analysis and reports

```
 portal (drop a file) ──multipart──▶ API ──▶ MinIO  uploads/<job>/<file>
                                     │
                              job worker (in the API process, one at a time)
                                     │  downloads, runs ivaas_pipeline.analyse:
                                     │  detect stacks → track → settle-count → layer count → LPR
                                     ▼
                     MinIO  frames/<job>/stack-NNNN-<t>s.jpg  (one annotated frame per counted stack)
                     MinIO  jobs/<job>.json                    (the report; survives restarts)
                                     │
                       portal report page: totals, per-load table, counted-stack gallery,
                       timeline, the video, Print/PDF. Progress streams over the WebSocket.
```

- Objects are served **through the API** (`/api/v1/objects/…`), never by presigned MinIO
  URLs: the S3 endpoint is an internal hostname the browser cannot reach. Because an
  `<img>`/`<video>` tag cannot send a bearer token, the report embeds **HMAC-signed links**
  (key + expiry, 6 h) minted only for callers who passed the viewer check; the route also
  accepts a bearer token. Tampered or expired links get 401.
- The worker resolves its use case on every pass, so components (a reloaded model, a test
  double) can be swapped without a restart. The analyser runs in a thread; frame saves and
  progress cross back to the event loop.
- A job interrupted by a restart is re-queued, not lost.
- Uploads up to 2 GB (nginx `client_max_body_size`, unbuffered proxying).

## 2c. Security model

```
 browser ──OIDC code+PKCE──▶ Keycloak (or any OIDC provider) ──▶ bearer token
                                                                   │
 portal ──Authorization: Bearer / ?token= (WebSocket)──▶ API ── TokenVerifier port
 pipeline ──X-IVaaS-Key──────────────────────────────────▶ API ── ApiKeyVerifier
```

- **Roles** are scoped bindings (proposal §4, see §2e below). Every route declares the
  permission it needs; anonymous gets 401. The pipeline's key holds `integration`: it can
  ingest and read counts, not run the bay, and a human admin cannot ingest.
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

Not done: rate limiting, audit log of who reconciled what, HTTPS termination (put Caddy or Traefik
in front for the POC network).

## 2e. Tenancy and scoped roles (M1)

IVaaS is multi-tenant from the POC on: Bakers Inn is tenant #1, under the partner
LITZIM, under the Cassava platform. `Platform → Partner → Tenant → Site → Bay → Camera`.
Decision record: [adr/0001-pooled-tenancy-rls.md](adr/0001-pooled-tenancy-rls.md).

- **Which tenant.** Only the verified identity says: the account's tenant, or the tenant
  a service key is bound to (`IVAAS_SERVICE_API_KEYS`, value `name@tenant-slug`; a bare
  name is Bakers Inn). No header, query or body can choose it. The auth dependency sets
  it in a context variable (`ivaas/tenancy.py`) for the rest of the request.
- **Row-level security does the isolating.** Every tenant table has `tenant_id`,
  defaulted from the transaction's `app.tenant_id`, and one forced policy. Each
  transaction runs as `ivaas_app` (no superuser, no BYPASSRLS) with the tenant set
  locally (`TenantScopedSessions`), so repository code has no tenant filters to forget.
  In-memory mode gets the same guarantee from one store per tenant (`PerTenant`).
- **Crossing tenants** takes `system_context()`: signing in (the account is found before
  its tenant is known), claiming the next analysis job, and platform provisioning.
  Background sweeps and the worker run inside each tenant in turn.
- **Everything else is per tenant too**: live WebSocket events, NATS subjects
  (`ivaas.t.<tenant>.session.opened`), object keys (`tenants/<id>/…`; keys from before
  tenancy are Bakers Inn's), setting overrides.
- **Roles** are bindings `(account, role, scope)`, stored in `role_bindings` and read on
  every request, so a change takes effect at once. The §4.2 matrix is data in
  `domain/rbac.py`. A binding at site or bay scope counts only on endpoints that check
  that scope (sessions, bays, cameras, zones, uploads); totals, the audit log and
  settings need the role tenant-wide. Platform and partner staff hold no standing
  access to tenant data.
- **Not found, not forbidden.** Another tenant's id, or a site outside the caller's
  scope, answers 404 with the same body as an id that exists nowhere.
- **Provisioning** is `POST /api/v1/platform/tenants` with an `Idempotency-Key`; the
  owner's temporary password is shown once. Steps that belong to later milestones (IdP
  organisation, storage key, plan, edge enrollment) are recorded as pending.

The old roles became: `admin` → `tenant_admin` + `site_manager`, `operator` →
`bay_operator`, `viewer` → `auditor` (migration 0012). Auditors lose camera snapshots,
which §4.2 does not grant them.

## 2d. Testing

| Layer | How | Where |
|---|---|---|
| Domain + use cases | pytest with in-memory fakes, no I/O | `services/api/tests/test_sessions.py`, … |
| Adapters | contract tests (both object stores expose one surface; wire-format tests for ONVIF, MediaMTX, the LLM protocol with `httpx.MockTransport`) | `test_cameras.py`, `test_assistant.py`, `test_object_store.py` |
| Repositories | **one behaviour suite run against both the in-memory fakes and a real Postgres** (Testcontainers, real Alembic migrations, fresh DB per session). A divergence fails once per backend. | `test_repositories.py` (`-m 'not postgres'` to skip Docker) |
| HTTP | FastAPI `TestClient` on the real app, fake analyser, real auth | `test_http.py`, `test_auth.py`, `test_analysis_api.py` |
| Pipeline stages | pure-function tests on synthetic stacks/tracks; decode tests for ONNX adapters | `services/pipeline/tests` |
| Golden footage | the real ONNX models on a 45 s real clip against a frozen result; a retrained model or a counter tweak that changes the count fails here. Models come from the `models-v1` GitHub release (`deploy/fetch-models.sh`). | `services/pipeline/tests/test_golden.py`, `make test-golden` |
| Frontend | Vitest + Testing Library + MSW: real API client against scripted responses, pages rendered from fixtures | `web/src/**/*.test.tsx` |
| Everything | `make test`; GitHub Actions runs lint + tests + build per service on every push | `Makefile`, `.github/workflows/ci.yml` |

Not yet: a Playwright login→upload→report smoke against the compose stack.

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
9. **Schema is migrated by Alembic at API startup** (`services/api/migrations/`), so
   this no longer bites. The baseline migration is idempotent to adopt databases that
   were created by the old `create_all`. Analysis jobs live in `analysis_jobs`
   (JSONB for loads/timeline); the JSON-in-MinIO store remains for dev without Postgres.
10. **A wrong camera URL is accepted silently**: the camera just never goes online. A
   registration-time probe would catch typos earlier.
9. **MinIO is provisioned but unused** — evidence-clip capture per session is not built.
11. **Tenancy gaps (M1).** Stream paths are unique platform-wide (the media server is
   shared), so two tenants cannot register the same path. Foreign keys do not carry
   `tenant_id`, so cross-tenant references are prevented by the API's lookups rather
   than by the schema. The app switches to `ivaas_app` per transaction from the owner's
   login; a separate login role with its own credential is the GA step. Provisioning is
   idempotent for retries, not for two simultaneous first calls with one key. There is
   no break-glass, SSO federation, platform console or partner console yet (M8).
12. **Edge gaps (M2, first slice).** Node credentials are bearer secrets over HTTPS,
   not mTLS client certificates; mTLS comes with TLS termination on the POC network.
   A configuration change other than a model restarts the node. There is no broker-level
   ACL (events reach NATS through the API, which enforces the site binding). The stream
   simulator, T2.3 load report and T2.4/T2.5 drills exist (`deploy/simulator/`), but
   the T2.3 figures themselves must come from a run on the GPU edge node: the dev
   machine has no GPU and was only used to prove the rig at 2 streams. Two nodes redeeming one token at the same
   instant are not prevented by the schema. The portal has no config editor yet: the
   node config is set with `PUT /api/v1/edge/nodes/{id}/config`.
