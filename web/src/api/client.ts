import { getToken, type Me } from "../auth/session";
import type {
  Bay,
  Camera,
  CameraRole,
  ChatTurn,
  DiscoveredDevice,
  DiscoveredStream,
  Direction,
  Session,
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
  bays: () => request<Bay[]>("/api/v1/bays"),
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
  sessions: () => request<Session[]>("/api/v1/sessions?limit=100"),
  openSession: (bay_id: string, direction: Direction) =>
    request<Session>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ bay_id, direction }),
    }),
  closeSession: (id: string) =>
    request<Session>(`/api/v1/sessions/${id}/close`, { method: "POST" }),
  reconcile: (id: string, manual_count: number) =>
    request<Session>(`/api/v1/sessions/${id}/reconcile`, {
      method: "POST",
      body: JSON.stringify({ manual_count }),
    }),
};
