export type CameraRole =
  | "overhead"
  | "side_high"
  | "side_mid"
  | "side_low"
  | "chokepoint"
  | "lpr";
export type CameraStatus = "online" | "degraded" | "offline";
export type Direction = "loading" | "offloading";
export type SessionStatus = "open" | "closed" | "reconciled" | "disputed" | "approved";

export interface Site {
  id: string;
  name: string;
  timezone: string;
}

export interface Bay {
  id: string;
  site_id: string;
  name: string;
  height_m: number;
  width_m: number;
}

export interface Camera {
  id: string;
  bay_id: string;
  name: string;
  role: CameraRole;
  stream_path: string;
  status: CameraStatus;
  last_seen_at: string | null;
  protocol: string;
  source_url: string | null;
}

export interface DiscoveredDevice {
  address: string;
  host: string;
  name: string | null;
  hardware: string | null;
}

export interface DiscoveredStream {
  profile: string;
  resolution: [number, number] | null;
  encoding: string | null;
  url: string;
}

export type ApprovalReason =
  | "damaged_removed"
  | "camera_blocked"
  | "sheet_error"
  | "ai_miscount"
  | "other";

export interface Session {
  id: string;
  bay_id: string;
  direction: Direction;
  status: SessionStatus;
  plate: string | null;
  ai_count: number;
  manual_count: number | null;
  variance: number | null;
  accuracy: number | null;
  opened_at: string;
  closed_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  approval_reason: ApprovalReason | null;
  approval_note: string | null;
}

export interface Summary {
  sessions_today: number;
  crates_today: number;
  open_sessions: number;
  verified_sessions: number;
  mean_accuracy: number | null;
  cameras_online: number;
  cameras_total: number;
}

export type Severity = "good" | "info" | "warn" | "critical";

export interface Trend {
  value: number | null;
  delta_pct: number | null;
  /** null = not measured that day, which is not the same as zero */
  series: (number | null)[];
}

export interface DayPoint {
  day: string;
  crates: number;
  sessions: number;
  accuracy: number | null;
}

export interface Insight {
  key: string;
  severity: Severity;
  title: string;
  detail: string;
  metric: string | null;
}

export interface Overview {
  generated_at: string;
  days: number;
  crates_today: number;
  sessions_today: number;
  open_sessions: number;
  verified_sessions: number;
  unverified_sessions: number;
  disputed_sessions: number;
  reconciled_sessions: number;
  approved_sessions: number;
  mean_accuracy: number | null;
  cameras_online: number;
  cameras_total: number;
  crates: Trend;
  throughput: Trend;
  accuracy: Trend;
  daily: DayPoint[];
  insights: Insight[];
}

export type AuditAction =
  | "signed_in"
  | "session_opened"
  | "session_closed"
  | "session_reconciled"
  | "session_approved"
  | "camera_registered"
  | "camera_removed"
  | "video_uploaded"
  | "site_created"
  | "bay_created"
  | "setting_changed";

export interface AuditEntry {
  id: string;
  at: string;
  actor: string;
  action: AuditAction;
  subject: string;
  detail: Record<string, string | number | boolean>;
}

export interface EditableSetting {
  key: string;
  label: string;
  help: string;
  kind: "percent" | "minutes" | "choice";
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  value: string | number;
  overridden: boolean;
}

export interface ConfigFact {
  label: string;
  value: string;
  detail: string | null;
}

export interface PlatformSettings {
  editable: EditableSetting[];
  security: ConfigFact[];
  platform: ConfigFact[];
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ToolUse {
  name: string;
  arguments: Record<string, unknown>;
}

export interface DetectedLoad {
  start_s: number;
  end_s: number;
  stacks: number;
  crates: number;
  low_confidence: number;
  plate: string | null;
}

export interface TimelineEvent {
  at_s: number;
  kind: "load_started" | "stack_counted" | "plate_read" | "load_ended";
  detail: string;
  frame_url: string | null;
}

export interface AnalysisJob {
  id: string;
  bay_id: string;
  filename: string;
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  error: string | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number | null;
  total_crates: number;
  loads: DetectedLoad[];
  timeline: TimelineEvent[];
  summary: string | null;
  video_url: string | null;
}
