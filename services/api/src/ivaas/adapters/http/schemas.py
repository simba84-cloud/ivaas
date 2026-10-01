from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from ivaas.application.overview import Overview, Severity, Trend
from ivaas.domain.alerts import AlertAcknowledgement
from ivaas.domain.audit import AuditAction, AuditEntry
from ivaas.domain.fleet import Identification, Vehicle, identification
from ivaas.domain.models import (
    ApprovalReason,
    Bay,
    Camera,
    CameraRole,
    CameraStatus,
    LoadingSession,
    OverrideReason,
    SessionDirection,
    SessionStatus,
    Site,
)
from ivaas.domain.rbac import TENANT_ROLES, Role, RoleBinding
from ivaas.domain.security import (
    BadgeEvent,
    EnrolledPerson,
    Incident,
    IncidentKind,
    IncidentStatus,
    Zone,
    ZoneRule,
)
from ivaas.domain.tally import TallySheet, TallyStatus
from ivaas.domain.tenancy import (
    Partner,
    ProvisioningRecord,
    ProvisioningStep,
    ScopeType,
    Tenant,
    TenantStatus,
)
from ivaas.domain.users import User


class BindingOut(BaseModel):
    role: Role
    scope_type: ScopeType
    scope_id: UUID | None

    @staticmethod
    def of(b: RoleBinding) -> BindingOut:
        return BindingOut(role=b.role, scope_type=b.scope_type, scope_id=b.scope_id)


class BindingIn(BaseModel):
    role: Role
    scope_type: ScopeType = ScopeType.TENANT
    #: the site or bay; ignored at tenant scope, where it is always the caller's tenant
    scope_id: UUID | None = None


class BindingsIn(BaseModel):
    bindings: list[BindingIn] = Field(min_length=1)


def _tenant_role(role: Role) -> Role:
    if role not in TENANT_ROLES:
        raise ValueError(f"a tenant cannot grant {role.value}")
    return role


class TenantRefOut(BaseModel):
    id: UUID
    slug: str
    name: str
    status: TenantStatus

    @staticmethod
    def of(t: Tenant) -> TenantRefOut:
        return TenantRefOut(id=t.id, slug=t.slug, name=t.name, status=t.status)


class TenantOut(TenantRefOut):
    partner_id: UUID | None
    created_at: datetime | None

    @staticmethod
    def of(t: Tenant) -> TenantOut:  # type: ignore[override]
        return TenantOut(
            id=t.id,
            slug=t.slug,
            name=t.name,
            status=t.status,
            partner_id=t.partner_id,
            created_at=t.created_at,
        )


class PartnerOut(BaseModel):
    id: UUID
    slug: str
    name: str

    @staticmethod
    def of(p: Partner) -> PartnerOut:
        return PartnerOut(id=p.id, slug=p.slug, name=p.name)


class ProvisionIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    #: required for partner admins (their own partner); optional for platform admins
    partner_id: UUID | None = None
    owner_username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    owner_display_name: str = Field(default="", max_length=120)


class ProvisioningStepOut(BaseModel):
    name: str
    done: bool
    detail: str

    @staticmethod
    def of(s: ProvisioningStep) -> ProvisioningStepOut:
        return ProvisioningStepOut(name=s.name, done=s.done, detail=s.detail)


class ProvisionOut(BaseModel):
    tenant: TenantOut
    owner_username: str
    #: shown once, on the call that created the tenant; null on a repeat
    temporary_password: str | None
    created: bool
    steps: list[ProvisioningStepOut]

    @staticmethod
    def of(
        record: ProvisioningRecord, tenant: Tenant, temporary: str | None, created: bool
    ) -> ProvisionOut:
        return ProvisionOut(
            tenant=TenantOut.of(tenant),
            owner_username=record.owner_username,
            temporary_password=temporary,
            created=created,
            steps=[ProvisioningStepOut.of(s) for s in record.steps],
        )


class BayOut(BaseModel):
    id: UUID
    site_id: UUID
    name: str
    height_m: float
    width_m: float

    @classmethod
    def of(cls, bay: Bay) -> BayOut:
        return cls(**bay.__dict__)


class SiteOut(BaseModel):
    id: UUID
    name: str
    timezone: str

    @classmethod
    def of(cls, site: Site) -> SiteOut:
        return cls(id=site.id, name=site.name, timezone=site.timezone)


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="UTC", max_length=64)


class BayIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    height_m: float = Field(default=4.5, gt=0, le=50)
    width_m: float = Field(default=4.0, gt=0, le=50)


class CameraOut(BaseModel):
    id: UUID
    bay_id: UUID
    name: str
    role: CameraRole
    stream_path: str
    status: CameraStatus
    last_seen_at: datetime | None
    protocol: str
    source_url: str | None  # always redacted: credentials never reach a browser

    @classmethod
    def of(cls, camera: Camera) -> CameraOut:
        return cls(
            id=camera.id,
            bay_id=camera.bay_id,
            name=camera.name,
            role=camera.role,
            stream_path=camera.stream_path,
            status=camera.status,
            last_seen_at=camera.last_seen_at,
            protocol=camera.source.protocol,
            source_url=camera.source.redacted,
        )


class CameraIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: CameraRole
    source_url: str | None = Field(
        default=None,
        max_length=1024,
        description="rtsp(s)/rtmp(s)/srt/http(s)/udp/whep URL, or null if the camera pushes to us",
    )


class DiscoveredDeviceOut(BaseModel):
    address: str
    host: str
    name: str | None
    hardware: str | None


class DiscoverStreamsIn(BaseModel):
    address: str = Field(max_length=512)
    username: str = Field(max_length=128)
    password: str = Field(max_length=128)


class DiscoveredStreamOut(BaseModel):
    profile: str
    resolution: tuple[int, int] | None
    encoding: str | None
    url: str


class SessionOut(BaseModel):
    id: UUID
    bay_id: UUID
    direction: SessionDirection
    status: SessionStatus
    plate: str | None
    ai_count: int
    manual_count: int | None
    variance: int | None
    accuracy: float | None
    opened_at: datetime
    closed_at: datetime | None
    approved_by: str | None
    approved_at: datetime | None
    approval_reason: ApprovalReason | None
    approval_note: str | None
    vehicle_id: UUID | None = None
    #: the plate exactly as the camera read it, beside the register's spelling
    plate_read: str | None = None
    #: registered, unregistered, unidentified, or unchecked (no register to check)
    identification: Identification = Identification.UNCHECKED
    identified_by: str | None = None
    #: a person's corrected count; the AI count above is never changed
    override_count: int | None = None
    override_reason: OverrideReason | None = None
    override_note: str | None = None
    override_by: str | None = None
    override_at: datetime | None = None
    #: what the load is taken to have carried: the correction if any, else the AI count
    count_of_record: int = 0

    @classmethod
    def of(cls, s: LoadingSession, *, has_register: bool = False) -> SessionOut:
        return cls(
            id=s.id,
            bay_id=s.bay_id,
            direction=s.direction,
            status=s.status,
            plate=s.plate,
            ai_count=s.ai_count,
            manual_count=s.manual_count,
            variance=s.variance,
            accuracy=s.accuracy,
            opened_at=s.opened_at,
            closed_at=s.closed_at,
            approved_by=s.approved_by,
            approved_at=s.approved_at,
            approval_reason=s.approval_reason,
            approval_note=s.approval_note,
            vehicle_id=s.vehicle_id,
            plate_read=s.plate_read,
            identification=identification(s.plate, s.vehicle_id, has_register),
            identified_by=s.identified_by,
            override_count=s.override_count,
            override_reason=s.override_reason,
            override_note=s.override_note,
            override_by=s.override_by,
            override_at=s.override_at,
            count_of_record=s.count_of_record,
        )


class OpenSessionIn(BaseModel):
    bay_id: UUID
    direction: SessionDirection


class ReconcileIn(BaseModel):
    manual_count: int = Field(ge=0)


class ApproveIn(BaseModel):
    """Signing off a disputed load. The counts are not changed, only accepted."""

    reason: ApprovalReason
    note: str | None = Field(default=None, max_length=280)


class CrossingIn(BaseModel):
    """Posted by the AI pipeline for each crate crossing the chokepoint."""

    bay_id: UUID
    camera_id: UUID
    track_id: int
    direction: SessionDirection
    crates: int = Field(default=1, ge=1, le=40, description="crates in the object that crossed")
    confidence: float = Field(ge=0, le=1)
    crossed_at: datetime
    #: set by the edge node when it spools the event; a replay with the same id is
    #: acknowledged and skipped, so a crash mid-delivery cannot count crates twice
    event_id: UUID | None = None


