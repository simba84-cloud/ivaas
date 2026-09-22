# IVaaS — Intelligent Video as a Service

Open-source AI video analytics platform. First deployment: a **bakery
loading-bay POC** — automatic counting of stacked bread crates per truck, linked
to the truck's number plate, reconciled against manual counts (target: >95%).


## Layout

| Path | What | Stack |
|---|---|---|
| `services/api` | Core API: any-vendor camera registry + ONVIF discovery, truck sessions, reconciliation, live events, analysis assistant | FastAPI, SQLAlchemy, PostgreSQL/TimescaleDB, NATS JetStream, Ollama |
| `services/pipeline` | Edge AI pipeline: preprocess → detect → track → count stacks → fuse; LPR read → vote | OpenCV, ONNX Runtime, RT-DETR, fast-alpr |
| `ml` | Model workflow: frame sampling, Label Studio interchange, dataset builder | OpenCV, Label Studio |
| `web` | Operator portal (Liquid Intelligent Technologies branding) | React, TypeScript, Vite, Tailwind, TanStack Query |
| `deploy` | MediaMTX, Prometheus config | MediaMTX, Prometheus, Grafana, MinIO |
| `docs/ARCHITECTURE.md` | Design, SOLID mapping, scaling path, known gaps | |

Every component is open source (Apache-2.0 / MIT / BSD / PostgreSQL / AGPL for MinIO & Grafana).

## Run it

Dev mode, no Docker, in-memory storage:

```bash
cd services/api && uv sync && uv run uvicorn ivaas.adapters.http.app:app --port 8000
cd web && npm install && npm run dev          # http://localhost:5173
```

Full stack (needs one secret for credential encryption):

```bash
export IVAAS_SECRETS_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
docker compose up -d --build                  # portal http://localhost:8080 (IVAAS_PORTAL_PORT to change)
docker compose --profile edge up pipeline     # on the GPU edge node; needs models/ + config
```

Assistant (dev): `docker run -d -p 11434:11434 -v ivaas_ollama:/root/.ollama ollama/ollama`, pull a model, then start
the API with `IVAAS_LLM_URL=http://localhost:11434 IVAAS_LLM_MODEL=qwen3:8b`.

Dev logins (local auth mode): `admin/admin`, `operator/operator`, `viewer/viewer`. The Docker stack uses
Keycloak (http://localhost:8180, realm `ivaas`, same demo users, forced password change on first login).

API docs: http://localhost:8000/docs · Metrics: `/metrics` · Grafana: `:3000` · MinIO: `:9001`

Replay recorded footage as a live camera (needs ffmpeg):

```bash
ffmpeg -re -stream_loop -1 -i clip.mp4 -c copy -f rtsp rtsp://localhost:8554/bay-poc/chokepoint-1
```

## Test

```bash
cd services/api && uv run pytest        # 71 tests: domain, HTTP/WebSocket, auth/OIDC, cameras, ONVIF, MediaMTX, assistant, secrets
cd services/pipeline && uv run pytest   # 48 tests: tracker, stack crossing, layer counter, fusion, RT-DETR decode, plates, spooled delivery
cd ml && uv run pytest                  # 26 tests: frame sampling, label round-trip, dataset split, pre-labelling, AP metrics
cd web && npm run build                 # strict TypeScript + production build
```
