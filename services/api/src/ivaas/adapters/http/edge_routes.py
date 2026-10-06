"""Edge nodes: enrolment, fleet health and the configuration a node runs (M2).

People manage nodes with the portal; nodes call three endpoints of their own
(enroll, heartbeat, config). A node is bound to the site it was enrolled at and
can neither see nor report for any other.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.schemas import (
    EdgeConfigIn,
    EdgeNodeOut,
    EnrolledOut,
    EnrollIn,
    EnrollmentTokenIn,
    EnrollmentTokenOut,
    HeartbeatIn,
    HeartbeatOut,
    NodeCameraOut,
)
from ivaas.adapters.http.scope import require_bay, require_site, site_scope
from ivaas.application.availability import NodeAvailability, RecordAvailability
from ivaas.domain.audit import AuditAction
from ivaas.domain.availability import Availability
from ivaas.domain.edge import (
    TOKEN_PREFIX,
    EdgeError,
    EdgeNode,
    EnrollmentToken,
    parse_secret,
)
from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import Permission as P
from ivaas.ports.auth import Principal
from ivaas.tenancy import system_context, tenant_context

Audit = Callable[..., Awaitable[None]]

#: One answer for every way a token can be wrong: unknown, used, expired or forged.
INVALID_TOKEN = "this enrollment token is not valid; create a new one in the portal"


def _node_id(principal: Principal) -> UUID:
    if not principal.subject.startswith("node:"):
        raise HTTPException(403, "only an enrolled edge node can call this")
    return UUID(principal.subject.removeprefix("node:"))


async def _own_node(c: Any, principal: Principal) -> EdgeNode:
    node = await c.edge.get_node(_node_id(principal))
    if node is None:  # revoked between authentication and here, or deleted
        raise HTTPException(401, "invalid node credential")
    return node


log = logging.getLogger(__name__)


class OutageOut(BaseModel):
    start: datetime
    end: datetime
    minutes: float
    #: the backlog the node brought back, and whether it then drained (no data lost)
    backlog: int | None
    drained: bool | None


class UptimeOut(BaseModel):
    #: share of the window it was working, as a percentage; null for an empty window
    uptime_pct: float | None
    down_minutes: float
    outages: list[OutageOut]

    @classmethod
    def of(cls, a: Availability) -> UptimeOut:
        return cls(
            uptime_pct=None if a.uptime is None else round(a.uptime * 100, 2),
            down_minutes=round(a.down_minutes, 1),
            outages=[
                OutageOut(
                    start=o.start,
                    end=o.end,
                    minutes=round(o.minutes, 1),
                    backlog=o.backlog,
                    drained=o.drained,
                )
                for o in a.outages
            ],
        )


class CameraUptimeOut(UptimeOut):
    api_camera_id: str
    name: str | None


class NodeAvailabilityOut(BaseModel):
    start: datetime
    end: datetime
    node: UptimeOut
    cameras: list[CameraUptimeOut]


def _stream_uri(c: Any, stream_path: str) -> str:
    return f"{c.settings.media_rtsp_url.rstrip('/')}/{stream_path}"


def add_edge_routes(app: FastAPI, get_container: Callable[[Request], Any], audit: Audit) -> None:
    async def node_out(c: Any, node: EdgeNode) -> EdgeNodeOut:
        report = node.last_report
        names: dict[str, str] = {}
        for cam in node.cameras:
            try:
                found = await c.cameras.get(UUID(cam.api_camera_id))
            except ValueError:
                found = None
            if found is not None:
                names[cam.api_camera_id] = found.name
        return EdgeNodeOut(
            id=node.id,
            name=node.name,
            hostname=node.hostname,
            site_id=node.site_id,
            bay_id=node.bay_id,
            status=node.status.value,
            health=node.health(c.clock.now()).value,
            enrolled_at=node.enrolled_at,
            last_seen_at=node.last_seen_at,
            version=report.get("version") or None,
            uptime_s=report.get("uptime_s"),
            spool_pending=report.get("spool_pending"),
            cameras=[
                NodeCameraOut(
                    api_camera_id=cam.api_camera_id,
                    name=names.get(cam.api_camera_id),
                    connected=cam.connected,
                    fps=cam.fps,
                    lag_s=cam.lag_s,
                )
                for cam in node.cameras
            ],
            config=node.config,
            config_version=node.config_version,
            applied_config_version=report.get("config_version"),
            config_drift=node.config_drift,
            models=report.get("models") or {},
            model_error=report.get("model_error"),
            can_roll_back=bool(node.previous_config),
        )

    async def managed_node(c: Any, principal: Principal, node_id: UUID, perm: P) -> EdgeNode:
        """A node the caller may manage; anything else is simply not found."""
        node = await c.edge.get_node(node_id)
        if node is None or not principal.can(perm, site_scope(node.site_id)):
            raise NotFoundError(f"node {node_id} not found")
        return node

    @app.get(
        "/api/v1/edge/nodes/{node_id}/availability",
        response_model=NodeAvailabilityOut,
        dependencies=[Depends(require(P.TOPOLOGY_READ, scoped=True))],
    )
    async def node_availability(
        node_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> NodeAvailabilityOut:
        """How long the node, and each camera it runs, was working over a window (the
        last 24 hours unless given). Time with no heartbeat is down, never assumed up."""
        node = await managed_node(c, principal, node_id, P.TOPOLOGY_READ)
        end = end or c.clock.now()
        start = start or end - timedelta(hours=24)
        if end <= start or end - start > timedelta(days=62):
            raise HTTPException(422, "give a window of up to 62 days, start before end")
        whole, cams = await NodeAvailability(c.availability)(node.id, start, end)
        names = {
            str(x.id): x.name for x in [await c.cameras.get(cid) for cid in cams] if x is not None
        }
        return NodeAvailabilityOut(
            start=start,
            end=end,
            node=UptimeOut.of(whole),
            cameras=[
                CameraUptimeOut(
                    api_camera_id=str(cid), name=names.get(str(cid)), **UptimeOut.of(a).model_dump()
                )
                for cid, a in sorted(cams.items(), key=lambda kv: names.get(str(kv[0])) or "")
            ],
        )

    # --- people: create a token, list, revoke, configure ----------------------------
    @app.post(
        "/api/v1/sites/{site_id}/enrollment-tokens",
        response_model=EnrollmentTokenOut,
        status_code=201,
        dependencies=[Depends(require(P.DEVICE_REGISTER, scoped=True))],
    )
    async def create_enrollment_token(
        site_id: UUID,
        body: EnrollmentTokenIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> EnrollmentTokenOut:
        """A single-use token for one node at this site, shown once."""
        await require_site(c, principal, P.DEVICE_REGISTER, site_id)
        if body.bay_id is not None:
            bay = await c.bays.get(body.bay_id)
            if bay is None or bay.site_id != site_id:
                raise NotFoundError(f"bay {body.bay_id} not found at this site")
        record, token = EnrollmentToken.issue(
            site_id=site_id,
            bay_id=body.bay_id,
            name=body.name,
            ttl=timedelta(hours=body.ttl_hours),
            by=principal.name,
            now=c.clock.now(),
        )
        await c.edge.save_token(record)
        await audit(
            c,
            principal.name,
            AuditAction.EDGE_TOKEN_CREATED,
            record.name,
            expires_at=record.expires_at.isoformat(),
            site_id=str(site_id),
        )
        return EnrollmentTokenOut(
            token=token,
            name=record.name,
            site_id=site_id,
            bay_id=record.bay_id,
            expires_at=record.expires_at,
        )

    @app.get(
        "/api/v1/edge/nodes",
        response_model=list[EdgeNodeOut],
        dependencies=[Depends(require(P.TOPOLOGY_READ, scoped=True))],
    )
    async def list_nodes(
        principal: Principal = Depends(current_principal), c: Any = Depends(get_container)
    ) -> list[EdgeNodeOut]:
        mine = [
            n
            for n in await c.edge.list_nodes()
            if principal.can(P.TOPOLOGY_READ, site_scope(n.site_id))
        ]
        return [await node_out(c, n) for n in mine]

    @app.delete(
        "/api/v1/edge/nodes/{node_id}",
        status_code=204,
        dependencies=[Depends(require(P.DEVICE_REGISTER, scoped=True))],
    )
    async def revoke_node(
        node_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> Response:
        """The node's credential stops working on its next request."""
        node = await managed_node(c, principal, node_id, P.DEVICE_REGISTER)
        if node.revoked_at is None:
            node.revoke(c.clock.now())
            await c.edge.save_node(node)
            await audit(
                c, principal.name, AuditAction.NODE_REVOKED, node.name, node_id=str(node.id)
            )
        return Response(status_code=204)

    @app.put(
        "/api/v1/edge/nodes/{node_id}/config",
        response_model=EdgeNodeOut,
        dependencies=[Depends(require(P.DEVICE_CALIBRATE, scoped=True))],
    )
    async def set_node_config(
        node_id: UUID,
        body: EdgeConfigIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> EdgeNodeOut:
        """What the node should run. It notices within a minute and restarts onto it."""
        node = await managed_node(c, principal, node_id, P.DEVICE_CALIBRATE)
        for cam in body.cameras:  # a counting camera must know where to count
            if cam.line is None and cam.zone is None:
                raise HTTPException(422, f"camera {cam.api_camera_id} needs a line or a zone")
        bays: set[UUID] = set()
        for cam in [*body.cameras, *body.lpr_cameras]:
            camera = await c.cameras.get(cam.api_camera_id)
            bay = await c.bays.get(camera.bay_id) if camera else None
            if bay is None or bay.site_id != node.site_id:
                raise HTTPException(422, f"camera {cam.api_camera_id} is not at this node's site")
            bays.add(bay.id)
        if len(bays) > 1 or (node.bay_id and bays - {node.bay_id}):
            raise HTTPException(422, "a node counts one bay; every camera must be in it")
        for ref in (body.model.version_id, body.layers_model_id):
            if ref is not None and await c.ml_models.get(ref) is None:
                raise HTTPException(422, f"model version {ref} is not registered")
        before = node.config_version
        if not node.set_config(body.model_dump(mode="json", exclude_none=True)):
            return await node_out(c, node)  # unchanged: nothing to roll back to, no audit
        await c.edge.save_node(node)
        await audit(
            c,
            principal.name,
            AuditAction.NODE_CONFIG_CHANGED,
            node.name,
            before=before,
            after=node.config_version,
        )
        return await node_out(c, node)

    @app.post(
        "/api/v1/edge/nodes/{node_id}/rollback",
        response_model=EdgeNodeOut,
        dependencies=[Depends(require(P.DEVICE_CALIBRATE, scoped=True))],
    )
    async def roll_back_node(
        node_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> EdgeNodeOut:
        """Back to the configuration (and so the model) before the last change. The node
        picks it up on its next heartbeat; rolling back again returns to the newer one."""
        node = await managed_node(c, principal, node_id, P.DEVICE_CALIBRATE)
        before = node.config_version
        try:
            node.rollback()
        except EdgeError as exc:
            raise HTTPException(409, str(exc)) from exc
        await c.edge.save_node(node)
        await audit(
            c,
            principal.name,
            AuditAction.NODE_ROLLED_BACK,
            node.name,
            before=before,
            after=node.config_version,
        )
        return await node_out(c, node)

    async def model_ref(c: Any, model_id: str) -> dict:
        """Everything the node needs to fetch, verify and load a registered model."""
        model = await c.ml_models.get(UUID(model_id))
        if model is None:  # removed since the config was saved
            raise HTTPException(409, f"model version {model_id} is no longer registered")
        return {
            "version_id": str(model.id),
            "name": model.name,
            "version": model.version,
            "sha256": model.sha256,
            "size_bytes": model.size_bytes,
            "url": f"/api/v1/edge/models/{model.id}/file",
            "meta": model.meta,
        }

    # --- nodes: enroll, heartbeat, fetch config --------------------------------------
    @app.post("/api/v1/edge/enroll", response_model=EnrolledOut, status_code=201)
    async def enroll(body: EnrollIn, c: Any = Depends(get_container)) -> EnrolledOut:
        """Trade a single-use token for this node's credential. No other auth: the
        token is the proof, and it is spent here."""
        parsed = parse_secret(body.token.strip(), TOKEN_PREFIX)
        token = None
        if parsed is not None:
            with system_context():  # the token's tenant is what we are finding out
                token = await c.edge.get_token(parsed[0])
        now = c.clock.now()
        if token is None or parsed is None or not token.usable(parsed[1], now):
            raise HTTPException(401, INVALID_TOKEN)
        assert token.tenant_id is not None
        with tenant_context(token.tenant_id):
            node, credential = EdgeNode.enrol(token, body.hostname, now)
            node.last_report = {"version": body.version} if body.version else {}
            await c.edge.save_node(node)
            await c.edge.save_token(token)
            await audit(
                c,
                f"node:{node.name}",
                AuditAction.NODE_ENROLLED,
                node.name,
                hostname=node.hostname,
                node_id=str(node.id),
                site_id=str(node.site_id),
            )
        return EnrolledOut(
            node_id=node.id,
            credential=credential,
            name=node.name,
            site_id=node.site_id,
            bay_id=node.bay_id,
            entitlement_public_key=c.entitlement_signer.public_key,
        )

    @app.post(
        "/api/v1/edge/heartbeat",
        response_model=HeartbeatOut,
        dependencies=[Depends(require(P.INGEST_WRITE, scoped=True))],
    )
    async def edge_heartbeat(
        body: HeartbeatIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> HeartbeatOut:
        node = await _own_node(c, principal)
        report = body.model_dump(mode="json")
        node.record_heartbeat(report, c.clock.now())
        await c.edge.save_node(node)
        # the history behind the POC's reliability figure. A node is credited only for
        # cameras it is configured with; a failure here is logged, never a failed beat.
        mine = {
            e.get("api_camera_id")
            for e in [*node.config.get("cameras", []), *node.config.get("lpr_cameras", [])]
        }
        # a copy: the node keeps the report it sent, every camera in it
        counted = {
            **report,
            "cameras": [x for x in report["cameras"] if x["api_camera_id"] in mine],
        }
        try:
            await RecordAvailability(c.availability)(node.id, counted, c.clock.now())
        except Exception:
            log.exception("could not record availability for node %s", node.id)
        return HeartbeatOut(config_version=node.config_version)

    @app.get(
        "/api/v1/edge/config",
        dependencies=[Depends(require(P.INGEST_WRITE, scoped=True))],
    )
    async def edge_config(
        principal: Principal = Depends(current_principal), c: Any = Depends(get_container)
    ) -> dict:
        """The node's configuration in pipeline.json's shape, with everything the API
        knows filled in: the bay, each camera's stream address and key."""
        node = await _own_node(c, principal)
        cfg = dict(node.config)
        if not cfg:
            # nothing configured yet: say so rather than start a node counting nothing
            return {"config_version": node.config_version, "configured": False}

        async def camera(entry: dict) -> tuple[dict, UUID]:
            cam = await c.cameras.get(UUID(entry["api_camera_id"]))
            if cam is None:  # removed since the config was saved
                raise HTTPException(409, f"camera {entry['api_camera_id']} no longer exists")
            await require_bay(c, principal, P.INGEST_WRITE, cam.bay_id)
            key = entry.get("key") or cam.stream_path.rsplit("/", 1)[-1]
            rendered = {**entry, "key": key, "uri": _stream_uri(c, cam.stream_path)}
            return rendered, cam.bay_id

        cameras, lpr, bays = [], [], set()
        for entry in cfg.get("cameras", []):
            rendered, bay = await camera(entry)
            cameras.append(rendered)
            bays.add(bay)
        for entry in cfg.get("lpr_cameras", []):
            rendered, bay = await camera(entry)
            lpr.append(rendered)
            bays.add(bay)
        bay_id = node.bay_id or (next(iter(bays)) if bays else None)
        # the plan's channels (T7.3): the node runs no more than it is entitled to, in
        # the order it was configured with, and is told which it may not run
        ents = await c.billing().entitlements()
        refused: list[str] = []
        if ents is not None:
            for kind, served in (("od_channels", cameras), ("lpr_channels", lpr)):
                over = served[ents.limits[kind] :]
                refused += [x["api_camera_id"] for x in over]
                del served[ents.limits[kind] :]
            if refused:
                log.warning(
                    "node %s: %d channel(s) beyond the plan not served", node.id, len(refused)
                )
        if cfg.get("model", {}).get("version_id"):
            arch = cfg["model"].get("arch", "rtdetr")
            cfg["model"] = {**await model_ref(c, cfg["model"]["version_id"]), "arch": arch}
        if cfg.get("layers_model_id"):
            cfg["layers_model_ref"] = await model_ref(c, cfg.pop("layers_model_id"))
        served = {
            **cfg,
            "configured": True,
            "config_version": node.config_version,
            "bay_id": str(bay_id) if bay_id else None,
            "cameras": cameras,
            "lpr_cameras": lpr,
            #: configured, but beyond the plan's channels: not run until it is upgraded
            "not_entitled": refused,
        }
        # signed, with this configuration's hash inside: what the node may run when it
        # starts without the cloud (T7.7)
        served["entitlement"] = c.entitlement_signer.snapshot(
            node_id=str(node.id),
            tenant_id=str(node.tenant_id),
            plan=ents.plan if ents else None,
            limits=({k: ents.limits[k] for k in ("od_channels", "lpr_channels")} if ents else None),
            config=served,
            now=c.clock.now(),
        )
        return served
