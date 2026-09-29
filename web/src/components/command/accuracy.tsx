/**
 * Accuracy per verified load, against the target. Only loads with a manual count
 * appear: a load nobody checked has no accuracy, and is not drawn as one.
 */
import type { Session } from "../../api/types";
import { ACCURACY_TARGET } from "../../live/activity";

const W = 320;
const H = 150;
const L = 36;
const R = 10;
const T = 12;
const B = 22;

export function AccuracyDots({ sessions, limit = 16 }: { sessions: Session[]; limit?: number }) {
  const verified = sessions
    .filter((s) => s.accuracy !== null && s.closed_at)
    .sort((a, b) => Date.parse(a.closed_at!) - Date.parse(b.closed_at!))
    .slice(-limit);

  if (!verified.length) {
    return (
      <div className="grid h-[150px] place-items-center text-center">
        <div>
          <div className="text-sm font-semibold text-ink">No load has a manual count yet</div>
          <div className="mt-1 text-xs text-muted">Accuracy is measured once a sheet figure is entered.</div>
        </div>
      </div>
    );
  }

  const lo = Math.min(0.9, ...verified.map((s) => s.accuracy!));
  const y = (v: number) => T + (H - T - B) * (1 - (v - lo) / (1 - lo));
  const n = verified.length;
  const x = (i: number) => L + (W - L - R) * (n > 1 ? i / (n - 1) : 0.5);
  // the target is labelled at the right edge, so the scale's own labels never collide with it
  const rules = [lo, ACCURACY_TARGET, 1].filter((v, i, a) => a.indexOf(v) === i);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full overflow-visible" role="img" aria-label={`Accuracy of the last ${n} verified loads against the ${ACCURACY_TARGET * 100}% target`}>
      {rules.map((v) => (
        <g key={v}>
          <line
            x1={L}
            x2={W - R}
            y1={y(v)}
            y2={y(v)}
            className={v === ACCURACY_TARGET ? "stroke-good" : "stroke-line"}
            strokeDasharray={v === ACCURACY_TARGET ? "5 4" : "2 4"}
          />
          {v !== ACCURACY_TARGET && (
            <text x={L - 6} y={y(v) + 3} textAnchor="end" className="num fill-faint text-[10px]">
              {(v * 100).toFixed(0)}%
            </text>
          )}
        </g>
      ))}
      <text x={W - R} y={y(ACCURACY_TARGET) - 5} textAnchor="end" className="fill-good text-[10px] font-semibold">
        {(ACCURACY_TARGET * 100).toFixed(0)}% target
      </text>
      {n > 1 && (
        <polyline
          points={verified.map((s, i) => `${x(i).toFixed(1)},${y(s.accuracy!).toFixed(1)}`).join(" ")}
          className="fill-none stroke-muted/40"
        />
      )}
      {verified.map((s, i) => {
        const ok = s.accuracy! >= ACCURACY_TARGET;
        const last = i === n - 1;
        return (
          <circle
            key={s.id}
            cx={x(i)}
            cy={y(s.accuracy!)}
            r={last ? 5 : 3.5}
            strokeWidth={1.5}
            className={`${ok ? "fill-good" : "fill-warn"} stroke-surface`}
            style={last ? { filter: `drop-shadow(0 0 5px rgb(var(--${ok ? "good" : "warn"})))` } : undefined}
          >
            <title>{`${s.plate ?? "No plate"} · ${(s.accuracy! * 100).toFixed(1)}%`}</title>
          </circle>
        );
      })}
      <text x={L} y={H - 5} className="fill-faint text-[10px]">
        older
      </text>
      <text x={W - R} y={H - 5} textAnchor="end" className="fill-faint text-[10px]">
        latest
      </text>
    </svg>
  );
}
