import { useQuery } from "@tanstack/react-query";
import {
  BadgeCheck,
  BellRing,
  Eraser,
  PenTool,
  ScanFace,
  ShieldAlert,
  UserMinus,
  Boxes,
  Camera,
  CameraOff,
  CheckCircle2,
  ClipboardCheck,
  FileVideo,
  LogIn,
  KeyRound,
  MapPin,
  SlidersHorizontal,
  Truck,
  UserCheck,
  UserCog,
  UserPlus,
  UserX,
  Warehouse,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { AuditAction, AuditEntry } from "../api/types";
import { EmptyState, dateTime } from "../components/ui";
import { Rise, Segmented, SkeletonList } from "../motion";

/** Each action in the words an operator would use, with the icon it earns. */
const ACTIONS: Record<AuditAction, { label: string; icon: LucideIcon; tone: string }> = {
  signed_in: { label: "Signed in", icon: LogIn, tone: "text-muted bg-ground" },
  session_opened: { label: "Opened a load", icon: Truck, tone: "text-brand bg-brand-tint" },
  session_closed: { label: "Closed a load", icon: Boxes, tone: "text-brand bg-brand-tint" },
  session_reconciled: {
    label: "Verified a count",
    icon: ClipboardCheck,
    tone: "text-accent bg-accent-tint",
  },
  session_approved: {
    label: "Signed off a dispute",
    icon: CheckCircle2,
    tone: "text-good bg-good/10",
  },
  camera_registered: { label: "Added a camera", icon: Camera, tone: "text-brand bg-brand-tint" },
  camera_removed: { label: "Removed a camera", icon: CameraOff, tone: "text-bad bg-bad/10" },
  video_uploaded: { label: "Uploaded footage", icon: FileVideo, tone: "text-brand bg-brand-tint" },
  site_created: { label: "Added a site", icon: MapPin, tone: "text-brand bg-brand-tint" },
  bay_created: { label: "Added a bay", icon: Warehouse, tone: "text-brand bg-brand-tint" },
  user_created: { label: "Added a user", icon: UserPlus, tone: "text-brand bg-brand-tint" },
  user_roles_changed: { label: "Changed a role", icon: UserCog, tone: "text-warn bg-warn/10" },
  user_enabled: { label: "Enabled a user", icon: UserCheck, tone: "text-good bg-good/10" },
  user_disabled: { label: "Disabled a user", icon: UserX, tone: "text-bad bg-bad/10" },
  password_reset: { label: "Reset a password", icon: KeyRound, tone: "text-bad bg-bad/10" },
  password_changed: { label: "Changed their password", icon: KeyRound, tone: "text-good bg-good/10" },
  setting_changed: {
    label: "Changed a rule",
    icon: SlidersHorizontal,
    tone: "text-warn bg-warn/10",
  },
  alert_acknowledged: {
    label: "Acknowledged an alert",
    icon: BellRing,
    tone: "text-brand bg-brand-tint",
  },
  zone_saved: { label: "Saved a security zone", icon: PenTool, tone: "text-warn bg-warn/10" },
  zone_deleted: { label: "Deleted a security zone", icon: Eraser, tone: "text-bad bg-bad/10" },
  incident_acknowledged: {
    label: "Acknowledged an incident",
    icon: ShieldAlert,
    tone: "text-brand bg-brand-tint",
  },
  incident_resolved: { label: "Resolved an incident", icon: BadgeCheck, tone: "text-good bg-good/10" },
  person_enrolled: { label: "Enrolled a face", icon: ScanFace, tone: "text-warn bg-warn/10" },
  person_removed: { label: "Removed an enrolled face", icon: UserMinus, tone: "text-bad bg-bad/10" },
};

const WINDOWS = [
  { days: 1, label: "24 hours" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

/** The numbers behind an entry, read left to right rather than as raw JSON. */
function Detail({ entry }: { entry: AuditEntry }) {
  const d = entry.detail;
  const parts: string[] = [];

  if (entry.action === "session_reconciled") {
    parts.push(`AI ${d.ai_count}`, `manual ${d.manual_count}`);
    if (d.variance !== undefined) parts.push(`variance ${Number(d.variance) > 0 ? "+" : ""}${d.variance}`);
    if (d.outcome) parts.push(String(d.outcome));
  } else if (entry.action === "session_approved") {
    if (d.reason) parts.push(String(d.reason).replace(/_/g, " "));
    if (d.variance !== undefined) parts.push(`variance ${Number(d.variance) > 0 ? "+" : ""}${d.variance}`);
  } else {
    for (const [k, v] of Object.entries(d)) {
      // ids and alert keys mean nothing to a person reading this; the note shows below
      if (k.endsWith("_id") || k === "key" || k === "note") continue;
      parts.push(`${k.replace(/_/g, " ")} ${v}`);
    }
  }

  if (!parts.length) return null;
  return (
    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
      {parts.map((p) => (
        <span key={p} className="num">
          {p}
        </span>
      ))}
      {typeof d.note === "string" && d.note && (
        <span className="italic text-faint">“{d.note}”</span>
      )}
    </div>
  );
}

export default function Audit() {
  const [days, setDays] = useState(7);
  const [action, setAction] = useState<AuditAction | "">("");
  const [actor, setActor] = useState("");

  const entries = useQuery({
    queryKey: ["audit", days, action, actor],
    queryFn: () => api.audit({ days, action: action || undefined, actor: actor || undefined }),
  });
  const rows = entries.data ?? [];
  const actors = [...new Set(rows.map((r) => r.actor))].sort();

  return (
    <>
      <div className="mb-4">
        <h1 className="text-2xl font-extrabold tracking-tight text-ink">Audit log</h1>
        <p className="mt-0.5 text-sm text-muted">
          Every action that changes a count or the setup, and who took it. Append-only.
        </p>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        <Segmented
          label="Time window"
          value={String(days)}
          onChange={(v) => setDays(Number(v))}
          options={WINDOWS.map((w) => ({ value: String(w.days), label: w.label }))}
        />
        <select
          id="audit-action"
          aria-label="Action"
          className="input h-7 w-auto py-0 text-xs"
          value={action}
          onChange={(e) => setAction(e.target.value as AuditAction | "")}
        >
          <option value="">All actions</option>
          {Object.entries(ACTIONS).map(([value, a]) => (
            <option key={value} value={value}>
              {a.label}
            </option>
          ))}
        </select>
        <select
          id="audit-actor"
          aria-label="User"
          className="input h-7 w-auto py-0 text-xs"
          value={actor}
          onChange={(e) => setActor(e.target.value)}
        >
          <option value="">All users</option>
          {actors.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <span className="num ml-auto text-xs text-faint">{entries.isPending ? "…" : `${rows.length} entries`}</span>
      </div>

      <div className="card overflow-hidden">
        {entries.isPending ? (
          <SkeletonList rows={6} />
        ) : rows.length ? (
          <ul className="divide-y divide-line">
            {rows.map((e, i) => {
              const a = ACTIONS[e.action];
              const Icon = a?.icon ?? ClipboardCheck;
              return (
                <Rise as="li" index={i} key={e.id} className="flex items-start gap-3 px-4 py-2.5">
                  <span
                    className={`mt-0.5 grid h-7 w-7 flex-none place-items-center rounded-lg ${a?.tone ?? "bg-ground text-muted"}`}
                  >
                    <Icon size={14} strokeWidth={2.2} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-sm font-semibold text-ink">{e.actor}</span>
                      <span className="text-sm text-muted">{a?.label ?? e.action}</span>
                      <span className="num text-sm font-semibold text-ink">{e.subject}</span>
                    </div>
                    <Detail entry={e} />
                  </div>
                  <span className="num flex-none text-xs text-faint">{dateTime(e.at)}</span>
                </Rise>
              );
            })}
          </ul>
        ) : (
          <EmptyState
            title="Nothing recorded in this window"
            body="Widen the time window, or clear the filters."
          />
        )}
      </div>
    </>
  );
}
