# IVaaS Stack Reference

A quick lookup for what the project is built with and where it runs. For *why*
it is built this way, see [ARCHITECTURE.md](ARCHITECTURE.md). For how to run it,
see the [README](../README.md).

## Frontend — `web/`

| Concern | Choice |
| --- | --- |
| Language / framework | TypeScript 5, React 18 |
| Build / dev server | Vite 5 (`npm run dev` → http://localhost:5173) |
| Styling | Tailwind CSS 3; colour tokens in `web/src/index.css`, exposed via `web/tailwind.config.js` |
| Server state | TanStack Query 5 |
| Routing | React Router 6 |
| Auth (OIDC mode) | `oidc-client-ts` against Keycloak |
| Charts / motion / icons | Recharts, Framer Motion, Lucide |
| API contract | The OpenAPI spec (FastAPI) checked against every test response with `jsonschema` |
| Tests | Vitest, Testing Library, MSW (API mocking), jsdom; Playwright (Chromium) end to end against the real API |
| Production image | `node:22-alpine` build → `nginx:1.27-alpine` serving `dist/` (`web/nginx.conf`) |

## Backend — `services/api/`

| Concern | Choice |
| --- | --- |
| Language | Python ≥ 3.12 (image: `python:3.13-slim`), managed with `uv` |
| Web framework | FastAPI on Uvicorn |
| Models / settings | Pydantic v2, pydantic-settings (`IVAAS_*` env vars) |
| Database | PostgreSQL 16 + TimescaleDB, via SQLAlchemy 2 (async) and asyncpg |
| Migrations | Alembic, run at API startup — one statement per `op.execute` |
| Messaging | NATS JetStream (`nats-py`) |
| Object storage | MinIO (S3 API) via `aioboto3` |
| Auth | Local accounts (Argon2id via `argon2-cffi`) or OIDC; JWTs via PyJWT |
| Tenancy | Postgres row-level security on every tenant table; app runs as `ivaas_app` with `app.tenant_id` set per transaction (ADR 0001) |
| Roles | Scoped bindings (platform / partner / tenant / site / bay), matrix in `domain/rbac.py` |
| Edge nodes | Enrolled with a single-use token; authenticate with `X-IVaaS-Node`, bound to one site; heartbeat and pulled config (`/api/v1/edge/*`) |
| Fleet register | Trucks per tenant; plate reads matched with OCR-confusion folding (`domain/plates.py`, `domain/fleet.py`) |
| Sheet uploads | Tally sheets, manifests and the fleet register as CSV or Excel (.xlsx, via openpyxl, MIT). A workbook sheet is turned into the CSV the importer reads, so both get the same rules and messages; the whole tally workbook can be uploaded once (`adapters/spreadsheet.py`). The portal hands out that workbook made for the tenant: the Liquid-branded paper form, entry sheets with exactly the importer's columns, and the tenant's bays in the drop-down (`GET /api/v1/tally/template`, `adapters/tally_template.py`) |
| Manifests and balances | Dispatch manifests matched to loads; exceptions per mismatch, missing or unexpected truck; balances from counts of record (`domain/manifests.py`) |
| Webhooks | `session.closed`, `exception.raised`; Standard Webhooks signing (HMAC-SHA256), retried on backoff, replayable, sent with httpx (`/api/v1/webhooks`) |
| POC report | Accuracy, speed, reliability, LPR and ROI over the POC window; PDF, CSV, Excel and JSON (`/api/v1/reports/poc`), from edge availability history (`/api/v1/edge/nodes/{id}/availability`) |
| Billing | Price book (placeholder until finance approves one), subscriptions with daily proration, entitlements enforced at registration and edge config, idempotent usage ledger, Decimal invoices (`/api/v1/billing`) |
| Backups | `pg_dump` every 15 min with an exact row-count manifest, an hourly MinIO mirror, and a restore drill reporting RPO/RTO (`deploy/backup`) |
| Daily reports | PDF via ReportLab, CSV via `csv`, Excel via openpyxl; filed after 06:00 site time, and any day on demand (`/api/v1/reports`) |
| Report branding | The PDFs and Excel workbooks carry the Liquid logo and palette (logo and report name on every page, navy table headers, brand and page number at the foot); every file is named `liquid-ivaas-<site>-…`. The CSVs stay plain, header row first, for imports (`adapters/branding.py`) |
| Evidence clips | MediaMTX records evidence cameras (30 min buffer); the node cuts clips via its playback server and uploads them; kept per tenant (default 90 days) |
| Models (OTA) | Registered versions in object storage with SHA-256; nodes verify, cache by digest and swap in place; rollback per node (`/api/v1/models`) |
| Secrets at rest | `cryptography` (Fernet, key in `IVAAS_SECRETS_KEY`) |
| Metrics | `prometheus-client`, exposed at `/metrics` |
| Health | `GET /healthz` |
| Analysis worker | Same image as the API, run as `worker`; `IVAAS_RUN_ANALYSIS_WORKER=false` on the API |

Layering: `domain` (rules, no I/O) → `application` (use cases) → `ports`
(protocols) → `adapters` (Postgres, NATS, S3, HTTP). `config/container.py` wires
adapters to ports.

## AI pipeline — `services/pipeline/` and `ml/`

| Concern | Choice |
| --- | --- |
| Inference | ONNX Runtime, OpenCV, NumPy |
| Detector | RT-DETR, exported to ONNX (`models/stacks-*.onnx`, `layers-*.onnx`, `people-coco.onnx`) |
| Plate reading | `fast-alpr` (YOLO plate detector + plate OCR) |
| Training data | `ml/`: frame sampling, Label Studio interchange, dataset builder |
| Models | Not in git; fetch with `./deploy/fetch-models.sh` (release `models-v1`) |
| Assistant LLM | Ollama (e.g. `qwen3:8b`) |

## Services and ports (docker compose)

| Service | Image | Host port | Purpose |
| --- | --- | --- | --- |
| `web` | nginx (built) | `IVAAS_PORTAL_PORT` (default 8080) | Operator portal |
| `api` | built | 8000 | Core API; docs at `/docs` |
| `worker` | same as `api` | — | Uploaded-video analysis; scale with `--scale worker=N` |
| `postgres` | `timescale/timescaledb:latest-pg16` | — | Primary database |
| `nats` | `nats:2-alpine` | — | Event bus |
| `minio` | `quay.io/minio/minio` | 9001 (console) | Video and snapshot storage |
| `mediamtx` | `bluenviron/mediamtx` | 8554 (RTSP), 8889 (WebRTC), 8189 | Camera ingest and relay |
| `keycloak` | `quay.io/keycloak/keycloak:26.0` | 8180 | Identity (realm `ivaas`) |
| `ollama` | `ollama/ollama` | — | Local LLM for the assistant |
| `prometheus` | `prom/prometheus` | 9090 | Metrics |
| `grafana` | `grafana/grafana-oss` | 3000 | Dashboards |
| `pipeline` | built (profile `edge`) | — | Edge AI on the GPU node |

## Commands

```bash
make test          # everything; this is what "green" means
make test-api-fast # API without Docker
make test-golden   # real models on a real clip (slow; needs models/)
make test-e2e      # portal + API in a browser (once: cd web && npx playwright install chromium)
make lint          # ruff + tsc
make ci            # lint, test, build
```

On Colima, set `TESTCONTAINERS_RYUK_DISABLED=true` or the Postgres tests error.

## Licences

Everything is open source: Apache-2.0 / MIT / BSD / PostgreSQL, with AGPL for
MinIO and Grafana.