class PlateReadIn(BaseModel):
    bay_id: UUID
    camera_id: UUID
    plate: str = Field(min_length=2, max_length=16)
    confidence: float = Field(ge=0, le=1)
    read_at: datetime
    event_id: UUID | None = None


class SummaryOut(BaseModel):
    sessions_today: int
    crates_today: int
    open_sessions: int
    verified_sessions: int
    mean_accuracy: float | None
    cameras_online: int
    cameras_total: int


class EditableSettingOut(BaseModel):
    key: str
    label: str
    help: str
    kind: str
    choices: list[str]
    minimum: float | None
    maximum: float | None
    value: object
    #: true when a stored override is in force rather than the deployed default
    overridden: bool


class ConfigFactOut(BaseModel):
    """One deployment fact, stated without its secret."""

    label: str
    value: str
    detail: str | None = None


class SettingsOut(BaseModel):
    editable: list[EditableSettingOut]
    security: list[ConfigFactOut]
    platform: list[ConfigFactOut]


class SettingIn(BaseModel):
    value: object


class AuditEntryOut(BaseModel):
    id: UUID
    at: datetime
    actor: str
    action: AuditAction
    subject: str
    detail: dict

    @staticmethod
    def of(e: AuditEntry) -> AuditEntryOut:
        return AuditEntryOut(
            id=e.id,
            at=e.at,
            actor=e.actor,
            action=e.action,
            subject=e.subject,
            detail=e.detail,
        )


class TrendOut(BaseModel):
    value: float | None
    delta_pct: float | None
    series: list[float | None]

    @staticmethod
    def of(t: Trend) -> TrendOut:
        return TrendOut(value=t.value, delta_pct=t.delta_pct, series=t.series)


class DayPointOut(BaseModel):
    day: date
    crates: int
    sessions: int
    accuracy: float | None


class InsightOut(BaseModel):
    key: str
    severity: Severity
    title: str
    detail: str
    metric: str | None


class OverviewOut(BaseModel):
    generated_at: datetime
    days: int
    crates_today: int
    sessions_today: int
    open_sessions: int
    verified_sessions: int
    unverified_sessions: int
    disputed_sessions: int
    reconciled_sessions: int
    approved_sessions: int
    mean_accuracy: float | None
    cameras_online: int
    cameras_total: int
    crates: TrendOut
    throughput: TrendOut
    accuracy: TrendOut
    daily: list[DayPointOut]
    insights: list[InsightOut]

    @staticmethod
    def of(o: Overview) -> OverviewOut:
        return OverviewOut(
            generated_at=o.generated_at,
            days=o.days,
            crates_today=o.crates_today,
            sessions_today=o.sessions_today,
            open_sessions=o.open_sessions,
            verified_sessions=o.verified_sessions,
            unverified_sessions=o.unverified_sessions,
            disputed_sessions=o.disputed_sessions,
            reconciled_sessions=o.reconciled_sessions,
            approved_sessions=o.approved_sessions,
            mean_accuracy=o.mean_accuracy,
            cameras_online=o.cameras_online,
            cameras_total=o.cameras_total,
            crates=TrendOut.of(o.crates),
            throughput=TrendOut.of(o.throughput),
            accuracy=TrendOut.of(o.accuracy),
            daily=[
                DayPointOut(day=d.day, crates=d.crates, sessions=d.sessions, accuracy=d.accuracy)
                for d in o.daily
            ],
            insights=[
                InsightOut(
                    key=i.key,
                    severity=i.severity,
                    title=i.title,
                    detail=i.detail,
                    metric=i.metric,
                )
                for i in o.insights
            ],
        )


class PlatformConfigOut(BaseModel):
    """Limits the portal needs so it can warn before a long upload, not after."""

    max_upload_mb: int


class ChatTurnIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    messages: list[ChatTurnIn] = Field(min_length=1, max_length=40)


class ToolUseOut(BaseModel):
    name: str
    arguments: dict


class HealthOut(BaseModel):
    status: Literal["ok"]


class AssistantStatusOut(BaseModel):
    enabled: bool
    model: str | None = None


