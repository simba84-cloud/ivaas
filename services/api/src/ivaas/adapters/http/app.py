from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from uuid import UUID, uuid4

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

from ivaas.adapters.http.auth import (
    current_principal,
    password_epoch,
    require,
    websocket_principal,
)
from ivaas.adapters.http.schemas import (
    AnalysisJobOut,
    ApproveIn,
    AssignRolesIn,
    AuditEntryOut,
    AuthConfigOut,
    BayIn,
    BayOut,
    CameraIn,
    CameraOut,
    ChangePasswordIn,
    ChatIn,
    ChatOut,
    ConfigFactOut,
    CreateUserIn,
    CrossingIn,
    DiscoveredDeviceOut,
    DiscoveredStreamOut,
    DiscoverStreamsIn,
    EditableSettingOut,
    LoginIn,
    MeOut,
    OpenSessionIn,
    OverviewOut,
    PlateReadIn,
    PlatformConfigOut,
    ReconcileIn,
    SessionOut,
    SetEnabledIn,
    SettingIn,
    SettingsOut,
    SiteIn,
    SiteOut,
    SummaryOut,
    TemporaryPasswordOut,
    TokenOut,
    ToolUseOut,
    UserOut,
)
from ivaas.adapters.storage.objects import LocalObjectStore
from ivaas.adapters.streaming.mediamtx import StreamGatewayError
from ivaas.adapters.streaming.onvif import is_lan_device_url
from ivaas.application.analysis import job_worker
from ivaas.config.container import Container, build_container
from ivaas.config.settings import Settings
from ivaas.domain.audit import AuditAction, AuditEntry
from ivaas.domain.models import (
    Bay,
    CameraStatus,
    CrateCrossing,
    DomainError,
    InvalidStreamSourceError,
    NotFoundError,
    PlateRead,
    SessionStatus,
    Site,
)
from ivaas.domain.platform_settings import (
    AUTO_CLOSE_IDLE_MINUTES,
    AUTO_OPEN_DIRECTION,
    EDITABLE,
    RECONCILE_TOLERANCE,
    InvalidSettingError,
    validate,
)
from ivaas.domain.users import UserError, WeakPasswordError
from ivaas.ports.assistant import ChatMessage, ChatModelUnavailableError
from ivaas.ports.auth import Principal, Role

log = logging.getLogger(__name__)

CROSSINGS = Counter("ivaas_crate_crossings_total", "Crate crossings ingested", ["direction"])
PLATES = Counter("ivaas_plate_reads_total", "LPR plate reads ingested")


