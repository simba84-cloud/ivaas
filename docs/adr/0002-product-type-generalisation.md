# ADR 0002: One codebase; a product type, not "crate"

- **Status:** proposed. Implementation waits for the NFL site visit, which confirms what NFL counts.
- **Date:** 2026-11-09
- **Source:** `docs/modules/nfl-snacks.md`; the project sponsor's direction (2 November 2026) to build NFL Snacks & Treats as an IVaaS module

## Context

IVaaS was built for one thing: counting bread crates at the Bakers Inn loading bay.
NFL Snacks & Treats needs product counted at four points, including a conveyor.
Detection, tracking, counting, reconciliation, audit, tenancy, reports and billing
all carry over. What does not carry over is the word "crate", which is built into the
model and the screens:

- `services/api/src/ivaas/domain/models.py`: `CrateCrossing`, and its `crates` field
- `services/api/src/ivaas/domain/manifests.py`: expected and counted crates, crate balances
- `services/api/src/ivaas/domain/billing.py`
- `services/api/src/ivaas/adapters/tally_template.py`, `adapters/tally_csv.py`
- `services/api/src/ivaas/adapters/http/schemas.py`: `crates` on the ingest payload (capped at 40 per crossing) and `crates_today`
- `services/api/src/ivaas/application/overview.py`, `adapters/poc_report.py`, `adapters/reports.py`
- 37 files under `web/src` that mention "crate"

Two ways to serve NFL were considered:
- **A separate project:** a fork of IVaaS for snacks.
- **One codebase:** NFL becomes a configuration of the existing platform.

## Decision

1. **One codebase.** NFL is a tenant of the same IVaaS platform, isolated by ADR 0001.
   A fork would duplicate every security and correctness fix. The only differences
   are a model, possibly a counter, and the unit the count is expressed in.
2. **A product type is configured per bay.** It names:
   - the unit, singular and plural (crate/crates, packet/packets, carton/cartons);
   - the detector model;
   - the counter strategy the edge pipeline uses.

   A tenant default is set, and a bay may override it. Bakers Inn's existing bays
   default to "crate", so nothing changes for them.
3. **Generic code uses neutral names.** Generic code counts `units`, and the
   product-type label is what the user sees. Code that really is about crates, such
   as stacked layers or crate balances against trucks, keeps its crate names and stays
   in its own bounded place.
4. **The counter is chosen by configuration.** The pipeline already picks
   `StackCrossingCounter`, `StackPresenceCounter` or `LineCrossingCounter` per
   deployment in `services/pipeline/src/ivaas_pipeline/__main__.py`. That choice moves
   from an implicit `"count"` key to the bay's product type. New strategies go behind
   the existing `CrossingCounter` port.

## Consequences

- **Bakers Inn.** Bakers Inn screens and reports read exactly as before. A test pins
  this down before any rename lands.
- **Migration.** The rename needs a migration (one statement per `op.execute`), and
  the product-type table needs `tenant_id` with forced RLS and the `tenant_isolation`
  policy, or `tests/test_rls.py` fails.
- **Ingest payload.** The payload's `crates` field becomes `units`, keeping `crates`
  as a deprecated alias until every edge node is updated, because edge nodes in the
  field post the old shape.
- **Reports.** Reports and exports take their unit label from the product type. A
  report never mixes units it cannot add up: totals are per product type.
- **Not covered here.** This ADR does not decide how NFL defines a session, or what
  its reference count is. Those wait for the site visit (`docs/modules/nfl-snacks.md` §9).