class ChatOut(BaseModel):
    reply: str
    tools_used: list[ToolUseOut]


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    #: the portal must send the user to set a new password before anything else
    must_change_password: bool = False


class UserOut(BaseModel):
    """An account as an administrator sees it. Never carries a hash."""

    username: str
    display_name: str
    #: roles held across the whole tenant; narrower ones are in `bindings`
    roles: list[Role]
    bindings: list[BindingOut]
    disabled: bool
    must_change_password: bool
    password_is_default: bool
    created_at: datetime | None
    password_changed_at: datetime | None
    last_login_at: datetime | None

    @staticmethod
    def of(u: User) -> UserOut:
        return UserOut(
            username=u.username,
            display_name=u.display_name,
            roles=sorted(b.role for b in u.bindings if b.scope_type is ScopeType.TENANT),
            bindings=[BindingOut.of(b) for b in u.bindings],
            disabled=u.disabled,
            must_change_password=u.must_change_password,
            password_is_default=u.password_is_default,
            created_at=u.created_at,
            password_changed_at=u.password_changed_at,
            last_login_at=u.last_login_at,
        )


class CreateUserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(default="", max_length=120)
    roles: list[Role] = Field(min_length=1)

    @field_validator("roles")
    @classmethod
    def _only_tenant_roles(cls, roles: list[Role]) -> list[Role]:
        return [_tenant_role(r) for r in roles]


class AssignRolesIn(BaseModel):
    roles: list[Role] = Field(min_length=1)

    @field_validator("roles")
    @classmethod
    def _only_tenant_roles(cls, roles: list[Role]) -> list[Role]:
        return [_tenant_role(r) for r in roles]


class SetEnabledIn(BaseModel):
    enabled: bool


class TemporaryPasswordOut(BaseModel):
    """Shown to the administrator once. The platform cannot show it again."""

    user: UserOut
    temporary_password: str


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class MeOut(BaseModel):
    subject: str
    name: str
    roles: list[str]
    #: what the portal may offer; the API checks every call regardless
    permissions: list[str] = []
    bindings: list[BindingOut] = []
    #: null for platform and partner staff
    tenant: TenantRefOut | None = None
    #: the portal must show the password screen and nothing else
    must_change_password: bool = False


class AuthConfigOut(BaseModel):
    mode: Literal["local", "oidc"]
    oidc_issuer: str | None
    oidc_client_id: str | None


class LoadOut(BaseModel):
    start_s: float
    end_s: float
    stacks: int
    crates: int
    low_confidence: int
    plate: str | None


class TimelineEventOut(BaseModel):
    at_s: float
    kind: str
    detail: str
    frame_url: str | None


class AnalysisJobOut(BaseModel):
    id: UUID
    bay_id: UUID
    filename: str
    status: str
    progress: float
    error: str | None
    created_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    total_crates: int
    loads: list[LoadOut]
    timeline: list[TimelineEventOut]
    summary: str | None
    video_url: str | None


class AcknowledgeIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    #: what the alert said, so the audit trail reads in operator terms
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)


class AcknowledgementOut(BaseModel):
    key: str
    acknowledged_by: str
    acknowledged_at: datetime
    note: str | None

    @staticmethod
    def of(a: AlertAcknowledgement) -> AcknowledgementOut:
        return AcknowledgementOut(
            key=a.key,
            acknowledged_by=a.acknowledged_by,
            acknowledged_at=a.acknowledged_at,
            note=a.note,
        )


# --- site security -------------------------------------------------------------------


class WindowIn(BaseModel):
    days: list[int] = Field(min_length=1, max_length=7)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class ZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=64)
    rules: list[ZoneRule] = []
    schedule: list[WindowIn] = Field(default=[], max_length=14)
    min_dwell_s: float = Field(default=3.0, ge=0, le=600)
    exclude: bool = False
    badge_door: str | None = Field(default=None, max_length=120)


class ZoneOut(BaseModel):
    id: UUID
    camera_id: UUID
    name: str
    polygon: list[tuple[float, float]]
    rules: list[ZoneRule]
    schedule: list[WindowIn]
    min_dwell_s: float
    exclude: bool
    badge_door: str | None
    #: armed right now, in the site's own time zone
    armed: bool

    @staticmethod
    def of(z: Zone, armed: bool) -> ZoneOut:
        return ZoneOut(
            id=z.id,
            camera_id=z.camera_id,
            name=z.name,
            polygon=list(z.polygon),
            rules=sorted(z.rules),
            schedule=[WindowIn(days=list(w.days), start=w.start, end=w.end) for w in z.schedule],
            min_dwell_s=z.min_dwell_s,
            exclude=z.exclude,
            badge_door=z.badge_door,
            armed=armed,
        )


