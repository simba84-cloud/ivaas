"""Composition root: the only place that knows which concrete adapter backs
which port. Swapping Postgres for another store, or NATS for Kafka, is a
change here and nowhere else (dependency inversion).
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
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
from ivaas.adapters.persistence.partitioned import PerTenant
from ivaas.adapters.storage.objects import LocalObjectStore, S3ObjectStore
from ivaas.adapters.streaming.mediamtx import MediaMtxGateway, NullStreamGateway
from ivaas.adapters.streaming.onvif import OnvifDiscovery
from ivaas.application.alerts import AcknowledgeAlert, ListAcknowledgements
from ivaas.application.analysis import RunNextJob, SubmitVideo
from ivaas.application.analytics import AnalyticsTools
from ivaas.application.assistant import AskAssistant
from ivaas.application.cameras import RefreshCameraStatus, RegisterCamera, RemoveCamera
from ivaas.application.overview import OperationsOverview, Overview
from ivaas.application.provisioning import ProvisionTenant
from ivaas.application.security import (
    EnrolPerson,
    RecordBadge,
    ReportIncident,
    SaveZone,
    UpdateIncident,
)
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
from ivaas.application.tally import RematchTallySheets, SaveTallySheets
from ivaas.application.users import UserAdmin
from ivaas.config.settings import Settings
from ivaas.domain.models import Bay, Camera, CameraRole, SessionDirection, Site
from ivaas.domain.platform_settings import (
    AUTO_CLOSE_IDLE_MINUTES,
    AUTO_OPEN_DIRECTION,
    BADGE_GRACE_MINUTES,
    FACE_RECOGNITION,
    RECONCILE_TOLERANCE,
)
from ivaas.domain.rbac import LEGACY_ROLES, Role, RoleBinding
from ivaas.domain.tenancy import (
    BAKERS_INN_ID,
    ISOLATION_TEST_ID,
    LITZIM_ID,
    OPERATING,
    Partner,
    ScopeType,
    Tenant,
    TenantStatus,
)
from ivaas.domain.users import User
from ivaas.ports.assistant import ChatModel
from ivaas.ports.auth import TokenVerifier
from ivaas.ports.repositories import Clock, EventPublisher
from ivaas.ports.streaming import CameraDiscovery, StreamGateway
from ivaas.tenancy import current_tenant, system_context, tenant_context

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


#: The commercial hierarchy every environment starts with (proposal M1 seed).
LITZIM = Partner(LITZIM_ID, "litzim", "LITZIM")
BAKERS_INN = Tenant(BAKERS_INN_ID, "bakers-inn", "Bakers Inn", LITZIM_ID, TenantStatus.TRIAL)
#: Synthetic: exists so isolation can be demonstrated against something real.
ISOLATION_TEST = Tenant(
    ISOLATION_TEST_ID, "isolation-test", "Isolation Test Foods", LITZIM_ID, TenantStatus.TRIAL
)


def isolation_topology() -> tuple[Site, Bay]:
    site = Site(id=_stable_id("site.isolation-test"), name="Test Depot", timezone="Africa/Harare")
    return site, Bay(id=_stable_id("bay.isolation-test"), site_id=site.id, name="Test Bay")


def _demo_accounts() -> list[tuple[str, UUID | None, list[RoleBinding]]]:
    """Platform, partner and second-tenant accounts, so every scope can be signed in as.

    Seeded with their username as password and flagged as still holding it, exactly
    like the configured tenant accounts, so the portal nags until they change.
    """
    t = ScopeType
    return [
        ("platform", None, [RoleBinding(Role.PLATFORM_ADMIN, t.PLATFORM)]),
        ("litzim", None, [RoleBinding(Role.PARTNER_ADMIN, t.PARTNER, LITZIM_ID)]),
        (
            "b-admin",
            ISOLATION_TEST_ID,
            [RoleBinding(Role.TENANT_ADMIN, t.TENANT, ISOLATION_TEST_ID)],
        ),
        (
            "b-operator",
            ISOLATION_TEST_ID,
            [RoleBinding(Role.BAY_OPERATOR, t.TENANT, ISOLATION_TEST_ID)],
        ),
    ]


def _configured_roles(role: str) -> list[RoleBinding]:
    """IVAAS_LOCAL_USERS names a pre-tenancy role (admin/operator/viewer) or a new one."""
    roles = LEGACY_ROLES.get(role) or (Role(role),)
    return [RoleBinding(r, ScopeType.TENANT, BAKERS_INN_ID) for r in roles]


def demo_topology() -> tuple[Site, Bay, list[Camera]]:
    # local time zone: tally sheets are written in wall-clock time and matched against it
    site = Site(
        id=_stable_id("site.demo-bakery"), name="Bakery Industrial Site", timezone="Africa/Harare"
    )
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
    tenants: Any
    edge: Any
    ingest: Any
    ml_models: Any
    evidence: Any
    users: Any
    hasher: Any
    audit: Any
    setting_store: Any
    acknowledgements: Any
    tally: Any
    zones: Any
    incidents: Any
    badges: Any
    people: Any
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
    #: per tenant: each tenant's settings are its own
    _overrides: dict[Any, tuple[datetime, dict[str, Any]]] = field(default_factory=dict)
    _dummy_hash: str = ""
    #: per tenant: what its edge node last said it can detect (set when it fetches zones)
    edge_security: dict[Any, Any] = field(default_factory=dict)
    _face_encoder: Any = None
    _face_encoder_missing: bool = False

    async def effective(self, key: str, default: Any) -> Any:
        """The value in force for the tenant in context: its override, else the environment."""
        now = self.clock.now()
        tenant = current_tenant()
        cached = self._overrides.get(tenant)
        if cached is None or now - cached[0] > self.OVERRIDE_TTL:
            try:
                values = await self.setting_store.all()
            except Exception:
                logging.getLogger(__name__).exception("could not read setting overrides")
                values = {}
            cached = self._overrides[tenant] = (now, values)
        return cached[1].get(key, default)

    def forget_overrides(self) -> None:
        """Drop the cache so a just-saved change is visible immediately."""
        self._overrides.pop(current_tenant(), None)

    async def operating_tenants(self) -> list[Tenant]:
        """Tenants whose sites are counting: what the background sweeps visit, one by one."""
        with system_context():
            return [t for t in await self.tenants.list_all() if t.status in OPERATING]

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
    def provision_tenant(self) -> ProvisionTenant:
        return ProvisionTenant(self.tenants, self.users, self.hasher, self.clock)

    # security --------------------------------------------------------------
    async def face_recognition_on(self) -> bool:
        return await self.effective(FACE_RECOGNITION, "off") == "on"

    @property
    def face_encoder(self) -> Any:
        """OpenCV's face models, if installed. None means enrolment cannot run here."""
        if self._face_encoder is None and not self._face_encoder_missing:
            paths = (self.settings.face_detector_model, self.settings.face_recognizer_model)
            if all(Path(p).is_file() for p in paths):
                from ivaas_pipeline.adapters.opencv_faces import OpenCvFaces

                self._face_encoder = OpenCvFaces(*paths)
            else:
                self._face_encoder_missing = True
        return self._face_encoder

    @property
    def save_zone(self) -> SaveZone:
        return SaveZone(self.zones, self.cameras)

    async def report_incident_uc(self) -> ReportIncident:
        grace = float(await self.effective(BADGE_GRACE_MINUTES, 10))
        return ReportIncident(
            self.zones,
            self.incidents,
            self.badges,
            self.objects,
            self.events,
            face_recognition_on=await self.face_recognition_on(),
            badge_grace=timedelta(minutes=grace),
        )

    @property
    def update_incident(self) -> UpdateIncident:
        return UpdateIncident(self.incidents, self.events, self.clock)

    @property
    def record_badge(self) -> RecordBadge:
        return RecordBadge(self.badges)

    async def enrol_person_uc(self) -> EnrolPerson:
        return EnrolPerson(
            self.people, self.face_encoder, self.clock, await self.face_recognition_on()
        )

    @property
    def acknowledge_alert(self) -> AcknowledgeAlert:
        return AcknowledgeAlert(self.acknowledgements, self.events, self.clock)

    @property
    def list_acknowledgements(self) -> ListAcknowledgements:
        return ListAcknowledgements(self.acknowledgements, self.clock)

    async def save_tally_sheets_uc(self) -> SaveTallySheets:
        return SaveTallySheets(
            self.tally,
            self.sessions,
            self.bays,
            self.sites,
            await self.reconcile_session_uc(),
            self.clock,
        )

    async def rematch_tally_sheets_uc(self) -> RematchTallySheets:
        return RematchTallySheets(
            self.tally,
            self.sessions,
            self.bays,
            self.sites,
            await self.reconcile_session_uc(),
            self.clock,
        )

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
    seed = settings.seed_demo_data
    tenants: Any
    if settings.storage == "postgres":
        from ivaas.adapters.persistence.postgres import build_postgres_repositories
        from ivaas.adapters.persistence.secrets import SecretBox

        if not settings.secrets_keys:
            raise RuntimeError(
                "IVAAS_SECRETS_KEYS is required with postgres storage: camera credentials "
                "are encrypted at rest (see Settings.secrets_keys for how to generate one)"
            )
        box = SecretBox(settings.secrets_keys)
        sites, bays, cameras, sessions, dispose, sm = await build_postgres_repositories(
            settings.database_url,
            seed=(site, bay, cams) if settings.seed_demo_data else None,
            box=box,
        )
        closers.append(dispose)
        pg_sessionmaker: Any = sm
    else:
        # one store per tenant: the in-memory equivalent of row-level security
        sites = PerTenant(
            InMemorySiteRepository, {BAKERS_INN_ID: InMemorySiteRepository([site] if seed else [])}
        )
        bays = PerTenant(
            InMemoryBayRepository, {BAKERS_INN_ID: InMemoryBayRepository([bay] if seed else [])}
        )
        cameras = PerTenant(
            InMemoryCameraRepository,
            {BAKERS_INN_ID: InMemoryCameraRepository(cams if seed else [])},
        )
        sessions = PerTenant(InMemorySessionRepository)
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
    acknowledgements: Any
    if pg_sessionmaker is not None:
        from ivaas.adapters.persistence.alerts_postgres import PostgresAcknowledgementStore
        from ivaas.adapters.persistence.audit_postgres import PostgresAuditLog
        from ivaas.adapters.persistence.settings_postgres import PostgresSettingsStore
        from ivaas.adapters.persistence.tenants_postgres import PostgresTenantStore
        from ivaas.adapters.persistence.users_postgres import PostgresUserStore

        tenants = PostgresTenantStore(pg_sessionmaker)
        from ivaas.adapters.persistence.edge_postgres import PostgresEdgeStore

        edge: Any = PostgresEdgeStore(pg_sessionmaker)
        from ivaas.adapters.persistence.ingest_postgres import PostgresIngestLedger

        ingest: Any = PostgresIngestLedger(pg_sessionmaker)
        from ivaas.adapters.persistence.ml_models_postgres import PostgresModelRegistry

        ml_models: Any = PostgresModelRegistry(pg_sessionmaker)
        from ivaas.adapters.persistence.evidence_postgres import PostgresEvidenceStore

        evidence: Any = PostgresEvidenceStore(pg_sessionmaker)
        audit = PostgresAuditLog(pg_sessionmaker)
        setting_store = PostgresSettingsStore(pg_sessionmaker)
        acknowledgements = PostgresAcknowledgementStore(pg_sessionmaker)
        from ivaas.adapters.persistence.tally_postgres import PostgresTallySheetStore

        tally: Any = PostgresTallySheetStore(pg_sessionmaker)
        users = PostgresUserStore(pg_sessionmaker)
        from ivaas.adapters.persistence.security_postgres import (
            PostgresBadgeLog,
            PostgresIncidentStore,
            PostgresPeopleStore,
            PostgresZoneStore,
        )

        zones: Any = PostgresZoneStore(pg_sessionmaker)
        incidents: Any = PostgresIncidentStore(pg_sessionmaker)
        badges: Any = PostgresBadgeLog(pg_sessionmaker)
        people: Any = PostgresPeopleStore(pg_sessionmaker, box)
    else:
        from ivaas.adapters.persistence.alerts_postgres import InMemoryAcknowledgementStore
        from ivaas.adapters.persistence.settings_postgres import InMemorySettingsStore
        from ivaas.adapters.persistence.tenants_postgres import InMemoryTenantStore
        from ivaas.adapters.persistence.users_postgres import InMemoryUserStore

        tenants = InMemoryTenantStore()
        from ivaas.adapters.persistence.edge_postgres import InMemoryEdgeStore

        edge = InMemoryEdgeStore()
        from ivaas.adapters.persistence.ingest_postgres import InMemoryIngestLedger

        ingest = PerTenant(InMemoryIngestLedger)
        from ivaas.adapters.persistence.ml_models_postgres import InMemoryModelRegistry

        ml_models = PerTenant(InMemoryModelRegistry)
        from ivaas.adapters.persistence.evidence_postgres import InMemoryEvidenceStore

        evidence = PerTenant(InMemoryEvidenceStore)
        audit = PerTenant(InMemoryAuditLog)
        setting_store = PerTenant(InMemorySettingsStore)
        acknowledgements = PerTenant(InMemoryAcknowledgementStore)
        from ivaas.adapters.persistence.tally_postgres import InMemoryTallySheetStore

        tally = PerTenant(InMemoryTallySheetStore)
        users = InMemoryUserStore()
        from ivaas.adapters.persistence.security_postgres import (
            InMemoryBadgeLog,
            InMemoryIncidentStore,
            InMemoryPeopleStore,
            InMemoryZoneStore,
        )

        zones = PerTenant(InMemoryZoneStore)
        incidents = PerTenant(InMemoryIncidentStore)
        badges = PerTenant(InMemoryBadgeLog)
        people = PerTenant(InMemoryPeopleStore)

    jobs: Any
    if pg_sessionmaker is not None:
        from ivaas.adapters.persistence.jobs_postgres import PostgresJobStore

        jobs = PostgresJobStore(pg_sessionmaker)
    else:
        jobs = PersistentJobStore(objects)
        restored = await jobs.load_all()
        if restored:
            logging.getLogger(__name__).info("restored %d analysis job(s)", restored)

    # The hierarchy every environment starts with. Partners and tenants are platform
    # records, written in system context; each tenant's own rows in its own.
    with system_context():
        if await tenants.get_partner(LITZIM_ID) is None:
            await tenants.save_partner(LITZIM)
        for tenant in (BAKERS_INN, ISOLATION_TEST) if seed else (BAKERS_INN,):
            if await tenants.get(tenant.id) is None:
                tenant.created_at = SystemClock().now()
                await tenants.save(tenant)
    if seed:
        other_site, other_bay = isolation_topology()
        with tenant_context(ISOLATION_TEST_ID):
            if await sites.get(other_site.id) is None:
                await sites.save(other_site)
                await bays.save(other_bay)

    # Accounts live in the database. The configured ones are carried across on first
    # run so an existing deployment keeps working, flagged as still holding the
    # password they were seeded with so the portal can say so. Only missing accounts
    # are created: one that exists keeps its password and roles.
    if settings.auth_mode == "local":
        accounts = [
            (name, password, BAKERS_INN_ID, _configured_roles(role))
            for name, (password, role) in settings.local_users.items()
        ]
        if seed:
            accounts += [(name, name, t, b) for name, t, b in _demo_accounts()]
        with system_context():  # usernames are global; the lookup must see every tenant
            for username, password, tenant_id, bindings in accounts:
                if await users.get(username) is not None:
                    continue
                await users.save(
                    User(
                        username=username,
                        display_name=username,
                        password_hash=hasher.hash(password),
                        tenant_id=tenant_id,
                        bindings=bindings,
                        password_is_default=True,
                        created_at=SystemClock().now(),
                        password_changed_at=SystemClock().now(),
                    )
                )

    from ivaas.adapters.auth.node_verifier import NodeCredentialVerifier

    verifiers["node"] = NodeCredentialVerifier(edge)

    return Container(
        settings=settings,
        tenants=tenants,
        edge=edge,
        ingest=ingest,
        ml_models=ml_models,
        evidence=evidence,
        users=users,
        hasher=hasher,
        audit=audit,
        setting_store=setting_store,
        acknowledgements=acknowledgements,
        tally=tally,
        zones=zones,
        incidents=incidents,
        badges=badges,
        people=people,
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
