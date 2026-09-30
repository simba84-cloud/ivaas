# Working in this repo

IVaaS counts stacked bread crates off loading-bay cameras, links each load to a
number plate, and reconciles the AI count against a manual one. The figure it
produces is the product. Most of the rules below exist to protect it.

## Layout

| Path | What it is |
| --- | --- |
| `services/api` | Core API and the analysis worker. Ports and adapters. |
| `services/pipeline` | Edge AI: detect → track → count stacks; LPR read → vote. Also runs uploaded-video analysis. |
| `ml` | Training and export for the ONNX models in `models/`. |
| `web` | React portal. |
| `deploy`, `docker-compose.yml` | The stack: Postgres, NATS, MinIO, MediaMTX, Keycloak, Ollama, Prometheus, Grafana. |

Inside `services/api`: `domain` (rules, no I/O) → `application` (use cases) →
`ports` (protocols) → `adapters` (Postgres, NATS, S3, HTTP). `config/container.py`
is the composition root and the only place that knows which adapter backs which
port. New behaviour goes behind a port; new integrations go in `adapters`.

## Commands

```bash
make test          # everything; this is what "green" means
make test-api-fast # API without Docker (skips the Testcontainers suite)
make lint          # ruff + tsc
make ci            # lint, test, build
```

Testcontainers needs `TESTCONTAINERS_RYUK_DISABLED=true` on Colima, or its
reaper returns 500 and every Postgres test errors.

## Rules that came from bugs

**Verify inside the built image, not just locally.** A method was lost in a
string-replace edit, the tests passed against the source, and the shipped image
hung every job. `docker compose exec api grep …` before believing a fix shipped.

**Never prune Docker volumes.** Other projects' databases live in them. Image
pruning is fine and is sometimes needed when rebuilds fill the disk.

**One statement per `op.execute`** in migrations: asyncpg rejects multi-statement
strings. Migrations run at API startup, so a broken one takes the API down.

**Tenant tables are row-level secured, and a test enforces it.** Any new table
with `tenant_id` needs `ENABLE` + `FORCE ROW LEVEL SECURITY` and the
`tenant_isolation` policy, or `tests/test_rls.py` fails. RLS binds the owner too,
so a migration that updates data sets `app.scope` to `system` first. Code that
must cross tenants uses `system_context()`, never a raw connection.

**Never invent a number.** If the data cannot support a figure, say so on screen:
"No prior period to compare" rather than 0%, a broken sparkline rather than zeroes
for days nothing was measured, "no cameras registered" rather than a green tick.
An approval records that a person accepted a discrepancy; it must not rewrite the
counts and flatter the accuracy figure this system exists to report.

**Entrances animate transform, never opacity from 0.** Six components faded in;
when the animation did not run the sign-in card was present, invisible, with
nothing in the console, and the portal was unusable.

**Colours come from tokens**, defined in `web/src/index.css` and exposed through
`web/tailwind.config.js`. No raw hex in components. `bg-ink` means "text colour",
which inverts in dark mode — using it as a dark background is what turned the
camera wall into white rectangles. The three surfaces that are deliberately
single-theme (video wells, login panel, navigation rail) use literals on purpose
and say so in a comment.

## Security

Passwords are Argon2id hashes and are never returned, logged, or readable —
a reset mints a temporary password shown exactly once. Tokens are minted against
the password they belong to, so a reset or a disable ends sessions already open.
The last enabled admin cannot be demoted or disabled, and nobody can lock
themselves out. Settings endpoints report whether a secret is configured, never
its value; a test asserts none leak.

Every action that changes a count or the setup is recorded in the audit log with
the user who took it. Audit writes are guarded at the route: the action has
already succeeded, so a failed audit write is logged, never turned into a failed
request.

## Architecture

This is a modular monolith for the control plane plus separate media, storage,
identity and AI services. That is deliberate. Do not split it further without
evidence of a real scaling or availability need — extracting the one seam that
had evidence (the analysis worker, which was pegging the API at 383% CPU while
serving the portal) surfaced three latent correctness bugs, and every further
split carries the same cost.

`IVAAS_RUN_ANALYSIS_WORKER` keeps a single-process deployment working; compose
sets it false on the API and runs a separate `worker`, which can be scaled.

## Before claiming a capability exists

Check `docs/ARCHITECTURE.md` and the README. Several things that look broken are
not: cameras are not installed yet, so screens show empty states, and plates are
missing because no camera faces one, not because the reader is wrong.
