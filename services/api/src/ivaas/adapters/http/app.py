from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, time
from uuid import UUID

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from ivaas.adapters.http.auth import current_principal, require, websocket_principal
from ivaas.adapters.http.schemas import (
    AnalysisJobOut,
    ApproveIn,
    AuthConfigOut,
    BayOut,
    CameraIn,
    CameraOut,
    ChatIn,
    ChatOut,
    CrossingIn,
    DiscoveredDeviceOut,
    DiscoveredStreamOut,
    DiscoverStreamsIn,
    LoginIn,
    MeOut,
    OpenSessionIn,
    OverviewOut,
    PlateReadIn,
    PlatformConfigOut,
    ReconcileIn,
    SessionOut,
    SummaryOut,
    TokenOut,
    ToolUseOut,
)
from ivaas.adapters.storage.objects import LocalObjectStore
from ivaas.adapters.streaming.mediamtx import StreamGatewayError
from ivaas.adapters.streaming.onvif import is_lan_device_url
from ivaas.application.analysis import job_worker
from ivaas.config.container import Container, build_container
from ivaas.config.settings import Settings
from ivaas.domain.models import (
    CameraStatus,
    CrateCrossing,
    DomainError,
    InvalidStreamSourceError,
    NotFoundError,
    PlateRead,
    SessionStatus,
)
from ivaas.ports.assistant import ChatMessage, ChatModelUnavailableError
from ivaas.ports.auth import Principal, Role

log = logging.getLogger(__name__)

CROSSINGS = Counter("ivaas_crate_crossings_total", "Crate crossings ingested", ["direction"])
PLATES = Counter("ivaas_plate_reads_total", "LPR plate reads ingested")


async def _sweep_idle_sessions(container: Container, every_s: float = 60.0) -> None:
    while True:
        await asyncio.sleep(every_s)
        use_case = container.close_idle_sessions
        if use_case is None:
            continue
        try:
            for session in await use_case():
                log.info("auto-closed session %s (%s): truck left", session.id, session.plate)
        except Exception:  # a failing sweep must not kill the API
            log.exception("idle-session sweep failed")


async def _refresh_camera_status(container: Container, every_s: float = 10.0) -> None:
    while True:
        try:
            changed = await container.refresh_camera_status()
            if changed["offline"]:
                log.warning("%d camera(s) stopped streaming", changed["offline"])
        except Exception:
            log.exception("camera status refresh failed")
        await asyncio.sleep(every_s)


