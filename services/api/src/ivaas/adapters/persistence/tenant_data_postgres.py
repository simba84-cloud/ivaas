"""A whole tenant's rows, generically: export (T8.4), import into another database, and
purge (T8.5).

"Every table with a `tenant_id`" is read from the catalogue, not listed by hand, so a
table added next month is exported and purged without anyone remembering to. The
same rule already makes `tests/test_rls.py` demand row-level security on it.

Rows travel as Postgres's own JSON (`row_to_json`) and come back through
`json_populate_recordset`, so every type (uuid, timestamptz, numeric, jsonb, bytea)
round-trips exactly as the database writes it, with no Python in between to drift.

Secrets never leave: password, credential and token hashes, webhook secrets, camera
URLs (they carry camera passwords), face embeddings, and setting overrides named like
a key or a secret. Each is exported as null and listed in the manifest as withheld;
an import fills a required one with a marker, so the row still counts.

Purge runs as the database owner in the system scope: the application role cannot
delete invoices, usage or audit entries, and should not be able to.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

#: columns whose values are secrets, whatever table they are in
WITHHELD_COLUMNS = frozenset(
    {"password_hash", "credential_hash", "token_hash", "secret", "source_url", "embedding"}
)
#: setting overrides that hold a secret, by key
_SECRET_SETTING = re.compile(r"(key|secret|token|password)", re.IGNORECASE)
#: what an import writes into a required withheld column
WITHHELD_MARK = "withheld-in-export"

_TENANT_TABLES = text(
    """
    SELECT c.relname
      FROM pg_class c
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
     WHERE c.relnamespace = 'public'::regnamespace
       AND c.relkind IN ('r', 'p')
       AND NOT c.relispartition
     ORDER BY c.relname
    """
)
_FOREIGN_KEYS = text(
    """
    SELECT child.relname AS child, parent.relname AS parent
      FROM pg_constraint k
      JOIN pg_class child ON child.oid = k.conrelid
      JOIN pg_class parent ON parent.oid = k.confrelid
     WHERE k.contype = 'f' AND child.relnamespace = 'public'::regnamespace
    """
)
_COLUMNS = text(
    """
    SELECT a.attname, a.attnotnull, format_type(a.atttypid, a.atttypmod), a.attgenerated <> ''
      FROM pg_attribute a
     WHERE a.attrelid = ('public.' || quote_ident(:table))::regclass
       AND a.attnum > 0 AND NOT a.attisdropped
     ORDER BY a.attnum
    """
)


def _ident(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"unexpected table or column name {name!r}")
    return f'"{name}"'


def in_parent_first_order(tables: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Parents before children, so a load never meets a missing parent and a purge,
    run backwards, never deletes a parent with children left."""
    wanted = set(tables)
    parents = {t: {p for c, p in edges if c == t and p in wanted and p != t} for t in tables}
    ordered: list[str] = []
    while parents:
        ready = sorted(t for t, ps in parents.items() if not ps - set(ordered))
        if not ready:
            raise ValueError(f"foreign keys form a cycle among {sorted(parents)}")
        ordered += ready
        for t in ready:
            del parents[t]
    return ordered


