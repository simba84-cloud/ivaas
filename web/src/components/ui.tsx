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
        <h1 className="text-2xl font-extrabold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

/** A small metric. Quiet by default; `tone` colours the value when it carries a judgement. */
export function Metric({
  label,
  value,
  hint,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: LucideIcon;
  tone?: "neutral" | "accent" | "good" | "warn" | "bad";
}) {
  const color = {
    neutral: "text-ink",
    accent: "text-accent",
    good: "text-good",
    warn: "text-warn",
    bad: "text-bad",
  }[tone];
  return (
    <div className="flex items-start gap-3 py-1">
      {Icon && (
        <span className="mt-0.5 rounded-lg bg-brand-tint p-2 text-brand">
          <Icon size={16} strokeWidth={2.2} />
        </span>
      )}
      <div className="min-w-0">
        <div className="eyebrow">{label}</div>
        <div className={`num mt-0.5 text-2xl font-bold leading-none ${color}`}>{value}</div>
        {hint && <div className="mt-1 truncate text-xs text-muted">{hint}</div>}
      </div>
    </div>
  );
}

/** Kept for the report page, where the figures are the point of the page. */
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
        <div className="eyebrow">{label}</div>
        <span className={`rounded-lg p-2 ${accent ? "bg-accent-tint text-accent" : "bg-brand-tint text-brand"}`}>
          <Icon size={16} strokeWidth={2.2} />
        </span>
      </div>
      <div className={`num mt-3 text-3xl font-bold leading-none ${accent ? "text-accent" : "text-ink"}`}>{value}</div>
      {hint && <div className="mt-2 text-xs text-muted">{hint}</div>}
    </div>
  );
}

const SESSION_CHIP: Record<SessionStatus, { cls: string; dot: string }> = {
  open: { cls: "bg-accent-tint text-accent", dot: "bg-accent animate-pulse" },
  closed: { cls: "bg-ground text-muted", dot: "bg-faint" },
  reconciled: { cls: "bg-good/10 text-good", dot: "bg-good" },
  disputed: { cls: "bg-warn/10 text-warn", dot: "bg-warn" },
};

export function SessionBadge({ status }: { status: SessionStatus }) {
  const s = SESSION_CHIP[status];
  return (
    <span className={`chip capitalize ${s.cls}`}>
      <span className={`dot ${s.dot}`} />
      {status}
    </span>
  );
}

const CAMERA_DOT: Record<CameraStatus, string> = {
  online: "bg-good",
  degraded: "bg-warn",
  offline: "bg-faint",
};

export function CameraDot({ status }: { status: CameraStatus }) {
  return <span className={`dot ${CAMERA_DOT[status]}`} title={status} />;
}

/**
 * Variance as a bar, not just a number: the eye reads sign and size before it reads
 * digits. Scaled to the manual count so ±5% fills the band at the 95% target.
 */
export function VarianceBar({
  variance,
  manual,
}: {
  variance: number | null;
  manual: number | null;
}) {
  if (variance === null || !manual) return <span className="text-faint">—</span>;
  const pct = variance / manual;
  const width = Math.min(50, Math.abs(pct) * 1000); // 5% -> 50 (half the band)
  const tone = Math.abs(pct) <= 0.05 ? "bg-good" : "bg-warn";
  return (
    <span className="inline-flex items-center gap-2">
      <span className="relative h-1.5 w-20 overflow-hidden rounded-full bg-line">
        <span className="absolute left-1/2 top-0 h-full w-px bg-faint/60" />
        <span
          className={`absolute top-0 h-full rounded-full ${tone}`}
          style={pct < 0 ? { right: "50%", width: `${width}%` } : { left: "50%", width: `${width}%` }}
        />
      </span>
      <span className={`num text-xs ${Math.abs(pct) <= 0.05 ? "text-muted" : "font-semibold text-warn"}`}>
        {variance > 0 ? "+" : ""}
        {variance}
      </span>
    </span>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="px-6 py-14 text-center">
      <div className="text-sm font-semibold text-ink">{title}</div>
      <div className="mt-1 text-sm text-muted">{body}</div>
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
