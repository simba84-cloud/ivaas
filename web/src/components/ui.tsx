import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { CameraStatus, SessionStatus } from "../api/types";

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-brand-navy">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  accent = false,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: LucideIcon;
  accent?: boolean;
}) {
  return (
    <div className="card p-5">
      <div className="flex items-start justify-between">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
        <div
          className={`rounded-lg p-2 ${
            accent ? "bg-brand-magenta-tint text-brand-magenta" : "bg-brand-navy-tint text-brand-navy"
          }`}
        >
          <Icon size={18} />
        </div>
      </div>
      <div className="mt-3 text-3xl font-bold tabular-nums text-brand-navy">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

const SESSION_BADGE: Record<SessionStatus, string> = {
  open: "bg-brand-magenta-tint text-brand-magenta",
  closed: "bg-slate-100 text-slate-600",
  reconciled: "bg-emerald-50 text-emerald-700",
  disputed: "bg-amber-50 text-amber-700",
};

export function SessionBadge({ status }: { status: SessionStatus }) {
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ${SESSION_BADGE[status]}`}
    >
      {status}
    </span>
  );
}

const CAMERA_DOT: Record<CameraStatus, string> = {
  online: "bg-emerald-500",
  degraded: "bg-amber-500",
  offline: "bg-slate-300",
};

export function CameraDot({ status }: { status: CameraStatus }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${CAMERA_DOT[status]}`} />;
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="px-6 py-14 text-center">
      <div className="text-sm font-semibold text-brand-navy">{title}</div>
      <div className="mt-1 text-sm text-slate-500">{body}</div>
    </div>
  );
}

export const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);
export const time = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
export const roleLabel = (role: string) =>
  role === "lpr" ? "LPR" : role.replace("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