class IncidentIn(BaseModel):
    bay_id: UUID
    camera_id: UUID
    zone_id: UUID
    kind: IncidentKind
    confidence: float = Field(ge=0, le=1)
    detected_at: datetime
    #: base64 JPEG; 2 MB decoded
    snapshot_jpeg_b64: str | None = Field(default=None, max_length=2_800_000)
    detail: dict = {}
    event_id: UUID | None = None


class IncidentOut(BaseModel):
    id: UUID
    bay_id: UUID
    camera_id: UUID
    kind: IncidentKind
    zone_id: UUID | None
    zone_name: str | None
    detected_at: datetime
    confidence: float
    snapshot_url: str | None
    detail: dict
    status: IncidentStatus
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    resolved_by: str | None
    resolved_at: datetime | None
    resolution_note: str | None

    @staticmethod
    def of(i: Incident, snapshot_url: str | None) -> IncidentOut:
        return IncidentOut(
            id=i.id,
            bay_id=i.bay_id,
            camera_id=i.camera_id,
            kind=i.kind,
            zone_id=i.zone_id,
            zone_name=i.zone_name,
            detected_at=i.detected_at,
            confidence=i.confidence,
            snapshot_url=snapshot_url,
            detail=i.detail,
            status=i.status,
            acknowledged_by=i.acknowledged_by,
            acknowledged_at=i.acknowledged_at,
            resolved_by=i.resolved_by,
            resolved_at=i.resolved_at,
            resolution_note=i.resolution_note,
        )


class ResolveIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


class BadgeIn(BaseModel):
    badge_id: str = Field(min_length=1, max_length=120)
    door: str = Field(min_length=1, max_length=120)
    at: datetime
    granted: bool
    holder: str | None = Field(default=None, max_length=160)


class BadgeOut(BadgeIn):
    id: UUID

    @staticmethod
    def of(e: BadgeEvent) -> BadgeOut:
        return BadgeOut(
            id=e.id, badge_id=e.badge_id, door=e.door, at=e.at, granted=e.granted, holder=e.holder
        )


class PersonOut(BaseModel):
    """Who is enrolled. The face embedding is never returned, to anyone."""

    id: UUID
    name: str
    employee_ref: str
    consent_reference: str
    enrolled_by: str
    enrolled_at: datetime

    @staticmethod
    def of(p: EnrolledPerson) -> PersonOut:
        return PersonOut(
            id=p.id,
            name=p.name,
            employee_ref=p.employee_ref,
            consent_reference=p.consent_reference,
            enrolled_by=p.enrolled_by,
            enrolled_at=p.enrolled_at,
        )


class EdgeCapabilities(BaseModel):
    reported_at: datetime
    detectors: list[str]


class SecurityStatusOut(BaseModel):
    """What the security features can actually do right now, so the portal can say so."""

    face_recognition: bool
    face_models_installed: bool
    enrolled_people: int
    badge_events_24h: int
    last_badge_at: datetime | None
    #: what the edge node last said it can detect; None if it has never checked in
    edge: EdgeCapabilities | None


class PipelineZone(BaseModel):
    id: UUID
    camera_id: UUID
    name: str
    polygon: list[tuple[float, float]]
    rules: list[ZoneRule]
    armed: bool
    min_dwell_s: float
    exclude: bool


class GalleryEntry(BaseModel):
    name: str
    embedding: list[float]


class PipelineSecurityOut(BaseModel):
    zones: list[PipelineZone]
    #: only while face recognition is switched on
    gallery: list[GalleryEntry]


# tally sheets --------------------------------------------------------------------


class TallyLineIn(BaseModel):
    line_no: int = Field(ge=1, le=999)
    #: negative for a stack carried back off the truck
    crates: int = Field(ge=-500, le=500)
    note: str | None = Field(default=None, max_length=40)