async def _sweep_idle_sessions(container: Container, every_s: float = 60.0) -> None:
    while True:
        await asyncio.sleep(every_s)
        use_case = await container.close_idle_sessions_uc()
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
        ]
        if settings.run_analysis_worker:
            tasks.append(asyncio.create_task(job_worker(lambda: app.state.container.run_next_job)))
        else:
            log.info("analysis worker disabled here; a separate worker process runs the jobs")
        app.state.background_tasks = tasks  # named so tests can assert what runs
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

    async def audit(
        c: Container,
        actor: str,
        action: AuditAction,
        subject: str,
        **detail: object,
    ) -> None:
        """Record who did what. The actor is an HTTP concern, so this lives here
        rather than threading authentication down into the use cases.

        The action has already succeeded by the time this runs. A failure to write
        the trail must therefore never surface as a failed request: telling an
        operator their approval failed when it did not is the worse outcome. It is
        logged loudly instead, and this is the boundary that guarantees it for every
        audit backend rather than trusting each one to remember.
        """
        try:
            await c.audit.record(
                AuditEntry(
                    at=c.clock.now(),
                    actor=actor,
                    action=action,
                    subject=subject,
                    detail={k: v for k, v in detail.items() if v is not None},
                )
            )
        except Exception:
            log.exception("could not record audit entry: %s by %s", action, actor)

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidStreamSourceError)
    async def _bad_source(_: Request, exc: InvalidStreamSourceError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(WeakPasswordError)
    async def _weak_password(_: Request, exc: WeakPasswordError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(UserError)
    async def _user_error(_: Request, exc: UserError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

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
        "/api/v1/sites", response_model=list[SiteOut], dependencies=[Depends(require(Role.VIEWER))]
    )
    async def list_sites(c: Container = Depends(get_container)) -> list[SiteOut]:
        return [SiteOut.of(s) for s in await c.sites.list_all()]

    @app.post(
        "/api/v1/sites",
        response_model=SiteOut,
        status_code=201,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def create_site(
        body: SiteIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> SiteOut:
        site = Site(id=uuid4(), name=body.name, timezone=body.timezone)
        await c.sites.save(site)
        await audit(c, principal.name, AuditAction.SITE_CREATED, site.name, site_id=str(site.id))
        return SiteOut.of(site)

    @app.get(
        "/api/v1/sites/{site_id}/bays",
        response_model=list[BayOut],
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def list_site_bays(site_id: UUID, c: Container = Depends(get_container)) -> list[BayOut]:
        if await c.sites.get(site_id) is None:
            raise NotFoundError(f"site {site_id} not found")
        return [BayOut.of(b) for b in await c.bays.list_for_site(site_id)]

    @app.post(
        "/api/v1/sites/{site_id}/bays",
        response_model=BayOut,
        status_code=201,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def create_bay(
        site_id: UUID,
        body: BayIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> BayOut:
        if await c.sites.get(site_id) is None:
            raise NotFoundError(f"site {site_id} not found")
        bay = Bay(
            id=uuid4(),
            site_id=site_id,
            name=body.name,
            height_m=body.height_m,
            width_m=body.width_m,
        )
        await c.bays.save(bay)
        await audit(c, principal.name, AuditAction.BAY_CREATED, bay.name, bay_id=str(bay.id))
        return BayOut.of(bay)

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
        bay_id: UUID,
        body: CameraIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> CameraOut:
        camera = await c.register_camera(bay_id, body.name, body.role, body.source_url)
        await audit(
            c,
            principal.name,
            AuditAction.CAMERA_REGISTERED,
            camera.name,
            position=camera.role.value,
            protocol=camera.source.protocol,
            camera_id=str(camera.id),
        )
        return CameraOut.of(camera)

    @app.delete(
        "/api/v1/cameras/{camera_id}", status_code=204, dependencies=[Depends(require(Role.ADMIN))]
    )
    async def remove_camera(
        camera_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> Response:
        existing = await c.cameras.get(camera_id)
        await c.remove_camera(camera_id)
        await audit(
            c,
            principal.name,
            AuditAction.CAMERA_REMOVED,
            existing.name if existing else str(camera_id),
            camera_id=str(camera_id),
        )
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
        body: OpenSessionIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> SessionOut:
        session = await c.open_session(body.bay_id, body.direction)
        await audit(
            c,
            principal.name,
            AuditAction.SESSION_OPENED,
            session.plate or "no plate yet",
            direction=session.direction.value,
            session_id=str(session.id),
        )
        return SessionOut.of(session)

    @app.post(
        "/api/v1/sessions/{session_id}/close",
        response_model=SessionOut,
        dependencies=[Depends(require(Role.OPERATOR))],
    )
    async def close_session(
        session_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> SessionOut:
        session = await c.close_session(session_id)
        await audit(
            c,
            principal.name,
            AuditAction.SESSION_CLOSED,
            session.plate or "no plate",
            ai_count=session.ai_count,
            session_id=str(session.id),
        )
        return SessionOut.of(session)

    @app.post(
        "/api/v1/sessions/{session_id}/reconcile",
        response_model=SessionOut,
        dependencies=[Depends(require(Role.OPERATOR))],
    )
    async def reconcile(
        session_id: UUID,
        body: ReconcileIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> SessionOut:
        reconcile_uc = await c.reconcile_session_uc()
        session = await reconcile_uc(session_id, body.manual_count)
        await audit(
            c,
            principal.name,
            AuditAction.SESSION_RECONCILED,
            session.plate or "no plate",
            ai_count=session.ai_count,
            manual_count=session.manual_count,
            variance=session.variance,
            outcome=session.status.value,
            session_id=str(session.id),
        )
        return SessionOut.of(session)

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
        await audit(
            c,
            principal.name,
            AuditAction.SESSION_APPROVED,
            session.plate or "no plate",
            reason=body.reason.value,
            note=body.note,
            variance=session.variance,
            session_id=str(session.id),
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
        record = await c.record_plate_uc()
        session = await record(body.bay_id, read)
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

    def _yes_no(ok: bool) -> str:
        return "Configured" if ok else "Not configured"

    # accounts ------------------------------------------------------------
    def _require_local_accounts(c: Container) -> None:
        if c.settings.auth_mode != "local":
            raise HTTPException(
                409,
                "accounts are managed by your identity provider, not here",
            )

    @app.get(
        "/api/v1/users",
        response_model=list[UserOut],
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def list_users(c: Container = Depends(get_container)) -> list[UserOut]:
        return [UserOut.of(u) for u in await c.user_admin.list_users()]

    @app.post(
        "/api/v1/users",
        response_model=TemporaryPasswordOut,
        status_code=201,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def create_user(
        body: CreateUserIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> TemporaryPasswordOut:
        _require_local_accounts(c)
        user, temporary = await c.user_admin.create(
            body.username, body.display_name, set(body.roles)
        )
        await audit(
            c,
            principal.name,
            AuditAction.USER_CREATED,
            user.username,
            roles=",".join(sorted(r.value for r in user.roles)),
        )
        return TemporaryPasswordOut(user=UserOut.of(user), temporary_password=temporary)

    @app.put(
        "/api/v1/users/{username}/roles",
        response_model=UserOut,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def assign_roles(
        username: str,
        body: AssignRolesIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> UserOut:
        _require_local_accounts(c)
        user = await c.user_admin.assign_roles(username, set(body.roles), by=principal.subject)
        await audit(
            c,
            principal.name,
            AuditAction.USER_ROLES_CHANGED,
            username,
            roles=",".join(sorted(r.value for r in user.roles)),
        )
        return UserOut.of(user)

    @app.put(
        "/api/v1/users/{username}/enabled",
        response_model=UserOut,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def set_user_enabled(
        username: str,
        body: SetEnabledIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> UserOut:
        _require_local_accounts(c)
        user = await c.user_admin.set_enabled(username, body.enabled, by=principal.subject)
        await audit(
            c,
            principal.name,
            AuditAction.USER_ENABLED if body.enabled else AuditAction.USER_DISABLED,
            username,
        )
        return UserOut.of(user)

    @app.post(
        "/api/v1/users/{username}/reset-password",
        response_model=TemporaryPasswordOut,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def reset_password(
        username: str,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> TemporaryPasswordOut:
        """Mint a temporary password and return it once.

        There is no mail server on a loading bay, so the administrator reads the
        temporary password to the person. It is never stored in the clear and the
        platform cannot show it a second time; the owner must replace it before the
        account can do anything else.
        """
        _require_local_accounts(c)
        user, temporary = await c.user_admin.reset_password(username)
        await audit(c, principal.name, AuditAction.PASSWORD_RESET, username)
        return TemporaryPasswordOut(user=UserOut.of(user), temporary_password=temporary)

    @app.post(
        "/api/v1/auth/password",
        response_model=TokenOut,
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def change_own_password(
        body: ChangePasswordIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> TokenOut:
        """Change your own password and stay signed in, here only.

        Every other session this account holds is minted against the old password
        and stops working immediately, which is the point: if the password is being
        changed because someone else learned it, their session must end. A fresh
        token is returned so the person doing it is not thrown out of their own.
        """
        _require_local_accounts(c)
        user = await c.user_admin.change_own_password(
            principal.subject, body.current_password, body.new_password
        )
        await audit(c, principal.name, AuditAction.PASSWORD_CHANGED, user.username)
        if c.local_auth is None:  # unreachable in local mode, but keeps the type honest
            raise HTTPException(409, "password login is disabled")
        return TokenOut(
            access_token=c.local_auth.mint(
                user.username,
                user.display_name,
                [Role(r.value) for r in user.roles],
                password_epoch=password_epoch(user),
            )
        )

    @app.get(
        "/api/v1/settings",
        response_model=SettingsOut,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def read_settings(c: Container = Depends(get_container)) -> SettingsOut:
        st = c.settings
        defaults = {
            RECONCILE_TOLERANCE: st.reconcile_tolerance,
            AUTO_CLOSE_IDLE_MINUTES: st.auto_close_idle_minutes,
            AUTO_OPEN_DIRECTION: st.auto_open_direction,
        }
        stored = await c.setting_store.all()
        editable = [
            EditableSettingOut(
                key=spec.key,
                label=spec.label,
                help=spec.help,
                kind=spec.kind,
                choices=list(spec.choices),
                minimum=spec.minimum,
                maximum=spec.maximum,
                value=stored.get(spec.key, defaults[spec.key]),
                overridden=spec.key in stored,
            )
            for spec in EDITABLE
        ]

        oidc = st.auth_mode == "oidc"
        # Never the values: this endpoint says whether a secret is set, not what it is.
        security = [
            ConfigFactOut(
                label="Sign-in",
                value="Identity provider (OIDC)" if oidc else "Local accounts",
                detail=st.oidc_issuer
                if oidc
                else "Accounts are defined in configuration. Use OIDC for real user management.",
            ),
            ConfigFactOut(
                label="Roles",
                value="viewer · operator · admin",
                detail="Viewers read. Operators run the bay and verify counts. "
                "Admins configure cameras and sign off disputes.",
            ),
            ConfigFactOut(
                label="Camera credentials at rest",
                value=_yes_no(bool(st.secrets_keys)),
                detail="Encrypted with Fernet keys from IVAAS_SECRETS_KEYS."
                if st.secrets_keys
                else "IVAAS_SECRETS_KEYS is empty, so camera passwords cannot be stored.",
            ),
            ConfigFactOut(
                label="Report links",
                value="Signed and expiring",
                detail="Frames and videos load over short-lived signed links, "
                "because an image tag cannot carry a bearer token.",
            ),
            ConfigFactOut(
                label="Machine callers",
                value=f"{len(st.service_api_keys)} service key(s)",
                detail="The pipeline posts counts with X-IVaaS-Key. Keys are never shown here.",
            ),
            ConfigFactOut(
                label="Audit trail",
                value="Append-only",
                detail="Every action that changes a count or the setup is recorded "
                "with the user who took it.",
            ),
        ]

        platform = [
            ConfigFactOut(label="Storage", value=st.storage, detail="IVAAS_STORAGE"),
            ConfigFactOut(label="Events", value=st.events, detail="IVAAS_EVENTS"),
            ConfigFactOut(
                label="Object storage",
                value=st.objects,
                detail=st.s3_endpoint if st.objects == "s3" else st.objects_dir,
            ),
            ConfigFactOut(
                label="Upload limit",
                value=f"{st.max_upload_mb} MB",
                detail="IVAAS_MAX_UPLOAD_MB; keep nginx client_max_body_size in step",
            ),
            ConfigFactOut(
                label="Analysis worker",
                value="In this process" if st.run_analysis_worker else "Separate container",
                detail="IVAAS_RUN_ANALYSIS_WORKER",
            ),
            ConfigFactOut(
                label="Media server",
                value=st.mediamtx_api_url or "Not configured",
                detail="IVAAS_MEDIAMTX_API_URL",
            ),
            ConfigFactOut(
                label="Assistant model",
                value=st.llm_model if st.llm_url else "Disabled",
                detail=st.llm_url or "IVAAS_LLM_URL is empty",
            ),
            ConfigFactOut(
                label="Crate detector",
                value=st.stack_model.rsplit("/", 1)[-1],
                detail="IVAAS_STACK_MODEL",
            ),
        ]
        return SettingsOut(editable=editable, security=security, platform=platform)

    @app.put(
        "/api/v1/settings/{key}",
        response_model=SettingsOut,
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def write_setting(
        key: str,
        body: SettingIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> SettingsOut:
        try:
            value = validate(key, body.value)
        except InvalidSettingError as exc:
            raise HTTPException(422, str(exc)) from exc
        await c.setting_store.set(key, value, by=principal.name, at=c.clock.now())
        c.forget_overrides()  # the next request sees it, not the next cache window
        await audit(c, principal.name, AuditAction.SETTING_CHANGED, key, value=value)
        return await read_settings(c)

    @app.get(
        "/api/v1/audit",
        response_model=list[AuditEntryOut],
        dependencies=[Depends(require(Role.ADMIN))],
    )
    async def audit_log(
        days: int = 7,
        actor: str | None = None,
        action: AuditAction | None = None,
        limit: int = 200,
        c: Container = Depends(get_container),
    ) -> list[AuditEntryOut]:
        since = c.clock.now() - timedelta(days=max(1, min(days, 365)))
        entries = await c.audit.list_recent(since=since, actor=actor, action=action, limit=limit)
        return [AuditEntryOut.of(e) for e in entries]

    @app.get(
        "/api/v1/analytics/overview",
        response_model=OverviewOut,
        dependencies=[Depends(require(Role.VIEWER))],
    )
    async def overview(
        days: int = 14,
        bay_id: UUID | None = None,
        c: Container = Depends(get_container),
    ) -> OverviewOut:
        return OverviewOut.of(await c.overview(days, bay_id=bay_id))

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
        await audit(
            c,
            principal.name,
            AuditAction.VIDEO_UPLOADED,
            job.filename,
            job_id=str(job.id),
            bay_id=str(bay_id),
        )
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

        username = body.username.strip().lower()
        user = await c.users.get(username)
        # One message and one cost for every failure: whether the account exists, is
        # disabled, or simply had the wrong password must not be distinguishable.
        ok = user is not None and not user.disabled
        if not c.hasher.verify(
            body.password, user.password_hash if user else c.dummy_password_hash
        ):
            ok = False
        if not ok or user is None:
            raise HTTPException(401, "invalid username or password")

        if c.hasher.needs_rehash(user.password_hash):
            user.password_hash = c.hasher.hash(body.password)  # upgrade quietly on sign-in
        user.last_login_at = c.clock.now()
        await c.users.save(user)

        token = c.local_auth.mint(
            user.username,
            user.display_name,
            [Role(r.value) for r in user.roles],
            must_change_password=user.must_change_password,
            password_epoch=password_epoch(user),
        )
        await audit(
            c,
            user.username,
            AuditAction.SIGNED_IN,
            user.username,
            role=user.highest_role.value,
        )
        return TokenOut(access_token=token, must_change_password=user.must_change_password)

    @app.get("/api/v1/auth/me", response_model=MeOut)
    async def me(
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> MeOut:
        user = await c.users.get(principal.subject) if c.users else None
        return MeOut(
            subject=principal.subject,
            name=principal.name,
            roles=sorted(principal.roles),
            must_change_password=bool(user.must_change_password) if user else False,
        )

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
