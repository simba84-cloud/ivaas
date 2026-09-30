# ADR 0001: Pooled tenancy, isolated by Postgres row-level security

- **Status:** accepted, implemented in migration `0012_tenancy`
- **Date:** 2026-09-29
- **Source:** `docs/IVaaS_Solution_Proposal.md` §2.2, §3, §4 and milestone M1

## Context

IVaaS is a commercial platform, and Bakers Inn is its first tenant, reached through
the partner LITZIM. The proposal says why tenancy cannot wait: adding it after a
single-customer build means rewriting the data layer, auth and APIs. A cross-tenant
leak is the one failure a SaaS does not recover from.

Before this change the system was single-tenant: nothing carried a tenant, three
flat roles, and every live event went to every connected portal.

## Decision

1. **Pooled by default.** Shared database and services, with `tenant_id` on every
   tenant-owned row. A silo deployment (a dedicated database) runs the same code, and
   is an option for enterprise tenants later.
2. **The database isolates, not the queries.** Each tenant table has row-level
   security enabled and forced, and one policy, `tenant_isolation`:
   `ivaas_tenant_visible(tenant_id)`. Each transaction runs as `ivaas_app` (no
   superuser, no BYPASSRLS) with `app.tenant_id` set locally. `tenant_id` defaults
   from that setting, so inserts land in the right tenant without the code naming it.
3. **The tenant comes from the verified identity only.** It is never taken from a
   header, query or body.
4. **Scoped RBAC in one module.** Roles are bindings with a scope (platform, partner,
   tenant, site or bay), inherited downward. The §4.2 matrix is data in
   `domain/rbac.py`, and a test holds it to the document cell by cell. The API checks
   permissions, and RLS stands behind it: one bug does not leak data.
5. **Cross-tenant ids answer 404**, identical to an id that does not exist.
6. **An explicit escape hatch.** `system_context()` sets `app.scope = 'system'`. It is
   used only to sign in, to claim from the shared job queue, and to provision.

## Consequences

- Repository code did not change to become tenant-safe, and a new repository cannot
  forget a tenant filter.
- `tests/test_rls.py` fails CI when any table with a `tenant_id` column lacks forced
  RLS or a policy (T1.3). It also proves the app role reads nothing when no tenant is
  set (T1.2).
- `tests/test_isolation.py` enumerates every route. A new route that takes an id
  fails the suite until it is classified (T1.1).
- A migration that updates data must set `app.scope` to `system` first, because RLS
  applies to the owner too once it is forced.
- Background work needs a tenant. The sweeps loop over operating tenants, and the
  worker adopts each job's tenant.
- Known gaps are listed in ARCHITECTURE.md §6, item 11: the platform-wide
  stream-path uniqueness, foreign keys without `tenant_id`, and the shared login for
  the app role.

## Alternatives considered

- **Filter in every query.** One forgotten `WHERE` leaks, and the proposal requires two
  independent layers.
- **Schema or database per tenant from day one.** This costs more for SMB tenants and
  multiplies migrations. It is kept as the silo option instead.