def get_container(request: Request) -> Container:
    return request.app.state.container


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    # uvicorn configures only its own loggers; ours would otherwise be silent
    logging.getLogger("ivaas").setLevel(logging.INFO)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = await build_container(settings)
        llm = app.state.container.chat_model
        if llm is not None and hasattr(llm, "check"):
            problem = await llm.check()
            if problem:
                log.warning("assistant will not work until fixed: %s", problem)
        tasks = [
            asyncio.create_task(_sweep_idle_sessions(app.state.container)),
            asyncio.create_task(_refresh_camera_status(app.state.container)),
            asyncio.create_task(job_worker(lambda: app.state.container.run_next_job)),
        ]
        yield
        for t in tasks:
            t.cancel()
        await app.state.container.aclose()

    app = FastAPI(title="IVaaS Core API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidStreamSourceError)
    async def _bad_source(_: Request, exc: InvalidStreamSourceError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def _conflict(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(StreamGatewayError)
    async def _gateway_down(_: Request, exc: StreamGatewayError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # topology ------------------------------------------------------------
    @app.get(
        "/api/v1/bays", response_model=list[BayOut], dependencies=[Depends(require(Role.VIEWER))]
    )
    async def list_bays(c: Container = Depends(get_container)) -> list[BayOut]:
        return [BayOut.of(b) for b in await c.bays.list_all()]

    @app.get(
        "/api/v1/bays/{bay_id}/cameras",
        response_model=list[CameraOut],
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def list_cameras(bay_id: UUID, c: Container = Depends(get_container)) -> list[CameraOut]:
        return [CameraOut.of(cam) for cam in await c.cameras.list_for_bay(bay_id)]

    @app.post(
        "/api/v1/bays/{bay_id}/cameras",
        response_model=CameraOut,
        status_code=201,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def register_camera(
        bay_id: UUID, body: CameraIn, c: Container = Depends(get_container)
    ) -> CameraOut:
        camera = await c.register_camera(bay_id, body.name, body.role, body.source_url)
        return CameraOut.of(camera)

    @app.delete(
        "/api/v1/cameras/{camera_id}", status_code=204, dependencies=[Depends(require(Role.ADMIN))]
    )
    async def remove_camera(camera_id: UUID, c: Container = Depends(get_container)) -> Response:
        await c.remove_camera(camera_id)
        return Response(status_code=204)

    @app.post(
        "/api/v1/discovery/onvif",
        response_model=list[DiscoveredDeviceOut],
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def discover(c: Container = Depends(get_container)) -> list[DiscoveredDeviceOut]:
        return [DiscoveredDeviceOut(**vars(d)) for d in await c.discovery.discover()]

    @app.post(
        "/api/v1/discovery/onvif/streams",
        response_model=list[DiscoveredStreamOut],
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def discover_streams(
        body: DiscoverStreamsIn, c: Container = Depends(get_container)
    ) -> list[DiscoveredStreamOut]:
        if not is_lan_device_url(body.address):
            raise HTTPException(422, "address must be an http(s) URL with a private LAN IP")
        try:
            found = await c.discovery.streams(body.address, body.username, body.password)
        except Exception as exc:  # device unreachable, wrong credentials, non-ONVIF host
            raise HTTPException(502, f"ONVIF device did not answer: {type(exc).__name__}") from exc
        return [DiscoveredStreamOut(**vars(s)) for s in found]

    @app.post(
        "/api/v1/cameras/{camera_id}/heartbeat",
        status_code=204,
        dependencies=[Depends(require(Role.SERVICE))],
    )
    async def heartbeat(camera_id: UUID, c: Container = Depends(get_container)) -> Response:
        camera = await c.cameras.get(camera_id)
        if camera is None:
            raise HTTPException(404, f"camera {camera_id} not found")
        camera.mark_seen(c.clock.now())
        await c.cameras.save(camera)
        return Response(status_code=204)

    # sessions ------------------------------------------------------------
    @app.get(
        "/api/v1/sessions",
        response_model=list[SessionOut],
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def list_sessions(
        bay_id: UUID | None = None,
        status: SessionStatus | None = None,
        limit: int = 50,
        c: Container = Depends(get_container),
    ) -> list[SessionOut]:
        rows = await c.sessions.list_recent(bay_id=bay_id, status=status, limit=min(limit, 500))
        return [SessionOut.of(s) for s in rows]

    @app.post(
        "/api/v1/sessions",
        response_model=SessionOut,
        status_code=201,
        dependencies=[Depends(require(Role.OPERATOR))],
    )
    async def open_session(
        body: OpenSessionIn, c: Container = Depends(get_container)
    ) -> SessionOut:
        return SessionOut.of(await c.open_session(body.bay_id, body.direction))

    @app.post(
        "/api/v1/sessions/{session_id}/close",
        response_model=SessionOut,
        dependencies=[Depends(require(Role.OPERATOR))],
    )
    async def close_session(session_id: UUID, c: Container = Depends(get_container)) -> SessionOut:
        return SessionOut.of(await c.close_session(session_id))

    @app.post(
        "/api/v1/sessions/{session_id}/reconcile",
        response_model=SessionOut,
        dependencies=[Depends(require(Role.OPERATOR))],
    )
    async def reconcile(
        session_id: UUID, body: ReconcileIn, c: Container = Depends(get_container)
    ) -> SessionOut:
        return SessionOut.of(await c.reconcile_session(session_id, body.manual_count))

    @app.post(
        "/api/v1/sessions/{session_id}/approve",
        response_model=SessionOut,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def approve(
        session_id: UUID,
        body: ApproveIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> SessionOut:
        session = await c.approve_session(
            session_id, by=principal.name, reason=body.reason, note=body.note
        )
        return SessionOut.of(session)

    # ingest (called by the AI pipeline) ----------------------------------
    @app.post(
        "/api/v1/ingest/crossings",
        response_model=SessionOut | None,
        dependencies=[Depends(require(Role.SERVICE))],
    )
    async def ingest_crossing(
        body: CrossingIn, c: Container = Depends(get_container)
    ) -> SessionOut | None:
        CROSSINGS.labels(body.direction.value).inc(body.crates)
        crossing = CrateCrossing(
            track_id=body.track_id,
            camera_id=body.camera_id,
            direction=body.direction,
            confidence=body.confidence,
            crossed_at=body.crossed_at,
            crates=body.crates,
        )
        session = await c.record_crossing(body.bay_id, crossing)
        return SessionOut.of(session) if session else None

    @app.post(
        "/api/v1/ingest/plates",
        response_model=SessionOut | None,
        dependencies=[Depends(require(Role.SERVICE))],
    )
    async def ingest_plate(
        body: PlateReadIn, c: Container = Depends(get_container)
    ) -> SessionOut | None:
        PLATES.inc()
        read = PlateRead(body.plate.upper(), body.confidence, body.camera_id, body.read_at)
        session = await c.record_plate(body.bay_id, read)
        return SessionOut.of(session) if session else None

    # dashboard -----------------------------------------------------------
    @app.get(
        "/api/v1/summary", response_model=SummaryOut, dependencies=[Depends(require(Role.VIEWER))]
    )
    async def summary(c: Container = Depends(get_container)) -> SummaryOut:
        midnight = datetime.combine(c.clock.now().date(), time.min, tzinfo=c.clock.now().tzinfo)
        today = await c.sessions.list_recent(since=midnight, limit=500)
        verified = [s for s in today if s.accuracy is not None]
        cams = [cam for b in await c.bays.list_all() for cam in await c.cameras.list_for_bay(b.id)]
        return SummaryOut(
            sessions_today=len(today),
            crates_today=sum(s.ai_count for s in today),
            open_sessions=sum(1 for s in today if s.status is SessionStatus.OPEN),
            verified_sessions=len(verified),
            mean_accuracy=(
                sum(s.accuracy or 0 for s in verified) / len(verified) if verified else None
            ),
            cameras_online=sum(1 for cam in cams if cam.status is CameraStatus.ONLINE),
            cameras_total=len(cams),
        )

    @app.get(
        "/api/v1/config",
        response_model=PlatformConfigOut,
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def platform_config(c: Container = Depends(get_container)) -> PlatformConfigOut:
        return PlatformConfigOut(max_upload_mb=c.settings.max_upload_mb)

    @app.get(
        "/api/v1/analytics/overview",
        response_model=OverviewOut,
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def overview(days: int = 14, c: Container = Depends(get_container)) -> OverviewOut:
        return OverviewOut.of(await c.overview(days))

    # video analysis --------------------------------------------------------
    async def _job_out(c: Container, job) -> AnalysisJobOut:
        async def url(key: str | None) -> str | None:
            # signed, not bearer-protected: these load in <img>/<video> tags
            return c.signer.sign(key) if key else None

        return AnalysisJobOut(
            id=job.id,
            bay_id=job.bay_id,
            filename=job.filename,
            status=job.status.value,
            progress=job.progress,
            error=job.error,
            created_by=job.created_by,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            duration_s=job.duration_s,
            total_crates=job.total_crates,
            loads=[vars(ld) for ld in job.loads],
            timeline=[
                {
                    "at_s": e.at_s,
                    "kind": e.kind,
                    "detail": e.detail,
                    "frame_url": await url(e.frame_key),
                }
                for e in job.timeline
            ],
            summary=job.summary,
            video_url=await url(job.object_key),
        )

    @app.post(
        "/api/v1/analysis",
        response_model=AnalysisJobOut,
        status_code=202,
        dependencies=[Depends(require(Role.OPERATOR))],
    )
    async def submit_video(
        bay_id: UUID,
        file: UploadFile = File(...),
        c: Container = Depends(get_container),
        principal: Principal = Depends(current_principal),
    ) -> AnalysisJobOut:
        limit = c.settings.max_upload_mb * 1024 * 1024

        async def chunks():
            seen = 0
            while chunk := await file.read(4 * 1024 * 1024):
                seen += len(chunk)
                if seen > limit:
                    raise HTTPException(413, f"upload exceeds {c.settings.max_upload_mb} MB")
                yield chunk

        try:
            job = await c.submit_video(
                bay_id,
                file.filename or "upload.mp4",
                file.content_type or "",
                chunks(),
                principal.name,
            )
        except ValueError as exc:
            raise HTTPException(415, str(exc)) from exc
        return await _job_out(c, job)

    @app.get(
        "/api/v1/analysis",
        response_model=list[AnalysisJobOut],
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def list_analyses(c: Container = Depends(get_container)) -> list[AnalysisJobOut]:
        return [await _job_out(c, j) for j in await c.jobs.list_recent()]

    @app.get(
        "/api/v1/analysis/{job_id}",
        response_model=AnalysisJobOut,
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def get_analysis(job_id: UUID, c: Container = Depends(get_container)) -> AnalysisJobOut:
        job = await c.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "analysis not found")
        return await _job_out(c, job)

    @app.get("/api/v1/objects/{key:path}")
    async def get_object(
        key: str,
        request: Request,
        exp: str | None = None,
        sig: str | None = None,
        c: Container = Depends(get_container),
    ) -> Response:
        """Serves uploaded videos and captured frames. Accepts a signed link (what the
        report embeds, since <img> cannot send a bearer token) or a viewer's token."""
        if ".." in key or key.startswith("/"):
            raise HTTPException(400, "bad key")
        if not c.signer.verify(key, exp, sig):
            principal = await current_principal(request)  # raises 401 without a token
            if not principal.allows(Role.VIEWER):
                raise HTTPException(403, "requires role 'viewer'")
        if isinstance(c.objects, LocalObjectStore):
            path = c.objects.path_of(key)
            if not path.is_file():
                raise HTTPException(404)
            return FileResponse(path)
        try:
            body, content_type = await c.objects.open(key)
        except Exception as exc:  # NoSuchKey and friends
            raise HTTPException(404) from exc
        return Response(body, media_type=content_type)

    # assistant -----------------------------------------------------------
    @app.get("/api/v1/assistant/status", dependencies=[Depends(require(Role.VIEWER))])
    async def assistant_status(c: Container = Depends(get_container)) -> dict:
        enabled = c.chat_model is not None
        return {"enabled": enabled, "model": c.settings.llm_model if enabled else None}

    @app.post(
        "/api/v1/assistant/chat",
        response_model=ChatOut,
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def assistant_chat(body: ChatIn, c: Container = Depends(get_container)) -> ChatOut:
        ask = c.ask_assistant
        if ask is None:
            raise HTTPException(503, "assistant is not configured (set IVAAS_LLM_URL)")
        try:
            reply = await ask([ChatMessage(m.role, m.content) for m in body.messages])
        except ChatModelUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        return ChatOut(
            reply=reply.text,
            tools_used=[ToolUseOut(name=t.name, arguments=t.arguments) for t in reply.tools_used],
        )

    # auth ----------------------------------------------------------------
    @app.get("/api/v1/auth/config", response_model=AuthConfigOut)
    async def auth_config(c: Container = Depends(get_container)) -> AuthConfigOut:
        oidc = c.settings.auth_mode == "oidc"
        return AuthConfigOut(
            mode=c.settings.auth_mode,
            oidc_issuer=c.settings.oidc_issuer if oidc else None,
            oidc_client_id=c.settings.oidc_audience if oidc else None,
        )

    @app.post("/api/v1/auth/login", response_model=TokenOut)
    async def login(body: LoginIn, c: Container = Depends(get_container)) -> TokenOut:
        """Local mode only. With OIDC the browser logs in at the provider instead."""
        if c.local_auth is None:
            raise HTTPException(404, "password login is disabled; use the identity provider")
        import hmac

        user = c.settings.local_users.get(body.username)
        if user is None or not hmac.compare_digest(user[0], body.password):
            raise HTTPException(401, "invalid username or password")
        token = c.local_auth.mint(body.username, body.username, [Role(user[1])])
        return TokenOut(access_token=token)

    @app.get("/api/v1/auth/me", response_model=MeOut)
    async def me(principal: Principal = Depends(current_principal)) -> MeOut:
        return MeOut(subject=principal.subject, name=principal.name, roles=sorted(principal.roles))

    @app.websocket("/ws/events")
    async def events(ws: WebSocket) -> None:
        principal = await websocket_principal(ws)
        if principal is None or not principal.allows(Role.VIEWER):
            await ws.close(code=4401)
            return
        hub = ws.app.state.container.hub
        await hub.connect(ws)
        try:
            while True:
                await ws.receive_text()  # keepalive pings from the client
        except WebSocketDisconnect:
            hub.disconnect(ws)

    return app


app = create_app()
