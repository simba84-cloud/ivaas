"""Composition root: the only place that knows which concrete adapter backs
which port. Swapping Postgres for another store, or NATS for Kafka, is a
change here and nowhere else (dependency inversion).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

from ivaas.adapters.analysis_runner import PipelineVideoAnalyser
from ivaas.adapters.auth.jwt_verifiers import (
    ApiKeyVerifier,
    LocalTokenVerifier,
    OidcTokenVerifier,
)
from ivaas.adapters.http.signed import ObjectLinkSigner
from ivaas.adapters.messaging.fanout import FanoutEventPublisher, WebSocketHub
from ivaas.adapters.persistence.jobs import PersistentJobStore
from ivaas.adapters.persistence.memory import (
    InMemoryBayRepository,
    InMemoryCameraRepository,
    InMemoryEventPublisher,
    InMemorySessionRepository,
    SystemClock,
)
from ivaas.adapters.storage.objects import LocalObjectStore, S3ObjectStore
from ivaas.adapters.streaming.mediamtx import MediaMtxGateway, NullStreamGateway
from ivaas.adapters.streaming.onvif import OnvifDiscovery
from ivaas.application.analysis import RunNextJob, SubmitVideo
from ivaas.application.analytics import AnalyticsTools
from ivaas.application.assistant import AskAssistant
from ivaas.application.cameras import RefreshCameraStatus, RegisterCamera, RemoveCamera
from ivaas.application.sessions import (
    CloseIdleSessions,
    CloseSession,
    OpenSession,
    ReconcileSession,
    RecordCrateCrossing,
    RecordPlateRead,
)
from ivaas.application.summarise import SummariseReport
from ivaas.config.settings import Settings
from ivaas.domain.models import Bay, Camera, CameraRole, SessionDirection, Site
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
    site = Site(id=_stable_id("site.demo-bakery"), name="Demo Bakery Industrial Site")
    bay = Bay(id=_stable_id("bay.poc"), site_id=site.id, name="POC Loading Bay")
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

    # use cases -----------------------------------------------------------
    @property
    def open_session(self) -> OpenSession:
        return OpenSession(self.bays, self.sessions, self.events, self.clock)

    @property
    def close_session(self) -> CloseSession:
        return CloseSession(self.sessions, self.events, self.clock)

    @property
    def reconcile_session(self) -> ReconcileSession:
        return ReconcileSession(self.sessions, self.events, self.settings.reconcile_tolerance)

    @property
    def record_crossing(self) -> RecordCrateCrossing:
        return RecordCrateCrossing(self.sessions, self.events)

    @property
    def record_plate(self) -> RecordPlateRead:
        direction = self.settings.auto_open_direction
        return RecordPlateRead(
            self.sessions,
            self.events,
            auto_open=self.open_session if direction else None,
            auto_open_direction=SessionDirection(direction or "loading"),
        )

    @property
    def close_idle_sessions(self) -> CloseIdleSessions | None:
        minutes = self.settings.auto_close_idle_minutes
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

    _, bay, cams = demo_topology()
    if settings.storage == "postgres":
        from ivaas.adapters.persistence.postgres import build_postgres_repositories
        from ivaas.adapters.persistence.secrets import SecretBox

        if not settings.secrets_keys:
            raise RuntimeError(
                "IVAAS_SECRETS_KEYS is required with postgres storage: camera credentials "
                "are encrypted at rest (see Settings.secrets_keys for how to generate one)"
            )
        bays, cameras, sessions, dispose, sm = await build_postgres_repositories(
            settings.database_url,
            seed=(bay, cams) if settings.seed_demo_data else None,
            box=SecretBox(settings.secrets_keys),
        )
        closers.append(dispose)
        pg_sessionmaker: Any = sm
    else:
        seed = settings.seed_demo_data
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

    jobs: Any
    if pg_sessionmaker is not None:
        from ivaas.adapters.persistence.jobs_postgres import PostgresJobStore

        jobs = PostgresJobStore(pg_sessionmaker)
    else:
        jobs = PersistentJobStore(objects)
        restored = await jobs.load_all()
        if restored:
            logging.getLogger(__name__).info("restored %d analysis job(s)", restored)

    return Container(
        settings=settings,
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