class PostgresTenantData:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        # the raw sessionmaker: these run as the owner, scoped by hand below
        self._sm = sm

    async def _system(self, db: AsyncSession) -> None:
        await db.execute(text("SELECT set_config('app.scope', 'system', true)"))

    async def tables(self) -> list[str]:
        """Every tenant-owned table, parents first."""
        async with self._sm() as db:
            tables = list((await db.execute(_TENANT_TABLES)).scalars())
            edges = [(r.child, r.parent) for r in await db.execute(_FOREIGN_KEYS)]
        return in_parent_first_order(tables, edges)

    async def counts(self, tenant_id: UUID) -> dict[str, int]:
        """Rows the tenant has in each table, and its own tenant row: what a purge must
        bring to zero and an import must reproduce."""
        out: dict[str, int] = {}
        async with self._sm.begin() as db:
            await self._system(db)
            for t in await self.tables():
                q = f"SELECT count(*) FROM {_ident(t)} WHERE tenant_id = :t"
                out[t] = (await db.execute(text(q), {"t": tenant_id})).scalar_one()
            q = "SELECT count(*) FROM tenants WHERE id = :t"
            out["tenants"] = (await db.execute(text(q), {"t": tenant_id})).scalar_one()
        return out

    async def dump(self, tenant_id: UUID) -> tuple[dict[str, list[dict[str, Any]]], dict]:
        """-> (rows by table, parents first; what was withheld, by table)."""
        rows: dict[str, list[dict[str, Any]]] = {}
        withheld: dict[str, list[str]] = {}
        async with self._sm.begin() as db:
            await self._system(db)
            tenant = (
                await db.execute(
                    text("SELECT row_to_json(t) FROM tenants t WHERE id = :t"), {"t": tenant_id}
                )
            ).scalar_one()
            rows["partners"] = []
            if tenant.get("partner_id"):
                q = "SELECT row_to_json(p) FROM partners p WHERE id = :p"
                rows["partners"] = [
                    (await db.execute(text(q), {"p": tenant["partner_id"]})).scalar_one()
                ]
            rows["tenants"] = [tenant]
            for t in await self.tables():
                q = f"SELECT row_to_json(r) FROM {_ident(t)} r WHERE tenant_id = :t"
                found = [r for (r,) in await db.execute(text(q), {"t": tenant_id})]
                hidden = sorted({c for r in found for c in r if c in WITHHELD_COLUMNS})
                for r in found:
                    for c in hidden:
                        r[c] = None
                if t == "platform_settings":
                    secret = [r for r in found if _SECRET_SETTING.search(str(r.get("key")))]
                    for r in secret:
                        r["value"] = None
                    if secret:
                        hidden.append("value, for keys naming a key, secret, token or password")
                if hidden:
                    withheld[t] = hidden
                rows[t] = found
        return rows, withheld

    async def load(self, rows: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
        """Into this database, parents first, as the owner. A scratch environment's
        import: the partner may exist already, nothing else may."""
        loaded: dict[str, int] = {}
        async with self._sm.begin() as db:
            await self._system(db)
            for table, found in rows.items():
                if not found:
                    loaded[table] = 0
                    continue
                cols = [r for r in await db.execute(_COLUMNS, {"table": table})]
                for r in found:  # a required withheld column gets the marker
                    for name, notnull, typ, _generated in cols:
                        if notnull and r.get(name) is None and name in WITHHELD_COLUMNS:
                            r[name] = "\\x" if typ == "bytea" else WITHHELD_MARK
                names = ", ".join(_ident(n) for n, _nn, _t, generated in cols if not generated)
                conflict = " ON CONFLICT DO NOTHING" if table == "partners" else ""
                q = (
                    f"INSERT INTO {_ident(table)} ({names}) OVERRIDING SYSTEM VALUE "
                    f"SELECT {names} FROM json_populate_recordset(NULL::{_ident(table)}, "
                    f"CAST(:rows AS json)){conflict}"
                )
                await db.execute(text(q), {"rows": json.dumps(found)})
                loaded[table] = len(found)
        return loaded

    async def purge(self, tenant_id: UUID) -> dict[str, int]:
        """Every row the tenant has, children first, then the tenant itself."""
        deleted: dict[str, int] = {}
        async with self._sm.begin() as db:
            await self._system(db)
            for t in reversed(await self.tables()):
                q = f"DELETE FROM {_ident(t)} WHERE tenant_id = :t"
                deleted[t] = (await db.execute(text(q), {"t": tenant_id})).rowcount
            q = "DELETE FROM tenants WHERE id = :t"
            deleted["tenants"] = (await db.execute(text(q), {"t": tenant_id})).rowcount
        return deleted
