/**
 * The bay as a plan: where each camera position sits, what it covers, and whether it
 * is streaming. Positions are the schematic places each role is mounted, not surveyed
 * coordinates; the platform stores no geometry beyond the bay's size.
 *
 * A role with no camera registered is drawn as a dashed outline marked "not
 * installed", so a gap in coverage shows as a gap rather than disappearing.
 */
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { Bay, Camera, CameraRole, Session } from "../../api/types";
import { roleLabel } from "../ui";

interface Mount {
  x: number;
  y: number;
  /** direction the camera faces, degrees clockwise from +x */
  dir: number;
  spread: number;
  len: number;
  /** where the camera count sits, relative to the mount */
  badge: [number, number];
}

const MOUNTS: Record<CameraRole, Mount> = {
  chokepoint: { x: 210, y: 128, dir: 90, spread: 64, len: 58, badge: [-12, -4] },
  overhead: { x: 210, y: 104, dir: 90, spread: 150, len: 30, badge: [0, -8] },
  side_high: { x: 118, y: 150, dir: 18, spread: 34, len: 78, badge: [-12, 3] },
  side_mid: { x: 118, y: 168, dir: 4, spread: 34, len: 78, badge: [-12, 3] },
  side_low: { x: 118, y: 186, dir: -10, spread: 34, len: 78, badge: [-12, 3] },
  lpr: { x: 318, y: 236, dir: 215, spread: 30, len: 96, badge: [10, 3] },
};
const ROLES = Object.keys(MOUNTS) as CameraRole[];

function wedge(x: number, y: number, m: Mount) {
  const a0 = ((m.dir - m.spread / 2) * Math.PI) / 180;
  const a1 = ((m.dir + m.spread / 2) * Math.PI) / 180;
  const p = (a: number) => `${(x + Math.cos(a) * m.len).toFixed(1)} ${(y + Math.sin(a) * m.len).toFixed(1)}`;
  return `M${x} ${y} L${p(a0)} A${m.len} ${m.len} 0 0 1 ${p(a1)} Z`;
}

const TONE = {
  selected: "fill-accent/30 stroke-accent",
  online: "fill-brand/15 stroke-brand/60",
  degraded: "fill-warn/15 stroke-warn",
  offline: "fill-bad/5 stroke-bad",
} as const;

