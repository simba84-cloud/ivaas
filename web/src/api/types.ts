export type CameraRole =
  | "overhead"
  | "side_high"
  | "side_mid"
  | "side_low"
  | "chokepoint"
  | "lpr";
export type CameraStatus = "online" | "degraded" | "offline";
export type Direction = "loading" | "offloading";
export type SessionStatus = "open" | "closed" | "reconciled" | "disputed";

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

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ToolUse {
  name: string;
  arguments: Record<string, unknown>;
}
