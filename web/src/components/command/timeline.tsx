/**
 * The bay's recent history on a rolling time axis.
 *
 * Marks are placed once against a fixed origin and the whole strip slides left by
 * transform, so the only thing that changes each second is one compositor-only
 * property; nothing is re-laid out as time passes.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { Session, SessionStatus } from "../../api/types";
import type { Mark } from "./activity";

export const WINDOWS = {
  "1h": { ms: 3_600_000, tick: 600_000 },
  "4h": { ms: 14_400_000, tick: 1_800_000 },
  "12h": { ms: 43_200_000, tick: 7_200_000 },
} as const;
export type WindowKey = keyof typeof WINDOWS;

const LANE = 30; // px per lane
const NOW_INSET = 44; // room right of "now" for labels arriving
const BAR: Record<SessionStatus, string> = {
  open: "border-accent bg-accent/30 shadow-[0_0_12px_rgb(var(--accent)/0.6)]",
  closed: "border-faint bg-faint/20",
  reconciled: "border-good bg-good/25",
  disputed: "border-warn bg-warn/25",
  approved: "border-brand bg-brand/25",
};
const MARK: Record<Mark["kind"], string> = {
  opened: "bg-accent",
  plate: "bg-accent rotate-45",
  crates: "bg-brand",
  closed: "bg-good",
};

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export function Timeline({
  sessions,
  marks,
  windowKey,
}: {
  sessions: Session[];
  marks: Mark[];
  windowKey: WindowKey;
}) {
  const [viewRef, width] = useWidth<HTMLDivElement>();
  const origin = useRef(Date.now()).current;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const { ms, tick } = WINDOWS[windowKey];
  const span = Math.max(1, width - NOW_INSET);
  const pxPerMs = span / ms;
  const x = (t: number) => (t - origin) * pxPerMs;
  const shift = span - x(now);
  // placement only changes when the data, scale or the minute does
  const minute = Math.floor(now / 60_000);

  const ticks = useMemo(() => {
    const out: number[] = [];
    const end = minute * 60_000 + ms;
    for (let t = Math.ceil((minute * 60_000 - ms * 1.5) / tick) * tick; t <= end; t += tick) out.push(t);
    return out;
  }, [minute, ms, tick]);

  const bars = useMemo(() => {
    const from = minute * 60_000 - ms * 1.5;
    return sessions
      .filter((s) => (s.closed_at ? Date.parse(s.closed_at) : Infinity) >= from)
      .map((s) => {
        const start = Date.parse(s.opened_at);
        const end = s.closed_at ? Date.parse(s.closed_at) : minute * 60_000 + 60_000;
        return { s, left: x(start), width: Math.max(4, x(end) - x(start)) };
      });
  }, [sessions, minute, ms, pxPerMs]); // x() reads only origin and pxPerMs

  const label = (t: number) =>
    new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  return (
    <div className="grid grid-cols-[76px_minmax(0,1fr)]">
      <div className="border-r border-line pt-2" aria-hidden>
        {["Loads", "Live", "Checks"].map((l) => (
          <div key={l} style={{ height: LANE }} className="flex items-center pl-4 text-[10px] font-bold uppercase tracking-[0.12em] text-faint">
            {l}
          </div>
        ))}
      </div>

      <div ref={viewRef} className="relative h-[124px] overflow-hidden pt-2">
        {/* lane rules */}
        <div
          aria-hidden
          className="absolute inset-x-0 top-2"
          style={{ height: LANE * 3, background: `repeating-linear-gradient(180deg, transparent 0 ${LANE - 1}px, rgb(var(--line)) ${LANE - 1}px ${LANE}px)` }}
        />

        {width > 0 && (
          <div
            className="absolute left-0 top-2 h-full w-px transition-transform duration-1000 ease-linear will-change-transform"
            style={{ transform: `translate3d(${shift.toFixed(1)}px,0,0)` }}
          >
            {ticks.map((t) => (
              <div key={t} className="absolute top-0" style={{ left: x(t) }}>
                <div className="h-[90px] border-l border-dashed border-line" />
                <div className="num -translate-x-1/2 pt-1 text-[10px] text-faint">{label(t)}</div>
              </div>
            ))}

            {bars.map(({ s, left, width: w }) => (
              <div
                key={s.id}
                title={`${s.plate ?? "No plate"} · ${s.ai_count} crates · ${s.status}`}
                className={`absolute h-3 rounded-full border ${BAR[s.status]}`}
                style={{ left, width: w, top: LANE / 2 - 6 }}
              >
                <span className="num absolute -top-3.5 left-1 whitespace-nowrap text-[10px] font-semibold text-muted">
                  {s.plate ?? "no plate"} · {s.ai_count.toLocaleString()}
                </span>
              </div>
            ))}

            {marks.map((m) => (
              <div
                key={m.key}
                className="absolute flex -translate-x-1/2 flex-col items-center"
                style={{ left: x(m.at), top: LANE + 7 }}
                title={`${m.kind} · ${label(m.at)}`}
              >
                <span className={`block h-2 w-2 rounded-[2px] ${MARK[m.kind]}`} />
                <span className="num mt-0.5 whitespace-nowrap text-[10px] font-semibold text-muted">{m.label}</span>
              </div>
            ))}

            {sessions
              .filter((s) => s.closed_at && s.status !== "closed" && s.status !== "open")
              .map((s) => (
                <span
                  key={`chk-${s.id}`}
                  title={`${s.plate ?? "No plate"} · ${s.status}`}
                  className={`absolute block h-2.5 w-2.5 -translate-x-1/2 rotate-45 ${
                    s.status === "reconciled" ? "bg-good" : s.status === "disputed" ? "bg-warn" : "bg-brand"
                  }`}
                  style={{ left: x(Date.parse(s.closed_at!)), top: LANE * 2 + LANE / 2 - 5 }}
                />
              ))}
          </div>
        )}

        {!marks.length && (
          <div
            className="pointer-events-none absolute text-[11px] text-faint"
            style={{ top: LANE + 16, right: NOW_INSET + 10 }}
          >
            Plate reads and counts appear here as they happen
          </div>
        )}

        {/* now */}
        <div
          aria-hidden
          className="absolute bottom-0 top-0 w-px bg-accent shadow-[0_0_10px_rgb(var(--accent))]"
          style={{ left: span }}
        >
          <span className="num absolute right-1.5 top-1 text-[9.5px] font-bold tracking-[0.1em] text-accent">NOW</span>
        </div>
        <div aria-hidden className="pointer-events-none absolute inset-y-0 left-0 w-12 bg-gradient-to-r from-surface to-transparent" />
      </div>
    </div>
  );
}