export function BayPlan({
  bay,
  cameras,
  session,
  selectedId,
  onSelect,
}: {
  bay: Bay | undefined;
  cameras: Camera[];
  session: Session | undefined;
  selectedId: string | undefined;
  onSelect: (id: string) => void;
}) {
  const still = useReducedMotion();
  const byRole = (r: CameraRole) => cameras.filter((c) => c.role === r);

  return (
    <svg
      viewBox="0 0 420 262"
      className="mx-auto h-auto w-full max-w-[560px]"
      role="img"
      aria-label={`Plan of ${bay?.name ?? "the bay"} showing camera positions and coverage`}
    >
      {/* hall, door, apron */}
      <rect x="16" y="12" width="388" height="112" rx="8" className="fill-ground stroke-line" />
      <text x="32" y="32" className="fill-muted text-[10px] font-bold">
        Dispatch hall
      </text>
      <text x="32" y="45" className="fill-faint text-[8.5px]">
        crates stacked here, then wheeled out
      </text>
      <rect x="160" y="120" width="100" height="8" rx="2" className={session ? "fill-accent" : "fill-brand"} />
      <text x="392" y="114" textAnchor="end" className="fill-muted text-[9px] font-semibold">
        {bay ? `${bay.name} · ${bay.width_m} × ${bay.height_m} m` : "Bay"}
      </text>
      <rect x="16" y="136" width="388" height="118" rx="8" className="fill-none stroke-line" strokeDasharray="4 5" />
      <text x="392" y="248" textAnchor="end" className="fill-faint text-[8.5px]">
        Apron
      </text>

      {/* the truck, only when a session says one is at the bay */}
      <AnimatePresence>
        {session && (
          <motion.g
            key={session.id}
            initial={still ? false : { y: 90 }}
            animate={{ y: 0 }}
            exit={still ? undefined : { y: 90 }}
            transition={{ type: "spring", bounce: 0.15, duration: 0.9 }}
          >
            <rect x="182" y="140" width="56" height="84" rx="5" className="fill-surface stroke-ink/40" />
            <rect x="186" y="210" width="48" height="12" rx="2" className="fill-brand/60" />
            <text x="210" y="178" textAnchor="middle" className="num fill-ink text-[10px] font-bold">
              {session.plate ?? "no plate"}
            </text>
            <text x="210" y="192" textAnchor="middle" className="num fill-muted text-[9px]">
              {session.ai_count.toLocaleString()} crates
            </text>
          </motion.g>
        )}
      </AnimatePresence>

      {/* coverage: one field of view per position, however many cameras share it */}
      {ROLES.map((role) => {
        const m = MOUNTS[role];
        const list = byRole(role);
        if (!list.length) {
          return (
            <g key={role} aria-label={`${roleLabel(role)}: not installed`}>
              <path d={wedge(m.x, m.y, m)} className="fill-none stroke-faint/60" strokeDasharray="3 4" />
              <circle cx={m.x} cy={m.y} r="3" className="fill-none stroke-faint" />
              <title>{`${roleLabel(role)}: not installed`}</title>
            </g>
          );
        }
        const sel = list.some((c) => c.id === selectedId);
        // the position is as good as its best camera: one streaming side camera still sees the stack
        const status = list.some((c) => c.status === "online")
          ? "online"
          : list.some((c) => c.status === "degraded")
            ? "degraded"
            : "offline";
        const target = list.find((c) => c.status === "online") ?? list.find((c) => c.status === "degraded") ?? list[0];
        const up = list.filter((c) => c.status === "online").length;
        const name = `${roleLabel(role)}: ${up} of ${list.length} streaming`;
        return (
          <g
            key={role}
            role="button"
            tabIndex={0}
            aria-label={`Show ${roleLabel(role)}, ${up} of ${list.length} streaming`}
            aria-pressed={sel}
            className="cursor-pointer outline-none"
            onClick={() => onSelect(target.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(target.id);
              }
            }}
          >
            <path
              d={wedge(m.x, m.y, m)}
              className={`${sel ? TONE.selected : TONE[status]} transition-colors`}
              strokeWidth={sel ? 1.5 : 1}
              strokeDasharray={status === "offline" && !sel ? "3 3" : undefined}
            />
            <circle
              cx={m.x}
              cy={m.y}
              r={sel ? 4.5 : 3.5}
              className={sel ? "fill-accent" : status === "online" ? "fill-ink" : status === "degraded" ? "fill-warn" : "fill-bad"}
            />
            {list.length > 1 && (
              <text x={m.x + m.badge[0]} y={m.y + m.badge[1]} textAnchor="middle" className="num fill-muted text-[8px] font-bold">
                ×{list.length}
              </text>
            )}
            <title>{name}</title>
          </g>
        );
      })}
    </svg>
  );
}

export function BayPlanLegend() {
  const swatch = "inline-block h-2.5 w-2.5 rounded-sm";
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted">
      <span className="inline-flex items-center gap-1.5">
        <i className={`${swatch} bg-accent/60`} /> Shown in the main view
      </span>
      <span className="inline-flex items-center gap-1.5">
        <i className={`${swatch} bg-brand/40`} /> Streaming
      </span>
      <span className="inline-flex items-center gap-1.5">
        <i className={`${swatch} border border-dashed border-bad`} /> No signal
      </span>
      <span className="inline-flex items-center gap-1.5">
        <i className={`${swatch} border border-dashed border-faint`} /> Not installed
      </span>
    </div>
  );
}