class TallySheetIn(BaseModel):
    """One paper sheet typed in by hand, the form twin of the CSV import."""

    sheet_id: str = Field(min_length=3, max_length=80)
    bay_id: UUID
    date: date
    plate: str = Field(min_length=2, max_length=32)
    direction: Literal["LOAD", "RETURN"]
    start_time: time | None = None
    end_time: time | None = None
    lines: list[TallyLineIn] = Field(default_factory=list, max_length=999)
    total_on_paper: int | None = Field(default=None, ge=0)
    pages: int | None = Field(default=None, ge=1)
    counted_by: str | None = Field(default=None, max_length=120)
    verified_by: str | None = Field(default=None, max_length=120)
    entered_by: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class TallySheetOut(BaseModel):
    """Blind by design: what was entered and where it stands, never the AI count."""

    id: UUID
    sheet_id: str
    bay_id: UUID
    date: date
    plate: str
    direction: Literal["LOAD", "RETURN"]
    start_time: time | None
    end_time: time | None
    lines: int
    line_total: int | None
    total_on_paper: int | None
    truth: int | None
    transcription_mismatch: bool
    counted_by: str | None
    verified_by: str | None
    status: TallyStatus
    entered_by_user: str
    entered_at: datetime

    @classmethod
    def of(cls, s: TallySheet) -> TallySheetOut:
        return cls(
            id=s.id,
            sheet_id=s.sheet_id,
            bay_id=s.bay_id,
            date=s.date,
            plate=s.plate,
            direction="LOAD" if s.direction is SessionDirection.LOADING else "RETURN",
            start_time=s.start_time,
            end_time=s.end_time,
            lines=len(s.lines),
            line_total=s.line_total,
            total_on_paper=s.total_on_paper,
            truth=s.truth,
            transcription_mismatch=s.transcription_mismatch,
            counted_by=s.counted_by,
            verified_by=s.verified_by,
            status=s.status,
            entered_by_user=s.entered_by_user,
            entered_at=s.entered_at,
        )


class TallyImportOut(BaseModel):
    saved: list[TallySheetOut]
    #: example rows and the like, left out on purpose
    skipped: list[str]


class TallyRematchOut(BaseModel):
    changed: list[TallySheetOut]


class TallyReportRow(BaseModel):
    sheet: TallySheetOut
    session_id: UUID | None
    session_status: SessionStatus | None
    ai_count: int | None
    variance: int | None
    #: None when the sheet has not reconciled a session: never shown as 0%
    accuracy: float | None
    passed: bool | None


class TallyReportOut(BaseModel):
    target: float
    sheets: int
    reconciled: int
    passing: int
    mean_accuracy: float | None
    #: |sum(AI) - sum(sheets)| / sum(sheets) over reconciled sheets
    aggregate_error: float | None
    rows: list[TallyReportRow]


# --- edge nodes ------------------------------------------------------------------------


class EnrollmentTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    bay_id: UUID | None = None
    ttl_hours: int = Field(default=24, ge=1, le=72)


class EnrollmentTokenOut(BaseModel):
    """Shown once. The platform keeps only a digest and cannot show it again."""

    token: str
    name: str
    site_id: UUID
    bay_id: UUID | None
    expires_at: datetime


class EnrollIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    hostname: str = Field(default="", max_length=120)
    version: str = Field(default="", max_length=40)


class EnrolledOut(BaseModel):
    """The node's credential, shown once, to the node itself."""

    node_id: UUID
    credential: str
    name: str
    site_id: UUID
    bay_id: UUID | None


class CameraReportIn(BaseModel):
    api_camera_id: str = Field(max_length=64)
    connected: bool
    fps: float | None = Field(default=None, ge=0, le=1000)
    lag_s: float | None = Field(default=None, ge=0)


class HeartbeatIn(BaseModel):
    version: str = Field(default="", max_length=40)
    config_version: str | None = Field(default=None, max_length=16)
    uptime_s: float = Field(default=0, ge=0)
    spool_pending: int = Field(default=0, ge=0)
    cameras: list[CameraReportIn] = Field(default=[], max_length=64)
    #: what runs, by role: {"detector": {"name", "version", "sha256"}, "layers": {...}}
    models: dict = Field(default={})
    #: set when the node refused a new model and kept the one it had
    model_error: str | None = Field(default=None, max_length=500)


