"""Composition root: the only place that knows which concrete adapter backs
which port. Swapping Postgres for another store, or NATS for Kafka, is a
change here and nowhere else (dependency inversion).
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from ivaas.adapters.analysis_runner import PipelineVideoAnalyser
from ivaas.adapters.auth.jwt_verifiers import (
    ApiKeyVerifier,
    LocalTokenVerifier,
    OidcTokenVerifier,
)
from ivaas.adapters.auth.passwords import Argon2PasswordHasher
from ivaas.adapters.http.signed import ObjectLinkSigner
from ivaas.adapters.messaging.fanout import FanoutEventPublisher, WebSocketHub
from ivaas.adapters.persistence.jobs import PersistentJobStore
from ivaas.adapters.persistence.memory import (
    InMemoryAuditLog,
    InMemoryBayRepository,
    InMemoryCameraRepository,
    InMemoryEventPublisher,
    InMemorySessionRepository,
    InMemorySiteRepository,
    SystemClock,
)
from ivaas.adapters.storage.objects import LocalObjectStore, S3ObjectStore
from ivaas.adapters.streaming.mediamtx import MediaMtxGateway, NullStreamGateway
from ivaas.adapters.streaming.onvif import OnvifDiscovery
from ivaas.application.analysis import RunNextJob, SubmitVideo
from ivaas.application.analytics import AnalyticsTools
from ivaas.application.assistant import AskAssistant
from ivaas.application.cameras import RefreshCameraStatus, RegisterCamera, RemoveCamera
from ivaas.application.overview import OperationsOverview, Overview
from ivaas.application.sessions import (
    ApproveSession,
    CloseIdleSessions,
    CloseSession,
    OpenSession,
    ReconcileSession,
    RecordCrateCrossing,
    RecordPlateRead,
)
from ivaas.application.summarise import SummariseReport
from ivaas.application.users import UserAdmin
from ivaas.config.settings import Settings
from ivaas.domain.models import Bay, Camera, CameraRole, SessionDirection, Site
from ivaas.domain.platform_settings import (
    AUTO_CLOSE_IDLE_MINUTES,
    AUTO_OPEN_DIRECTION,
    RECONCILE_TOLERANCE,
)
from ivaas.domain.users import User, UserRole
from ivaas.ports.assistant import ChatModel
from ivaas.ports.auth import TokenVerifier
from ivaas.ports.repositories import Clock, EventPublisher
from ivaas.ports.streaming import CameraDiscovery, StreamGateway

# Camera array from section 4.1 of the POC scope: 16 volumetric + 1 LPR.
POC_CAMERA_LAYOUT: list[tuple[CameraRole, int]] = [
    (CameraRole.OVERHEAD, 4),
    (CameraRole.SIDE_HIGH, 4),
    (CameraRole.SIDE_MID, 4),
    (CameraRole.SIDE_LOW, 2),
    (CameraRole.CHOKEPOINT, 2),
    (CameraRole.LPR, 1),
]


def _stable_id(name: str) -> Any:
    return uuid5(NAMESPACE_DNS, f"ivaas.{name}")


def demo_topology() -> tuple[Site, Bay, list[Camera]]:
    site = Site(id=_stable_id("site.demo-bakery"), name="Bakery Industrial Site")
    bay = Bay(id=_stable_id("bay.poc"), site_id=site.id, name="Loading Bay")
    cameras = [
        Camera(
            id=_stable_id(f"cam.{role.value}.{n}"),
            bay_id=bay.id,
            name=f"{role.value.replace('_', ' ').title()} {n}",
            role=role,
            stream_path=f"bay-poc/{role.value}-{n}",
        )
        for role, qty in POC_CAMERA_LAYOUT
        for n in range(1, qty + 1)
    ]
    return site, bay, cameras


@dataclass
class Container:
    settings: Settings
    users: Any
    hasher: Any
    audit: Any
    setting_store: Any
    sites: Any
    bays: Any
    cameras: Any
    sessions: Any
    events: EventPublisher
    hub: WebSocketHub
    clock: Clock
    gateway: StreamGateway
    discovery: CameraDiscovery
    chat_model: ChatModel | None
    verifiers: dict[str, TokenVerifier]
    local_auth: LocalTokenVerifier | None
    jobs: Any
    objects: Any
    analyser: Any
    signer: ObjectLinkSigner
    _closers: list[Any]

    #: overrides cached for this long; a change is live everywhere within it
    OVERRIDE_TTL = timedelta(seconds=10)
    _overrides: dict[str, Any] = field(default_factory=dict)
    _overrides_at: datetime | None = None
    _dummy_hash: str = ""

    async def effective(self, key: str, default: Any) -> Any:
        """The value in force: a stored override if there is one, else the environment."""
        now = self.clock.now()
        if self._overrides_at is None or now - self._overrides_at > self.OVERRIDE_TTL:
            try:
                self._overrides = await self.setting_store.all()
            except Exception:
                logging.getLogger(__name__).exception("could not read setting overrides")
                self._overrides = {}
            self._overrides_at = now
        return self._overrides.get(key, default)

    def forget_overrides(self) -> None:
        """Drop the cache so a just-saved change is visible immediately."""
        self._overrides_at = None

    # use cases -----------------------------------------------------------
    @property
    def open_session(self) -> OpenSession:
        return OpenSession(self.bays, self.sessions, self.events, self.clock)

    @property
    def close_session(self) -> CloseSession:
        return CloseSession(self.sessions, self.events, self.clock)

    async def reconcile_session_uc(self) -> ReconcileSession:
        tolerance = await self.effective(RECONCILE_TOLERANCE, self.settings.reconcile_tolerance)
        return ReconcileSession(self.sessions, self.events, float(tolerance))

    @property
    def record_crossing(self) -> RecordCrateCrossing:
        return RecordCrateCrossing(self.sessions, self.events)

    async def record_plate_uc(self) -> RecordPlateRead:
        direction = await self.effective(AUTO_OPEN_DIRECTION, self.settings.auto_open_direction)
        return RecordPlateRead(
            self.sessions,
            self.events,
            auto_open=self.open_session if direction else None,
            auto_open_direction=SessionDirection(direction or "loading"),
        )

    async def close_idle_sessions_uc(self) -> CloseIdleSessions | None:
        minutes = float(
            await self.effective(AUTO_CLOSE_IDLE_MINUTES, self.settings.auto_close_idle_minutes)
        )
        if minutes <= 0:
            return None
        return CloseIdleSessions(
            self.sessions, self.events, self.clock, idle_after=timedelta(minutes=minutes)
        )

    @property
    def submit_video(self) -> SubmitVideo:
        return SubmitVideo(self.bays, self.jobs, self.objects, self.events, self.clock)

    @property
    def run_next_job(self) -> RunNextJob:
        return RunNextJob(
            self.jobs,
            self.objects,
            self.analyser,
            self.events,
            self.clock,
            summarise=SummariseReport(self.chat_model),
        )

    @property
    def register_camera(self) -> RegisterCamera:
        return RegisterCamera(self.bays, self.cameras, self.gateway, self.events)

    @property
    def refresh_camera_status(self) -> RefreshCameraStatus:
        return RefreshCameraStatus(self.bays, self.cameras, self.gateway, self.clock)

    @property
    def remove_camera(self) -> RemoveCamera:
        return RemoveCamera(self.cameras, self.gateway, self.events)

    async def overview(self, days: int = 14, bay_id: UUID | None = None) -> Overview:
        use_case = OperationsOverview(self.sessions, self.cameras, self.bays, self.clock)
        return await use_case(days, bay_id=bay_id)

    @property
    def dummy_password_hash(self) -> str:
        """A real hash of a random secret, verified against when no such user exists.

        Without it a wrong username returns immediately while a wrong password pays
        for Argon2, and the difference tells an attacker which usernames are real.
        """
        if not self._dummy_hash:
            self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))
        return self._dummy_hash

    @property
    def user_admin(self) -> UserAdmin:
        return UserAdmin(self.users, self.hasher, self.clock)

    @property
    def approve_session(self) -> ApproveSession:
        return ApproveSession(self.sessions, self.events, self.clock)

    @property
    def ask_assistant(self) -> AskAssistant | None:
        if self.chat_model is None:
            return None
        tools = AnalyticsTools(self.sessions, self.cameras, self.bays, self.clock)
        return AskAssistant(self.chat_model, tools, self.clock)

    async def aclose(self) -> None:
        for closer in self._closers:
            await closer()


async def build_container(settings: Settings) -> Container:
    closers: list[Any] = []
    hub = WebSocketHub()
    sinks: list[EventPublisher] = [hub]

    if settings.events == "nats":
        from ivaas.adapters.messaging.nats_publisher import NatsEventPublisher

        nats = NatsEventPublisher(settings.nats_url)
        await nats.connect()
        closers.append(nats.close)
        sinks.append(nats)
    else:
        sinks.append(InMemoryEventPublisher())

    site, bay, cams = demo_topology()
    if settings.storage == "postgres":
        from ivaas.adapters.persistence.postgres import build_postgres_repositories
        from ivaas.adapters.persistence.secrets import SecretBox

        if not settings.secrets_keys:
            raise RuntimeError(
                "IVAAS_SECRETS_KEYS is required with postgres storage: camera credentials "
                "are encrypted at rest (see Settings.secrets_keys for how to generate one)"
            )
        sites, bays, cameras, sessions, dispose, sm = await build_postgres_repositories(
            settings.database_url,
            seed=(site, bay, cams) if settings.seed_demo_data else None,
            box=SecretBox(settings.secrets_keys),
        )
        closers.append(dispose)
        pg_sessionmaker: Any = sm
    else:
        seed = settings.seed_demo_data
        sites = InMemorySiteRepository([site] if seed else [])
        bays = InMemoryBayRepository([bay] if seed else [])
        cameras = InMemoryCameraRepository(cams if seed else [])
        sessions = InMemorySessionRepository()
        pg_sessionmaker = None

    gateway: StreamGateway
    if settings.mediamtx_api_url:
        mtx = MediaMtxGateway(settings.mediamtx_api_url)
        closers.append(mtx.aclose)
        gateway = mtx
    else:
        gateway = NullStreamGateway()

    chat_model: ChatModel | None = None
    if settings.llm_url:
        from ivaas.adapters.llm.openai_compatible import OpenAiCompatibleChatModel

        llm = OpenAiCompatibleChatModel(settings.llm_url, settings.llm_model, settings.llm_api_key)
        closers.append(llm.aclose)
        chat_model = llm

    verifiers: dict[str, TokenVerifier] = {"api_key": ApiKeyVerifier(settings.service_api_keys)}
    local_auth: LocalTokenVerifier | None = None
    if settings.auth_mode == "local":
        local_auth = LocalTokenVerifier(settings.auth_local_secret)
        verifiers["user"] = local_auth
    else:
        oidc = OidcTokenVerifier(settings.oidc_issuer, settings.oidc_audience)
        closers.append(oidc.aclose)
        verifiers["user"] = oidc

    if settings.objects == "s3":
        objects: Any = S3ObjectStore(
            settings.s3_endpoint, settings.s3_access_key, settings.s3_secret_key, settings.s3_bucket
        )
        await objects.ensure_bucket()
    else:
        objects = LocalObjectStore(settings.objects_dir)

    hasher = Argon2PasswordHasher()
    users: Any
    audit: Any
    setting_store: Any
    if pg_sessionmaker is not None:
        from ivaas.adapters.persistence.audit_postgres import PostgresAuditLog
        from ivaas.adapters.persistence.settings_postgres import PostgresSettingsStore
        from ivaas.adapters.persistence.users_postgres import PostgresUserStore

        audit = PostgresAuditLog(pg_sessionmaker)
        setting_store = PostgresSettingsStore(pg_sessionmaker)
        users = PostgresUserStore(pg_sessionmaker)
    else:
        from ivaas.adapters.persistence.settings_postgres import InMemorySettingsStore
        from ivaas.adapters.persistence.users_postgres import InMemoryUserStore

        audit = InMemoryAuditLog()
        setting_store = InMemorySettingsStore()
        users = InMemoryUserStore()

    jobs: Any
    if pg_sessionmaker is not None:
        from ivaas.adapters.persistence.jobs_postgres import PostgresJobStore

        jobs = PostgresJobStore(pg_sessionmaker)
    else:
        jobs = PersistentJobStore(objects)
        restored = await jobs.load_all()
        if restored:
            logging.getLogger(__name__).info("restored %d analysis job(s)", restored)

    # Accounts live in the database. The configured ones are carried across on first
    # run so an existing deployment keeps working, flagged as still holding the
    # password they were seeded with so the portal can say so.
    if settings.auth_mode == "local" and not await users.list_all():
        for username, (password, role) in settings.local_users.items():
            await users.save(
                User(
                    username=username,
                    display_name=username,
                    password_hash=hasher.hash(password),
                    roles={UserRole(role)},
                    password_is_default=True,
                    created_at=SystemClock().now(),
                    password_changed_at=SystemClock().now(),
                )
            )

    return Container(
        settings=settings,
        users=users,
        hasher=hasher,
        audit=audit,
        setting_store=setting_store,
        sites=sites,
        bays=bays,
        cameras=cameras,
        sessions=sessions,
        events=FanoutEventPublisher(sinks),
        hub=hub,
        clock=SystemClock(),
        gateway=gateway,
        discovery=OnvifDiscovery(),
        chat_model=chat_model,
        verifiers=verifiers,
        local_auth=local_auth,
        jobs=jobs,
        signer=ObjectLinkSigner(settings.object_link_secret),
        objects=objects,
        analyser=PipelineVideoAnalyser(settings.stack_model, settings.layers_model),
        _closers=closers,
    )
