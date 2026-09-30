"""TokenVerifier for enrolled edge nodes (X-IVaaS-Node).

The credential names its node; the node is looked up on every request, so revoking
it ends its access at once, exactly as disabling an account does. What the node may
do comes from where it was enrolled: the integration role, bound to that one site.
"""

from __future__ import annotations

from ivaas.domain.edge import CREDENTIAL_PREFIX, parse_secret
from ivaas.domain.rbac import Role, RoleBinding
from ivaas.domain.tenancy import ScopeType
from ivaas.ports.auth import AuthError, Principal
from ivaas.ports.edge import EdgeStore
from ivaas.tenancy import system_context


class NodeCredentialVerifier:
    def __init__(self, edge: EdgeStore) -> None:
        self._edge = edge

    async def verify(self, token: str) -> Principal:
        parsed = parse_secret(token, CREDENTIAL_PREFIX)
        node = None
        if parsed is not None:
            with system_context():  # the node's tenant is what we are finding out
                node = await self._edge.get_node(parsed[0])
        # one answer for unknown, wrong secret and revoked alike
        if node is None or parsed is None or not node.authenticates(parsed[1]):
            raise AuthError("invalid node credential")
        return Principal(
            subject=f"node:{node.id}",
            name=node.name,
            bindings=(RoleBinding(Role.INTEGRATION, ScopeType.SITE, node.site_id),),
            tenant_id=node.tenant_id,
            is_service=True,
        )
