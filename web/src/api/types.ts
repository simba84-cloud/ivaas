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
  vehicle_id?: string | null;
  /** the plate exactly as the camera read it */
  plate_read?: string | null;
  identification?: Identification;
  identified_by?: "lpr" | "operator" | null;
  /** a person's correction; ai_count is never changed */
  override_count?: number | null;
  override_reason?: OverrideReason | null;
  override_note?: string | null;
  override_by?: string | null;
  override_at?: string | null;
  count_of_record?: number;
}

/** How sure the platform is which truck a load was. */
export type Identification = "registered" | "unregistered" | "unidentified" | "unchecked";

export type OverrideReason =
  | "person_or_forklift"
  | "double_counted"
  | "missed_by_camera"
  | "camera_blocked"
  | "damaged_removed"
  | "other";

export interface Vehicle {
  id: string;
  plate: string;
  fleet_number: string;
  operator: string;
  notes: string;
  active: boolean;
  created_at: string | null;
}

export interface FleetImport {
  added: number;
  updated: number;
  errors: string[];
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
  | "setting_changed"
  | "user_created"
  | "user_roles_changed"
  | "user_enabled"
  | "user_disabled"
  | "password_reset"
  | "password_changed"
  | "alert_acknowledged"
  | "zone_saved"
  | "zone_deleted"
  | "incident_acknowledged"
  | "incident_resolved"
  | "person_enrolled"
  | "person_removed"
  | "tally_sheet_saved"
  | "tally_conflict"
  | "tenant_provisioned"
  | "role_bound"
  | "edge_token_created"
  | "node_enrolled"
  | "node_revoked"
  | "node_config_changed"
  | "node_rolled_back"
  | "model_uploaded"
  | "vehicle_saved"
  | "fleet_imported"
  | "session_identified"
  | "count_overridden"
  | "manifest_imported"
  | "exception_resolved"
  | "webhook_created"
  | "webhook_deleted"
  | "webhook_replayed"
  | "subscription_changed"
  | "invoice_issued"
  | "payment_recorded"
  | "tenant_held"
  | "tenant_released"
  | "break_glass_requested"
  | "break_glass_approved"
  | "break_glass_denied"
  | "break_glass_ended"
  | "break_glass_used";

export interface AuditEntry {
  id: string;
  at: string;
  actor: string;
  action: AuditAction;
  subject: string;
  detail: Record<string, string | number | boolean | string[]>;
}

/** The roles a tenant grants its own people (proposal §4.1). */
export type UserRole =
  | "tenant_owner"
  | "tenant_admin"
  | "site_manager"
  | "bay_operator"
  | "auditor"
  | "integration";

export interface RoleBinding {
  role: string;
  scope_type: "platform" | "partner" | "tenant" | "site" | "bay";
  scope_id: string | null;
}

export interface User {
  username: string;
  display_name: string;
  /** Roles held across the whole tenant; narrower ones are in `bindings`. */
  roles: UserRole[];
  bindings?: RoleBinding[];
  disabled: boolean;
  must_change_password: boolean;
  password_is_default: boolean;
  created_at: string | null;
  password_changed_at: string | null;
  last_login_at: string | null;
}

export interface TemporaryPassword {
  user: User;
  temporary_password: string;
}

export interface EditableSetting {
  key: string;
  label: string;
  help: string;
  kind: "percent" | "minutes" | "days" | "crates" | "choice" | "text";
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

export interface Acknowledgement {
  key: string;
  acknowledged_by: string;
  acknowledged_at: string;
  note: string | null;
}

export type ZoneRule = "intrusion" | "ppe" | "face" | "badge" | "fire";
export type IncidentKind = "intrusion" | "no_ppe" | "unknown_face" | "unbadged" | "fire" | "smoke";
export type IncidentStatus = "open" | "acknowledged" | "resolved";

export interface ScheduleWindow {
  days: number[]; // 0 = Monday
  start: string; // HH:MM
  end: string;
}

export interface ZoneInput {
  name: string;
  polygon: [number, number][]; // fractions of the frame
  rules: ZoneRule[];
  schedule: ScheduleWindow[];
  min_dwell_s: number;
  exclude: boolean;
  badge_door: string | null;
}

export interface Zone extends ZoneInput {
  id: string;
  camera_id: string;
  armed: boolean;
}

export interface Incident {
  id: string;
  bay_id: string;
  camera_id: string;
  kind: IncidentKind;
  zone_id: string | null;
  zone_name: string | null;
  detected_at: string;
  confidence: number;
  snapshot_url: string | null;
  detail: Record<string, unknown>;
  status: IncidentStatus;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
}

export interface SecurityStatus {
  face_recognition: boolean;
  face_models_installed: boolean;
  enrolled_people: number;
  badge_events_24h: number;
  last_badge_at: string | null;
  edge: { reported_at: string; detectors: string[] } | null;
}

export interface EnrolledPerson {
  id: string;
  name: string;
  employee_ref: string;
  consent_reference: string;
  enrolled_by: string;
  enrolled_at: string;
}

export interface BadgeEvent {
  id: string;
  badge_id: string;
  door: string;
  at: string;
  granted: boolean;
  holder: string | null;
}

// tally sheets: the paper counts the AI is judged against -------------------------

export type TallyStatus = "pending" | "matched" | "reconciled" | "conflict" | "unmatched";
export type TallyDirection = "LOAD" | "RETURN";

/** Blind by design: what was entered and where it stands, never the AI count. */
export interface TallySheet {
  id: string;
  sheet_id: string;
  bay_id: string;
  date: string;
  plate: string;
  direction: TallyDirection;
  start_time: string | null;
  end_time: string | null;
  lines: number;
  line_total: number | null;
  total_on_paper: number | null;
  truth: number | null;
  transcription_mismatch: boolean;
  counted_by: string | null;
  verified_by: string | null;
  status: TallyStatus;
  entered_by_user: string;
  entered_at: string;
}

export interface TallyLineInput {
  line_no: number;
  crates: number;
  note?: string | null;
}

export interface TallySheetInput {
  sheet_id: string;
  bay_id: string;
  date: string;
  plate: string;
  direction: TallyDirection;
  start_time?: string | null;
  end_time?: string | null;
  lines: TallyLineInput[];
  total_on_paper?: number | null;
  counted_by?: string | null;
  verified_by?: string | null;
  entered_by?: string | null;
  notes?: string | null;
}

export interface TallyImport {
  saved: TallySheet[];
  skipped: string[];
}

export interface TallyReportRow {
  sheet: TallySheet;
  session_id: string | null;
  session_status: SessionStatus | null;
  ai_count: number | null;
  variance: number | null;
  accuracy: number | null;
  passed: boolean | null;
}

export interface TallyReport {
  target: number;
  sheets: number;
  reconciled: number;
  passing: number;
  mean_accuracy: number | null;
  aggregate_error: number | null;
  rows: TallyReportRow[];
}

/** Proposal M2: an edge node's health is derived from its heartbeats, never assumed. */
export type NodeHealth = "never_seen" | "online" | "stale" | "offline" | "revoked";

export interface EdgeNodeCamera {
  api_camera_id: string;
  name: string | null;
  connected: boolean;
  fps: number | null;
  lag_s: number | null;
}

export interface EdgeNode {
  id: string;
  name: string;
  hostname: string;
  site_id: string;
  bay_id: string | null;
  status: "active" | "revoked";
  health: NodeHealth;
  enrolled_at: string;
  last_seen_at: string | null;
  version: string | null;
  uptime_s: number | null;
  spool_pending: number | null;
  cameras: EdgeNodeCamera[];
  config: Record<string, unknown>;
  config_version: string;
  applied_config_version: string | null;
  /** null until the node has said which configuration it runs */
  config_drift: boolean | null;
  /** what the node says it runs, by role (detector, layers) */
  models?: Record<string, { name?: string; version?: string; sha256?: string; path?: string }>;
  /** the last model the node refused to switch to, and why; it kept the one it had */
  model_error?: string | null;
  can_roll_back?: boolean;
}

/** Shown once: the platform keeps only a digest. */
export interface EnrollmentToken {
  token: string;
  name: string;
  site_id: string;
  bay_id: string | null;
  expires_at: string;
}

/** A few seconds of video around a count, kept with the load for the retention period. */
export interface EvidenceClip {
  id: string;
  session_id: string | null;
  camera_id: string;
  kind: "crossing" | "plate";
  started_at: string;
  ended_at: string;
  seconds: number;
  size_bytes: number;
  sha256: string;
  expires_at: string;
  /** signed and short-lived: plays in a <video> tag without a token */
  url: string;
}

export interface Balance {
  /** the truck's plate, the route, or the day (YYYY-MM-DD); "" when unknown */
  key: string;
  dispatched: number;
  returned: number;
  outstanding: number;
  loads_out: number;
  loads_back: number;
  /** loads still being counted: not in the totals yet */
  in_progress: number;
  corrected: number;
}

export type ExceptionKind = "count_mismatch" | "not_seen" | "unexpected";

export interface ManifestException {
  id: string;
  kind: ExceptionKind;
  day: string;
  plate: string | null;
  route: string;
  expected: number | null;
  counted: number | null;
  /** counted - expected: negative means crates that went and were not seen */
  difference: number | null;
  session_id: string | null;
  status: "open" | "resolved";
  raised_at: string;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
}

export interface ManifestImport {
  added: number;
  updated: number;
  errors: string[];
  exceptions_raised: number;
}

/** A daily report filed each morning for the day before. Links are signed and expire. */
export interface FiledReport {
  site_id: string;
  site: string;
  day: string;
  loads: number;
  generated_at: string;
  pdf_url: string;
  csv_url: string;
}

export type WebhookEvent = "session.closed" | "exception.raised";

export interface Webhook {
  id: string;
  url: string;
  events: WebhookEvent[];
  description: string;
  created_by: string;
  created_at: string;
}

/** Only when it is created: the secret is not shown again. */
export interface WebhookCreated extends Webhook {
  secret: string;
}

export interface WebhookDelivery {
  id: string;
  event_id: string;
  event: string;
  status: "pending" | "delivered" | "failed";
  attempts: number;
  next_attempt_at: string | null;
  last_status_code: number | null;
  last_error: string | null;
  delivered_at: string | null;
  replay_of: string | null;
  created_at: string;
}

export type Point = [number, number];

/** Where a camera counts: across a line (a chokepoint) or within a zone. Pixels. */
export interface NodeCameraConfig {
  api_camera_id: string;
  key?: string;
  line?: [Point, Point];
  zone?: [number, number, number, number];
  stride: number;
  frames?: "latest" | "all";
}

/** What a node runs (PUT /edge/nodes/{id}/config); keys the editor does not touch are kept. */
export interface NodeConfig {
  model: { path?: string; version_id?: string; arch?: "rtdetr" | "yolo" };
  layers_model?: string;
  layers_model_id?: string;
  forward_means?: "loading" | "offloading";
  count?: "stack" | "crate";
  cameras: NodeCameraConfig[];
  lpr_cameras: NodeCameraConfig[];
}

export interface PocCriterion {
  name: string;
  result: "pass" | "fail" | "not measured";
  figure: string;
  target: string;
  how: string;
  notes: string[];
}

export interface PocReport {
  site: string;
  start: string;
  end: string;
  timezone: string;
  generated_at: string;
  /** pass only when every criterion was measured and met */
  verdict: "pass" | "fail" | "incomplete";
  criteria: PocCriterion[];
  loads: number;
  dispatched: number;
  returned: number;
  outstanding: number;
  still_at_the_bay: number;
  corrections: number;
  correction_crates: number;
  outstanding_value: number | null;
  currency: string;
  exceptions: { kind: string; status: string; count: number }[];
  nodes: {
    name: string;
    measured_from: string;
    measured_to: string;
    uptime_pct: number | null;
    outages: number;
    down_minutes: number;
  }[];
}

export interface PocParams {
  start: string;
  end: string;
  baseline_minutes?: number;
  crate_value?: number;
  currency?: string;
}

export interface BillingEntitlements {
  plan: string;
  valid_until: string | null;
  limits: Record<string, number>;
  features: Record<string, boolean>;
  allowances: Record<string, number>;
}

export interface BillingSubscription {
  /** false: no plan, so nothing is limited and nothing is billed */
  subscribed: boolean;
  entitlements: BillingEntitlements | null;
  segments: { starts: string; plan: string; quantities: Record<string, number>; by: string }[];
  channels_in_use: { od: number; lpr: number };
  usage_this_month: Record<string, string>;
}

export interface BillingPlan {
  id: string;
  name: string;
  recurring: Record<string, number>;
  term_days: number | null;
}

export interface PriceBookView {
  version: string;
  /** made-up prices: invoices from it are stamped not for issue */
  placeholder: boolean;
  currency: string;
  plans: BillingPlan[];
}

export interface InvoiceLineView {
  sku: string;
  description: string;
  quantity: string;
  unit_price: string;
  amount: string;
}

export interface InvoiceView {
  number: string | null;
  period_start: string;
  period_end: string;
  currency: string;
  lines: InvoiceLineView[];
  subtotal: string;
  tax_name: string;
  tax: string;
  total: string;
  stamp: string | null;
  due_date: string | null;
  paid: string;
  settled: boolean;
}

export interface StatementView {
  period_start: string;
  period_end: string;
  billed_by: string;
  lines: { sku: string; description: string; quantity: string }[];
  usage: Record<string, string>;
}

// --- consoles (M8) ----------------------------------------------------------------------

export interface TenantRecord {
  id: string;
  slug: string;
  name: string;
  status: "provisioning" | "trial" | "active" | "past_due" | "suspended" | "expired" | "cancelled";
  partner_id: string | null;
  created_at: string | null;
  on_hold: boolean;
}

export interface PartnerRecord {
  id: string;
  slug: string;
  name: string;
}

export interface Provisioned {
  tenant: TenantRecord;
  owner_username: string;
  /** shown once, on the call that created the tenant; null on a repeat */
  temporary_password: string | null;
  created: boolean;
}

export type OnboardingStepName =
  | "tenant_created"
  | "owner_signed_in"
  | "plan_set"
  | "site_and_bay"
  | "enrollment_token"
  | "node_enrolled"
  | "node_reporting";

export interface OnboardingStep {
  name: OnboardingStepName;
  done: boolean;
  /** null where no time was recorded */
  at: string | null;
  detail: string;
}

export interface OnboardingNode {
  id: string;
  name: string;
  site_id: string;
  bay_id: string | null;
  health: string;
  enrolled_at: string;
  last_seen_at: string | null;
  version: string | null;
}

export interface Onboarding {
  tenant: TenantRecord;
  steps: OnboardingStep[];
  sites: Site[];
  bays: Bay[];
  nodes: OnboardingNode[];
  /** null until a node has enrolled */
  seconds_to_first_node: number | null;
  target_seconds: number;
  within_target: boolean | null;
}

export interface PartnerInvoiceView {
  partner: string;
  number: string | null;
  period_start: string;
  period_end: string;
  currency: string;
  customers: { tenant_id: string; tenant_name: string; lines: InvoiceLineView[]; subtotal: string }[];
  subtotal: string;
  tax_name: string;
  tax: string;
  total: string;
  price_book: string;
  placeholder: boolean;
  stamp: string | null;
  issued_at: string | null;
  due_date: string | null;
  paid: string;
  settled: boolean;
}

// --- break-glass support access (M8, T8.3) ----------------------------------------------

export type GrantState = "pending" | "active" | "denied" | "ended" | "expired" | "lapsed";

export interface BreakGlassGrant {
  id: string;
  tenant_id: string;
  tenant_name: string;
  requested_by: string;
  reason: string;
  minutes: number;
  requested_at: string;
  state: GrantState;
  decided_by: string | null;
  decided_at: string | null;
  /** null until approved */
  expires_at: string | null;
  ended_by: string | null;
  ended_at: string | null;
}
