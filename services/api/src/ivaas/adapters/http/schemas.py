from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ivaas.application.overview import Overview, Severity, Trend
from ivaas.domain.models import (
    ApprovalReason,
    Bay,
    Camera,
    CameraRole,
    CameraStatus,
    LoadingSession,
    SessionDirection,
    SessionStatus,
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

    @classmethod
    def of(cls, s: LoadingSession) -> SessionOut:
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


class PlateReadIn(BaseModel):
    bay_id: UUID
    camera_id: UUID
    plate: str = Field(min_length=2, max_length=16)
    confidence: float = Field(ge=0, le=1)
    read_at: datetime


class SummaryOut(BaseModel):
    sessions_today: int
    crates_today: int
    open_sessions: int
    verified_sessions: int
    mean_accuracy: float | None
    cameras_online: int
    cameras_total: int


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


class ChatOut(BaseModel):
    reply: str
    tools_used: list[ToolUseOut]


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(BaseModel):
    subject: str
    name: str
    roles: list[str]


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
