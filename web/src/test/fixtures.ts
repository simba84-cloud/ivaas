import type { AnalysisJob, Bay, Camera, Overview, Session, Summary } from "../api/types";

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
