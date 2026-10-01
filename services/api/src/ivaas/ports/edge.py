"""Port for edge nodes and the tokens that enrol them."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ivaas.domain.edge import EdgeNode, EnrollmentToken


class EdgeStore(Protocol):
    async def save_token(self, token: EnrollmentToken) -> None: ...

    async def get_token(self, token_id: UUID) -> EnrollmentToken | None: ...

    async def list_tokens(self) -> list[EnrollmentToken]: ...

    async def save_node(self, node: EdgeNode) -> None: ...

    async def get_node(self, node_id: UUID) -> EdgeNode | None: ...

    async def list_nodes(self) -> list[EdgeNode]: ...