class HeartbeatOut(BaseModel):
    #: what the node should be running; a different value means fetch the config again
    config_version: str


class EdgeCameraIn(BaseModel):
    api_camera_id: UUID
    key: str | None = Field(default=None, max_length=64)
    line: list[tuple[float, float]] | None = Field(default=None, min_length=2, max_length=2)
    zone: list[float] | None = Field(default=None, min_length=4, max_length=4)
    stride: int = Field(default=1, ge=1, le=60)
    frames: Literal["latest", "all"] | None = None


class EdgeModelIn(BaseModel):
    """A model the node already has on disk (`path`), or a registered version it
    downloads and verifies (`version_id`). Exactly one."""

    path: str | None = Field(default=None, min_length=1, max_length=300)
    version_id: UUID | None = None
    arch: Literal["rtdetr", "yolo"] = "rtdetr"

    @model_validator(mode="after")
    def _one_source(self) -> EdgeModelIn:
        if (self.path is None) == (self.version_id is None):
            raise ValueError("give the model either a path or a version_id")
        return self


class EdgeConfigIn(BaseModel):
    """What the node runs: the same shape as pipeline.json, minus anything the API knows."""

    model: EdgeModelIn
    layers_model: str | None = Field(default=None, max_length=300)
    #: a registered layer-counting model, instead of `layers_model`'s path
    layers_model_id: UUID | None = None
    forward_means: Literal["loading", "offloading"] = "loading"
    count: Literal["stack", "crate"] = "stack"
    cameras: list[EdgeCameraIn] = Field(default=[], max_length=32)
    lpr_cameras: list[EdgeCameraIn] = Field(default=[], max_length=8)


class NodeCameraOut(BaseModel):
    api_camera_id: str
    name: str | None
    connected: bool
    fps: float | None
    lag_s: float | None


class EdgeNodeOut(BaseModel):
    id: UUID
    name: str
    hostname: str
    site_id: UUID
    bay_id: UUID | None
    status: str
    #: never_seen, online, stale, offline or revoked; never assumed healthy
    health: str
    enrolled_at: datetime
    last_seen_at: datetime | None
    version: str | None
    uptime_s: float | None
    spool_pending: int | None
    cameras: list[NodeCameraOut]
    config: dict
    config_version: str
    applied_config_version: str | None
    #: null until the node has reported which config it runs
    config_drift: bool | None
    #: what the node says it runs, by role: {"detector": {name, version, sha256}, ...}
    models: dict = {}
    #: the last model the node refused to switch to, and why; it kept the one it had
    model_error: str | None = None
    can_roll_back: bool = False


class ModelVersionOut(BaseModel):
    id: UUID
    name: str
    version: str
    sha256: str
    size_bytes: int
    meta: dict
    notes: str
    created_by: str
    created_at: datetime

    @staticmethod
    def of(m) -> ModelVersionOut:
        return ModelVersionOut(
            id=m.id,
            name=m.name,
            version=m.version,
            sha256=m.sha256,
            size_bytes=m.size_bytes,
            meta=m.meta,
            notes=m.notes,
            created_by=m.created_by,
            created_at=m.created_at,
        )


class VehicleIn(BaseModel):
    plate: str = Field(min_length=2, max_length=16)
    fleet_number: str = Field(default="", max_length=40)
    operator: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=1000)
    active: bool = True


class VehicleOut(VehicleIn):
    id: UUID
    created_at: datetime | None

    @staticmethod
    def of(v: Vehicle) -> VehicleOut:  # type: ignore[override]
        return VehicleOut(
            id=v.id,
            plate=v.plate,
            fleet_number=v.fleet_number,
            operator=v.operator,
            notes=v.notes,
            active=v.active,
            created_at=v.created_at,
        )


class FleetImportOut(BaseModel):
    added: int
    updated: int
    #: "line 7: ..." for every row that could not be used; the rest were saved
    errors: list[str]


class AssignVehicleIn(BaseModel):
    """Say which truck a load was: a registered one, or a plate that is not registered."""

    vehicle_id: UUID | None = None
    plate: str | None = Field(default=None, min_length=2, max_length=16)
    note: str | None = Field(default=None, max_length=280)


class OverrideIn(BaseModel):
    count: int = Field(ge=0, le=100_000)
    reason: OverrideReason
    note: str | None = Field(default=None, max_length=280)
