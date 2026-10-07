import { getBreakGlass, getToken, type Me } from "../auth/session";
import type {
  Acknowledgement,
  BadgeEvent,
  EnrolledPerson,
  Incident,
  IncidentKind,
  IncidentStatus,
  SecurityStatus,
  Zone,
  ZoneInput,
  AnalysisJob,
  ApprovalReason,
  AuditEntry,
  PlatformSettings,
  TemporaryPassword,
  User,
  UserRole,
  Bay,
  Camera,
  CameraRole,
  ChatTurn,
  DiscoveredDevice,
  DiscoveredStream,
  Direction,
  EdgeNode,
  EnrollmentToken,
  EvidenceClip,
  Balance,
  FiledReport,
  FleetImport,
  ManifestException,
  ManifestImport,
  OverrideReason,
  Vehicle,
  Session,
  Site,
  Overview,
  Summary,
  TallyImport,
  TallyReport,
  TallySheet,
  TallySheetInput,
  ToolUse,
  BillingSubscription,
  InvoiceView,
  NodeConfig,
  PocParams,
  PriceBookView,
  StatementView,
  PocReport,
  Webhook,
  WebhookCreated,
  WebhookDelivery,
  WebhookEvent,
  Onboarding,
  PartnerInvoiceView,
  PartnerRecord,
  Provisioned,
  TenantRecord,
  BreakGlassGrant,
  CertificateView,
  CommissionView,
  LifecycleView,
  SsoView,
  RevenueView,
  TenantFleet,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    ...init,
    headers: {
      // a FormData body sets its own multipart content type, boundary included
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(getBreakGlass() ? { "X-IVaaS-Break-Glass": getBreakGlass()! } : {}),
      ...init?.headers,
    },
  });
  if (res.status === 401) {
    window.dispatchEvent(new Event("ivaas:unauthorized"));
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (res.status === 403 && /^break-glass access has ended/.test(body.detail ?? "")) {
      window.dispatchEvent(new Event("ivaas:break-glass-ended"));
    }
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

/** The POC report's query: only what was given, so nothing defaults to a made-up figure. */
function pocQuery(siteId: string, p: PocParams): string {
  const q = new URLSearchParams({ site_id: siteId, start: p.start, end: p.end });
  if (p.baseline_minutes !== undefined) q.set("baseline_minutes", String(p.baseline_minutes));
  if (p.crate_value !== undefined) q.set("crate_value", String(p.crate_value));
  if (p.currency) q.set("currency", p.currency);
  return q.toString();
}

export type ReportFormat = "pdf" | "csv" | "xlsx";
export interface ReportFile {
  blob: Blob;
  name: string;
}

/** A file fetched with the token, named as the API's Content-Disposition says. */
async function reportFile(url: string, fallback: string): Promise<ReportFile> {
  const token = getToken();
  const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`);
  }
  const named = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "");
  return { blob: await res.blob(), name: named?.[1] ?? fallback };
}

/** Hand a fetched file to the browser to save. */
export function saveFile({ blob, name }: ReportFile) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export const api = {
  me: () => request<Me>("/api/v1/auth/me"),
  edgeNodes: () => request<EdgeNode[]>("/api/v1/edge/nodes"),
  createEnrollmentToken: (siteId: string, name: string, bayId?: string, ttlHours = 24) =>
    request<EnrollmentToken>(`/api/v1/sites/${siteId}/enrollment-tokens`, {
      method: "POST",
      body: JSON.stringify({ name, bay_id: bayId ?? null, ttl_hours: ttlHours }),
    }),
  revokeNode: (nodeId: string) =>
    request<void>(`/api/v1/edge/nodes/${nodeId}`, { method: "DELETE" }),
  reports: () => request<FiledReport[]>("/api/v1/reports"),
  billing: () => request<BillingSubscription>("/api/v1/billing/subscription"),
  priceBook: () => request<PriceBookView>("/api/v1/billing/price-book"),
  billingDraft: (period: string) =>
    request<InvoiceView>(`/api/v1/billing/invoices/draft?period=${period}`),
  billingStatement: (period: string) =>
    request<StatementView>(`/api/v1/billing/statement?period=${period}`),
  invoices: () => request<InvoiceView[]>("/api/v1/billing/invoices"),
  changePlan: (plan: string, quantities: Record<string, number>) =>
    request<BillingSubscription>("/api/v1/billing/subscription", {
      method: "PUT",
      body: JSON.stringify({ plan, quantities }),
    }),
  // consoles (M8): platform staff see every tenant, a partner its own customers
  tenants: () => request<TenantRecord[]>("/api/v1/platform/tenants"),
  tenant: (id: string) => request<TenantRecord>(`/api/v1/platform/tenants/${id}`),
  partners: () => request<PartnerRecord[]>("/api/v1/platform/partners"),
  provisionTenant: (
    body: { slug: string; name: string; owner_username: string; owner_display_name?: string; partner_id?: string | null },
    idempotencyKey: string,
  ) =>
    request<Provisioned>("/api/v1/platform/tenants", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(body),
    }),
  onboarding: (id: string) => request<Onboarding>(`/api/v1/platform/tenants/${id}/onboarding`),
  onboardingSite: (id: string, body: { site_name: string; timezone: string; bay_name: string }) =>
    request<{ site: Site; bay: Bay }>(`/api/v1/platform/tenants/${id}/onboarding/site`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  onboardingToken: (id: string, body: { site_id: string; bay_id: string | null; name: string; ttl_hours: number }) =>
    request<EnrollmentToken>(`/api/v1/platform/tenants/${id}/onboarding/enrollment-tokens`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  tenantPlan: (id: string) => request<BillingSubscription>(`/api/v1/platform/tenants/${id}/subscription`),
  setTenantPlan: (id: string, plan: string, quantities: Record<string, number>) =>
    request<BillingSubscription>(`/api/v1/platform/tenants/${id}/subscription`, {
      method: "PUT",
      body: JSON.stringify({ plan, quantities }),
    }),
  tenantInvoices: (id: string) => request<InvoiceView[]>(`/api/v1/platform/tenants/${id}/invoices`),
  tenantDraft: (id: string, period: string) =>
    request<InvoiceView>(`/api/v1/platform/tenants/${id}/invoices/draft?period=${period}`),
  issueTenantInvoice: (id: string, period: string) =>
    request<InvoiceView>(`/api/v1/platform/tenants/${id}/invoices?period=${period}`, { method: "POST" }),
  payTenantInvoice: (id: string, number: string, amount: string, reference: string) =>
    request<InvoiceView>(`/api/v1/platform/tenants/${id}/invoices/${number}/payments`, {
      method: "POST",
      body: JSON.stringify({ amount, reference }),
    }),
  holdTenant: (id: string, onHold: boolean, reason = "") =>
    request<{ tenant_id: string; status: string; on_hold: boolean }>(`/api/v1/platform/tenants/${id}/hold`, {
      method: "PUT",
      body: JSON.stringify({ on_hold: onHold, reason }),
    }),
  partnerInvoices: (partnerId: string) =>
    request<PartnerInvoiceView[]>(`/api/v1/platform/partners/${partnerId}/invoices`),
  partnerCommission: (partnerId: string, period: string) =>
    request<CommissionView>(`/api/v1/platform/partners/${partnerId}/commission?period=${period}`),
  partnerDraft: (partnerId: string, period: string) =>
    request<PartnerInvoiceView>(`/api/v1/platform/partners/${partnerId}/invoices/draft?period=${period}`),
  issuePartnerInvoice: (partnerId: string, period: string) =>
    request<PartnerInvoiceView>(`/api/v1/platform/partners/${partnerId}/invoices?period=${period}`, {
      method: "POST",
    }),
  payPartnerInvoice: (number: string, amount: string, reference: string) =>
    request<PartnerInvoiceView>(`/api/v1/platform/partner-invoices/${number}/payments`, {
      method: "POST",
      body: JSON.stringify({ amount, reference }),
    }),
  // break-glass (M8): support asks, the tenant's owner decides
  requestBreakGlass: (tenantId: string, reason: string, minutes: number) =>
    request<BreakGlassGrant>(`/api/v1/platform/tenants/${tenantId}/break-glass`, {
      method: "POST",
      body: JSON.stringify({ reason, minutes }),
    }),
  myBreakGlass: () => request<BreakGlassGrant[]>("/api/v1/platform/break-glass"),
  endMyBreakGlass: (id: string) =>
    request<BreakGlassGrant>(`/api/v1/platform/break-glass/${id}/end`, { method: "POST" }),
  supportAccess: () => request<BreakGlassGrant[]>("/api/v1/support-access"),
  decideSupportAccess: (id: string, action: "approve" | "deny" | "end") =>
    request<BreakGlassGrant>(`/api/v1/support-access/${id}/${action}`, { method: "POST" }),
  // the end of a tenant (M8)
  myLifecycle: () => request<LifecycleView>("/api/v1/account/lifecycle"),
  cancelMine: (confirm: string, reason: string) =>
    request<LifecycleView>("/api/v1/account/cancel", { method: "POST", body: JSON.stringify({ confirm, reason }) }),
  /** The whole export, as a file the browser saves. */
  exportMine: async (): Promise<void> => {
    const token = getToken();
    const res = await fetch("/api/v1/account/export", {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
    }
    const name = /filename="?([^";]+)"?/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "export.zip";
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    a.click();
    URL.revokeObjectURL(url);
  },
  tenantLifecycle: (id: string) => request<LifecycleView>(`/api/v1/platform/tenants/${id}/lifecycle`),
  changeLifecycle: (id: string, action: "cancel" | "reinstate", confirm = "", reason = "") =>
    request<LifecycleView>(`/api/v1/platform/tenants/${id}/${action}`, {
      method: "POST",
      body: action === "cancel" ? JSON.stringify({ confirm, reason }) : undefined,
    }),
  purgeTenant: (id: string, confirm: string) =>
    request<CertificateView>(`/api/v1/platform/tenants/${id}/purge`, {
      method: "POST",
      body: JSON.stringify({ confirm }),
    }),
  certificates: () => request<CertificateView[]>("/api/v1/platform/deletion-certificates"),
  sso: () => request<SsoView>("/api/v1/sso"),
  saveSso: (body: {
    issuer: string;
    client_id: string;
    client_secret: string;
    domains: string[];
    default_role: string | null;
    required: boolean;
  }) => request<SsoView>("/api/v1/sso", { method: "PUT", body: JSON.stringify(body) }),
  removeSso: () => request<SsoView>("/api/v1/sso", { method: "DELETE" }),
  fleetView: () => request<TenantFleet[]>("/api/v1/platform/fleet"),
  revenue: (months = 6) => request<RevenueView>(`/api/v1/platform/revenue?months=${months}`),
  webhooks: () => request<Webhook[]>("/api/v1/webhooks"),
  createWebhook: (url: string, events: WebhookEvent[], description: string) =>
    request<WebhookCreated>("/api/v1/webhooks", {
      method: "POST",
      body: JSON.stringify({ url, events, description }),
    }),
  deleteWebhook: (id: string) => request<void>(`/api/v1/webhooks/${id}`, { method: "DELETE" }),
  webhookDeliveries: (id: string) =>
    request<WebhookDelivery[]>(`/api/v1/webhooks/${id}/deliveries`),
  testWebhook: (id: string) =>
    request<WebhookDelivery>(`/api/v1/webhooks/${id}/test`, { method: "POST" }),
  replayDelivery: (id: string) =>
    request<WebhookDelivery>(`/api/v1/webhooks/deliveries/${id}/replay`, { method: "POST" }),
  /** Any day's report, built now; fetched with the token and handed back as a file,
   *  under the name the API gives it (liquid-ivaas-<site>-<day>). */
  dailyReport: (siteId: string, day: string, format: ReportFormat): Promise<ReportFile> =>
    reportFile(`/api/v1/reports/daily?site_id=${siteId}&day=${day}&format=${format}`, `report.${format}`),
  pocReport: (siteId: string, p: PocParams) =>
    request<PocReport>(`/api/v1/reports/poc?${pocQuery(siteId, p)}`),
  /** The tally sheet workbook, made for this tenant: its bays are in the drop-down. */
  tallyTemplate: (): Promise<ReportFile> => reportFile("/api/v1/tally/template", "tally-sheet.xlsx"),
  /** The POC report as a file, fetched with the token. */
  pocReportFile: (siteId: string, p: PocParams, format: ReportFormat): Promise<ReportFile> =>
    reportFile(`/api/v1/reports/poc?${pocQuery(siteId, p)}&format=${format}`, `poc-report.${format}`),
  balances: (by: "truck" | "route" | "day", days: number) =>
    request<Balance[]>(`/api/v1/balances?by=${by}&days=${days}`),
  exceptions: (status: "open" | "resolved") =>
    request<ManifestException[]>(`/api/v1/exceptions?status=${status}`),
  resolveException: (id: string, note: string) =>
    request<ManifestException>(`/api/v1/exceptions/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),
  importManifest: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ManifestImport>("/api/v1/manifests/import", { method: "POST", body: form });
  },
  fleet: () => request<Vehicle[]>("/api/v1/fleet"),
  addVehicle: (v: { plate: string; fleet_number?: string; operator?: string }) =>
    request<Vehicle>("/api/v1/fleet", { method: "POST", body: JSON.stringify(v) }),
  importFleet: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<FleetImport>("/api/v1/fleet/import", { method: "POST", body: form });
  },
  assignVehicle: (sessionId: string, body: { vehicle_id?: string; plate?: string; note?: string }) =>
    request<Session>(`/api/v1/sessions/${sessionId}/vehicle`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  overrideCount: (sessionId: string, body: { count: number; reason: OverrideReason; note?: string }) =>
    request<Session>(`/api/v1/sessions/${sessionId}/override`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  sessionEvidence: (sessionId: string) =>
    request<EvidenceClip[]>(`/api/v1/sessions/${sessionId}/evidence`),
  setNodeConfig: (nodeId: string, config: NodeConfig) =>
    request<EdgeNode>(`/api/v1/edge/nodes/${nodeId}/config`, {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  rollBackNode: (nodeId: string) =>
    request<EdgeNode>(`/api/v1/edge/nodes/${nodeId}/rollback`, { method: "POST" }),
  tallySheets: () => request<TallySheet[]>("/api/v1/tally/sheets"),
  tallyReport: () => request<TallyReport>("/api/v1/tally/report"),
  /** The accuracy report as a branded file, for one bay when given: what the page shows. */
  tallyReportFile: (format: "pdf" | "xlsx", bayId?: string): Promise<ReportFile> =>
    reportFile(
      `/api/v1/tally/report?format=${format}${bayId ? `&bay_id=${bayId}` : ""}`,
      `accuracy.${format}`,
    ),
  enterTallySheet: (body: TallySheetInput) =>
    request<TallySheet>("/api/v1/tally/sheets", { method: "POST", body: JSON.stringify(body) }),
  rematchTally: () =>
    request<{ changed: TallySheet[] }>("/api/v1/tally/rematch", { method: "POST" }),
  importTally: (bayId: string, sheets: File, stacks?: File | null) => {
    const form = new FormData();
    form.append("sheets", sheets);
    if (stacks) form.append("stacks", stacks);
    return request<TallyImport>(`/api/v1/tally/import?bay_id=${bayId}`, {
      method: "POST",
      body: form,
    });
  },
  summary: () => request<Summary>("/api/v1/summary"),
  users: () => request<User[]>("/api/v1/users"),
  createUser: (username: string, display_name: string, roles: UserRole[]) =>
    request<TemporaryPassword>("/api/v1/users", {
      method: "POST",
      body: JSON.stringify({ username, display_name, roles }),
    }),
  assignRoles: (username: string, roles: UserRole[]) =>
    request<User>(`/api/v1/users/${username}/roles`, {
      method: "PUT",
      body: JSON.stringify({ roles }),
    }),
  setUserEnabled: (username: string, enabled: boolean) =>
    request<User>(`/api/v1/users/${username}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  resetPassword: (username: string) =>
    request<TemporaryPassword>(`/api/v1/users/${username}/reset-password`, { method: "POST" }),
  changePassword: (current_password: string, new_password: string) =>
    request<{ access_token: string }>("/api/v1/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),
  settings: () => request<PlatformSettings>("/api/v1/settings"),
  setSetting: (key: string, value: unknown) =>
    request<PlatformSettings>(`/api/v1/settings/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  audit: (params: { days?: number; actor?: string; action?: string } = {}) => {
    const q = new URLSearchParams({ days: String(params.days ?? 7), limit: "200" });
    if (params.actor) q.set("actor", params.actor);
    if (params.action) q.set("action", params.action);
    return request<AuditEntry[]>(`/api/v1/audit?${q}`);
  },
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
  acknowledgements: () => request<Acknowledgement[]>("/api/v1/alerts/acknowledgements"),
  acknowledge: (key: string, title: string, note?: string) =>
    request<Acknowledgement>("/api/v1/alerts/acknowledgements", {
      method: "POST",
      body: JSON.stringify({ key, title, note: note || null }),
    }),
  securityStatus: () => request<SecurityStatus>("/api/v1/security/status"),
  zones: (bayId: string) => request<Zone[]>(`/api/v1/bays/${bayId}/zones`),
  createZone: (cameraId: string, body: ZoneInput) =>
    request<Zone>(`/api/v1/cameras/${cameraId}/zones`, { method: "POST", body: JSON.stringify(body) }),
  updateZone: (id: string, body: ZoneInput) =>
    request<Zone>(`/api/v1/zones/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteZone: (id: string) => request<void>(`/api/v1/zones/${id}`, { method: "DELETE" }),
  /** A still frame to draw zones on; null when the camera is not streaming. */
  cameraSnapshot: async (cameraId: string): Promise<Blob | null> => {
    const token = getToken();
    const res = await fetch(`/api/v1/cameras/${cameraId}/snapshot`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    return res.ok ? res.blob() : null;
  },
  incidents: (params: { bayId?: string; status?: IncidentStatus; kind?: IncidentKind; days?: number } = {}) => {
    const q = new URLSearchParams({ days: String(params.days ?? 7) });
    if (params.bayId) q.set("bay_id", params.bayId);
    if (params.status) q.set("status", params.status);
    if (params.kind) q.set("kind", params.kind);
    return request<Incident[]>(`/api/v1/incidents?${q}`);
  },
  acknowledgeIncident: (id: string) =>
    request<Incident>(`/api/v1/incidents/${id}/acknowledge`, { method: "POST" }),
  resolveIncident: (id: string, note: string) =>
    request<Incident>(`/api/v1/incidents/${id}/resolve`, { method: "POST", body: JSON.stringify({ note }) }),
  badges: (hours = 24) => request<BadgeEvent[]>(`/api/v1/badges?hours=${hours}`),
  people: () => request<EnrolledPerson[]>("/api/v1/people"),
  enrol: async (form: FormData): Promise<EnrolledPerson> => {
    // multipart: no JSON content type, the browser sets the boundary
    const token = getToken();
    const res = await fetch("/api/v1/people", {
      method: "POST",
      body: form,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (res.status === 401) window.dispatchEvent(new Event("ivaas:unauthorized"));
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`);
    }
    return res.json();
  },
  removePerson: (id: string) => request<void>(`/api/v1/people/${id}`, { method: "DELETE" }),
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
