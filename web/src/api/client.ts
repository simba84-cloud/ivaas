import { getToken, type Me } from "../auth/session";
import type {
  AnalysisJob,
  ApprovalReason,
  Bay,
  Camera,
  CameraRole,
  ChatTurn,
  DiscoveredDevice,
  DiscoveredStream,
  Direction,
  Session,
  Site,
  Overview,
  Summary,
  ToolUse,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (res.status === 401) {
    window.dispatchEvent(new Event("ivaas:unauthorized"));
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  me: () => request<Me>("/api/v1/auth/me"),
  summary: () => request<Summary>("/api/v1/summary"),
  platformConfig: () => request<{ max_upload_mb: number }>("/api/v1/config"),
  overview: (days = 14, bayId?: string) =>
    request<Overview>(
      `/api/v1/analytics/overview?days=${days}${bayId ? `&bay_id=${bayId}` : ""}`,
    ),
  bays: () => request<Bay[]>("/api/v1/bays"),
  sites: () => request<Site[]>("/api/v1/sites"),
  siteBays: (siteId: string) => request<Bay[]>(`/api/v1/sites/${siteId}/bays`),
  createSite: (name: string) =>
    request<Site>("/api/v1/sites", { method: "POST", body: JSON.stringify({ name }) }),
  createBay: (siteId: string, name: string) =>
    request<Bay>(`/api/v1/sites/${siteId}/bays`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  cameras: (bayId: string) => request<Camera[]>(`/api/v1/bays/${bayId}/cameras`),
  addCamera: (bayId: string, body: { name: string; role: CameraRole; source_url: string | null }) =>
    request<Camera>(`/api/v1/bays/${bayId}/cameras`, { method: "POST", body: JSON.stringify(body) }),
  removeCamera: (id: string) => request<void>(`/api/v1/cameras/${id}`, { method: "DELETE" }),
  discover: () => request<DiscoveredDevice[]>("/api/v1/discovery/onvif", { method: "POST" }),
  discoverStreams: (address: string, username: string, password: string) =>
    request<DiscoveredStream[]>("/api/v1/discovery/onvif/streams", {
      method: "POST",
      body: JSON.stringify({ address, username, password }),
    }),
  assistantStatus: () =>
    request<{ enabled: boolean; model: string | null }>("/api/v1/assistant/status"),
  chat: (messages: ChatTurn[]) =>
    request<{ reply: string; tools_used: ToolUse[] }>("/api/v1/assistant/chat", {
      method: "POST",
      body: JSON.stringify({ messages }),
    }),
  analyses: () => request<AnalysisJob[]>("/api/v1/analysis"),
  analysis: (id: string) => request<AnalysisJob>(`/api/v1/analysis/${id}`),
  uploadVideo: (bayId: string, file: File, onProgress?: (frac: number) => void) =>
    new Promise<AnalysisJob>((resolve, reject) => {
      // XMLHttpRequest rather than fetch: it is the only way to get upload progress
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/v1/analysis?bay_id=${bayId}`);
      const token = getToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
      xhr.onload = () => {
        if (xhr.status === 202) resolve(JSON.parse(xhr.responseText));
        else if (xhr.status === 401) {
          window.dispatchEvent(new Event("ivaas:unauthorized"));
          reject(new Error("Signed out"));
        } else {
          let detail = `${xhr.status} ${xhr.statusText}`;
          try {
            detail = JSON.parse(xhr.responseText).detail ?? detail;
          } catch {
            /* not JSON */
          }
          reject(new Error(detail));
        }
      };
      xhr.onerror = () => reject(new Error("Upload failed"));
      const body = new FormData();
      body.append("file", file);
      xhr.send(body);
    }),
  sessions: (bayId?: string) =>
    request<Session[]>(`/api/v1/sessions?limit=100${bayId ? `&bay_id=${bayId}` : ""}`),
  openSession: (bay_id: string, direction: Direction) =>
    request<Session>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ bay_id, direction }),
    }),
  closeSession: (id: string) =>
    request<Session>(`/api/v1/sessions/${id}/close`, { method: "POST" }),
  approve: (id: string, reason: ApprovalReason, note?: string) =>
    request<Session>(`/api/v1/sessions/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ reason, note: note || null }),
    }),
  reconcile: (id: string, manual_count: number) =>
    request<Session>(`/api/v1/sessions/${id}/reconcile`, {
      method: "POST",
      body: JSON.stringify({ manual_count }),
    }),
};
