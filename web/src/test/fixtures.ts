import type {
  AnalysisJob,
  Bay,
  EdgeNode,
  Camera,
  Overview,
  Session,
  Site,
  Summary,
} from "../api/types";

export const site: Site = {
  id: "s", // matches bay.site_id below
  name: "Bakery Industrial Site",
  timezone: "UTC",
};

export const bay: Bay = {
  id: "0bc39dce-7ea1-5331-b0dc-4ffcd94bbfd3",
  site_id: "s",
  name: "Loading Bay",
  height_m: 4,
  width_m: 3,
};

export const camera = (over: Partial<Camera> = {}): Camera => ({
  id: "c1",
  bay_id: bay.id,
  name: "Chokepoint 1",
  role: "chokepoint",
  stream_path: "bay-poc/chokepoint-1",
  status: "offline",
  last_seen_at: null,
  protocol: "push",
  source_url: null,
  ...over,
});

export const session = (over: Partial<Session> = {}): Session => ({
  id: "s1",
  bay_id: bay.id,
  direction: "loading",
  status: "closed",
  plate: "ABC 1234",
  ai_count: 42,
  manual_count: null,
  variance: null,
  accuracy: null,
  opened_at: "2026-09-23T08:00:00Z",
  closed_at: "2026-09-23T08:30:00Z",
  approved_by: null,
  approved_at: null,
  approval_reason: null,
  approval_note: null,
  ...over,
});

export const summary: Summary = {
  sessions_today: 3,
  crates_today: 120,
  open_sessions: 1,
  verified_sessions: 2,
  mean_accuracy: 0.955,
  cameras_online: 2,
  cameras_total: 17,
};

export const job = (over: Partial<AnalysisJob> = {}): AnalysisJob => ({
  id: "j1",
  bay_id: bay.id,
  filename: "loading.mp4",
  status: "done",
  progress: 1,
  error: null,
  created_by: "operator",
  created_at: "2026-09-23T09:00:00Z",
  started_at: "2026-09-23T09:00:05Z",
  finished_at: "2026-09-23T09:06:00Z",
  duration_s: 100,
  total_crates: 16,
  loads: [{ start_s: 32, end_s: 88, stacks: 3, crates: 16, low_confidence: 0, plate: null }],
  timeline: [
    { at_s: 32, kind: "load_started", detail: "Load 1 started", frame_url: null },
    { at_s: 32, kind: "stack_counted", detail: "Stack of 6 crates", frame_url: "/api/v1/objects/frames/j1/a.jpg?exp=1&sig=x" },
    { at_s: 62, kind: "stack_counted", detail: "Stack of 5 crates", frame_url: "/api/v1/objects/frames/j1/b.jpg?exp=1&sig=y" },
    { at_s: 88, kind: "load_ended", detail: "Load 1 ended: 3 stacks, 16 crates", frame_url: null },
  ],
  summary: "One truck load, three stacks, 16 crates.",
  video_url: "/api/v1/objects/uploads/j1/loading.mp4?exp=1&sig=z",
  ...over,
});

export const overview = (over: Partial<Overview> = {}): Overview => ({
  generated_at: "2026-09-24T12:00:00Z",
  days: 14,
  crates_today: 120,
  sessions_today: 3,
  open_sessions: 1,
  verified_sessions: 2,
  unverified_sessions: 1,
  disputed_sessions: 1,
  reconciled_sessions: 4,
  approved_sessions: 0,
  mean_accuracy: 0.962,
  cameras_online: 2,
  cameras_total: 4,
  crates: { value: 640, delta_pct: 0.18, series: [20, 35, 41, 38, 52, 60, 48, 55, 61, 44, 58, 66, 62, 120] },
  throughput: { value: 26, delta_pct: -0.1, series: [1, 2, 2, 1, 3, 2, 2, 3, 2, 1, 2, 3, 2, 3] },
  accuracy: { value: 0.962, delta_pct: 0.012, series: [0, 0.94, 0.95, 0.96, 0.95, 0.97, 0.96, 0.95, 0.96, 0.97, 0.96, 0.97, 0.96, 0.97] },
  daily: Array.from({ length: 14 }, (_, i) => ({
    day: `2026-09-${String(i + 11).padStart(2, "0")}`,
    crates: [20, 35, 41, 38, 52, 60, 48, 55, 61, 44, 58, 66, 62, 120][i],
    sessions: 2,
    accuracy: i === 0 ? null : 0.96,
  })),
  insights: [
    {
      key: "cameras_offline",
      severity: "warn",
      title: "2 of 4 cameras are not streaming",
      detail: "Door 3, Door 4. Crates passing an offline camera are not counted.",
      metric: "2/4",
    },
    {
      key: "accuracy_on_target",
      severity: "good",
      title: "Counting accuracy is meeting the 95% target",
      detail: "96.2% across 2 verified loads.",
      metric: "96.2%",
    },
  ],
  ...over,
});

/**
 * The seeded personas as the API describes them since roles were scoped: their
 * roles and the permissions those grant (services/api/src/ivaas/domain/rbac.py).
 * "admin", "operator" and "viewer" are the accounts, not roles, so tests read the same.
 */
const PERSONAS: Record<string, { roles: string[]; permissions: string[] }> = {
  owner: {
    roles: ["tenant_owner"],
    permissions: ["subscription.manage", "invoice.read", "user.invite", "count.read", "report.export", "audit.read"],
  },
  admin: {
    roles: ["site_manager", "tenant_admin"],
    permissions: [
      "user.invite",
      "user.manage",
      "device.register",
      "device.calibrate",
      "video.live.view",
      "count.read",
      "count.override",
      "groundtruth.enter",
      "reconciliation.resolve",
      "report.export",
      "audit.read",
      "assistant.query",
      "topology.read",
      "site.manage",
      "session.operate",
      "settings.manage",
      "security.manage",
    ],
  },
  operator: {
    roles: ["bay_operator"],
    permissions: [
      "video.live.view",
      "count.read",
      "count.override",
      "groundtruth.enter",
      "assistant.query",
      "topology.read",
      "session.operate",
    ],
  },
  viewer: {
    roles: ["auditor"],
    permissions: ["count.read", "report.export", "audit.read", "assistant.query", "topology.read"],
  },
};

export const meAs = (personas: string[]) => {
  const held = personas.map((p) => PERSONAS[p]).filter(Boolean);
  return {
    subject: personas[0] ?? "u",
    name: personas[0] ?? "u",
    roles: [...new Set(held.flatMap((h) => h.roles))],
    permissions: [...new Set(held.flatMap((h) => h.permissions))],
    tenant: { id: "t", slug: "bakers-inn", name: "Bakers Inn", status: "trial" },
  };
};

export const edgeNode = (over: Partial<EdgeNode> = {}): EdgeNode => ({
  id: "n1",
  name: "Loading bay edge",
  hostname: "edge-01",
  site_id: site.id,
  bay_id: bay.id,
  status: "active",
  health: "online",
  enrolled_at: "2026-09-30T08:00:00Z",
  last_seen_at: "2026-09-30T09:00:00Z",
  version: "0.2.0",
  uptime_s: 3700,
  spool_pending: 0,
  cameras: [{ api_camera_id: "c1", name: "Chokepoint 1", connected: true, fps: 7.5, lag_s: 0.1 }],
  config: { model: { path: "/models/stacks-v2.onnx" } },
  config_version: "abc123",
  applied_config_version: "abc123",
  config_drift: false,
  ...over,
});
