import { useQuery } from "@tanstack/react-query";
import {
  Boxes,
  Camera,
  CameraOff,
  CheckCircle2,
  ClipboardCheck,
  FileVideo,
  LogIn,
  MapPin,
  SlidersHorizontal,
  Truck,
  Warehouse,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { AuditAction, AuditEntry } from "../api/types";
import { EmptyState, dateTime } from "../components/ui";

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
  setting_changed: {
    label: "Changed a rule",
    icon: SlidersHorizontal,
    tone: "text-warn bg-warn/10",
  },
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
      if (k.endsWith("_id")) continue; // ids mean nothing to a person reading this
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
        <div className="segment" role="group" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button
              key={w.days}
              onClick={() => setDays(w.days)}
              aria-pressed={days === w.days}
              className={`segment-item ${days === w.days ? "segment-item-on" : ""}`}
            >
              {w.label}
            </button>
          ))}
        </div>
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
        <span className="num ml-auto text-xs text-faint">{rows.length} entries</span>
      </div>

      <div className="card overflow-hidden">
        {rows.length ? (
          <ul className="divide-y divide-line">
            {rows.map((e) => {
              const a = ACTIONS[e.action];
              const Icon = a?.icon ?? ClipboardCheck;
              return (
                <li key={e.id} className="flex items-start gap-3 px-4 py-2.5">
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
                </li>
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
