"""Edge nodes and enrollment tokens on Postgres, plus the in-memory twin.

Both tables are tenant-owned and row-level secured. Two lookups happen before a
tenant is known (redeeming a token, authenticating a node) and run in
`system_context()`; each record carries its tenant so the caller can then narrow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.edge import EdgeNode, EnrollmentToken, NodeStatus
from ivaas.tenancy import current_tenant, is_system, require_tenant


class EnrollmentTokenRow(Base):
    __tablename__ = "edge_enrollment_tokens"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    site_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    bay_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by_node: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EdgeNodeRow(Base):
    __tablename__ = "edge_nodes"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    site_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    bay_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(120))
    hostname: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16))
    credential_hash: Mapped[str] = mapped_column(String(64))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_report: Mapped[Any] = mapped_column(JSONB)
    config: Mapped[Any] = mapped_column(JSONB)
    config_version: Mapped[str] = mapped_column(String(16))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_TOKEN_FIELDS = (
    "id",
    "tenant_id",
    "site_id",
    "bay_id",
    "name",
    "token_hash",
    "expires_at",
    "used_at",
    "used_by_node",
    "created_by",
    "created_at",
)
_NODE_FIELDS = (
    "id",
    "tenant_id",
    "site_id",
    "bay_id",
    "name",
    "hostname",
    "credential_hash",
    "enrolled_at",
    "last_seen_at",
    "last_report",
    "config",
    "revoked_at",
)


def _stamp(record: Any) -> None:
    """A record is stored in the tenant it belongs to, which is the one in context."""
    if record.tenant_id is None:
        record.tenant_id = require_tenant()


def _token(r: EnrollmentTokenRow) -> EnrollmentToken:
    return EnrollmentToken(**{f: getattr(r, f) for f in _TOKEN_FIELDS})


def _node(r: EdgeNodeRow) -> EdgeNode:
    values = {f: getattr(r, f) for f in _NODE_FIELDS}
    values["last_report"] = dict(values["last_report"] or {})
    values["config"] = dict(values["config"] or {})
    return EdgeNode(status=NodeStatus(r.status), **values)


class PostgresEdgeStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save_token(self, token: EnrollmentToken) -> None:
        _stamp(token)
        values = {f: getattr(token, f) for f in _TOKEN_FIELDS}
        stmt = insert(EnrollmentTokenRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[EnrollmentTokenRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def get_token(self, token_id: UUID) -> EnrollmentToken | None:
        async with self._sm() as db:
            r = await db.get(EnrollmentTokenRow, token_id)
        return _token(r) if r else None

    async def save_node(self, node: EdgeNode) -> None:
        _stamp(node)
        values = {f: getattr(node, f) for f in _NODE_FIELDS}
        values |= {"status": node.status.value, "config_version": node.config_version}
        stmt = insert(EdgeNodeRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[EdgeNodeRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def get_node(self, node_id: UUID) -> EdgeNode | None:
        async with self._sm() as db:
            r = await db.get(EdgeNodeRow, node_id)
        return _node(r) if r else None

    async def list_nodes(self) -> list[EdgeNode]:
        async with self._sm() as db:
            rows = (await db.scalars(select(EdgeNodeRow).order_by(EdgeNodeRow.name))).all()
        return [_node(r) for r in rows]


class InMemoryEdgeStore:
    """Sees what row-level security would let it see, like the user store."""

    def __init__(self) -> None:
        self._tokens: dict[UUID, EnrollmentToken] = {}
        self._nodes: dict[UUID, EdgeNode] = {}

    @staticmethod
    def _visible(record: Any) -> bool:
        return is_system() or (
            record.tenant_id is not None and record.tenant_id == current_tenant()
        )

    async def save_token(self, token: EnrollmentToken) -> None:
        _stamp(token)
        self._tokens[token.id] = token

    async def get_token(self, token_id: UUID) -> EnrollmentToken | None:
        token = self._tokens.get(token_id)
        return token if token is not None and self._visible(token) else None

    async def save_node(self, node: EdgeNode) -> None:
        _stamp(node)
        self._nodes[node.id] = node

    async def get_node(self, node_id: UUID) -> EdgeNode | None:
        node = self._nodes.get(node_id)
        return node if node is not None and self._visible(node) else None

    async def list_nodes(self) -> list[EdgeNode]:
        return sorted((n for n in self._nodes.values() if self._visible(n)), key=lambda n: n.name)
