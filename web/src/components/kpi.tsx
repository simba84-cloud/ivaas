/**
 * Executive KPI cards: the figure, how it moved, and the shape behind it.
 *
 * A delta is only shown when the API could compute one. "No comparable prior
 * period" is a real state on a young deployment and is said, not hidden behind
 * a 0% that would read as "flat".
 */
import { motion, useReducedMotion } from "framer-motion";
import { ArrowDownRight, ArrowRight, ArrowUpRight, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Sparkline, useChartTheme } from "./charts";

export type Tone = "brand" | "accent" | "good" | "warn" | "bad";

const TONE: Record<Tone, { text: string; tint: string; token: "brand" | "accent" | "good" | "warn" }> = {
  brand: { text: "text-brand", tint: "bg-brand-tint", token: "brand" },
  accent: { text: "text-accent", tint: "bg-accent-tint", token: "accent" },
  good: { text: "text-good", tint: "bg-good/10", token: "good" },
  warn: { text: "text-warn", tint: "bg-warn/10", token: "warn" },
  bad: { text: "text-bad", tint: "bg-bad/10", token: "warn" },
};

/**
 * @param delta fraction (0.12 = +12%) for "pct", or points (0.02 = +2.0 pts) for "points"
 * @param higherIsBetter which direction earns the green
 */
export function Delta({
  delta,
  unit = "pct",
  higherIsBetter = true,
}: {
  delta: number | null;
  unit?: "pct" | "points";
  higherIsBetter?: boolean;
}) {
  if (delta === null)
    return <span className="text-xs text-faint">No prior period to compare</span>;

  const flat = Math.abs(delta) < (unit === "points" ? 0.001 : 0.005);
  const good = flat ? null : delta > 0 === higherIsBetter;
  const Icon = flat ? ArrowRight : delta > 0 ? ArrowUpRight : ArrowDownRight;
  const colour = flat ? "text-muted bg-ground" : good ? "text-good bg-good/10" : "text-bad bg-bad/10";
  const shown =
    unit === "points"
      ? `${delta > 0 ? "+" : ""}${(delta * 100).toFixed(1)} pts`
      : `${delta > 0 ? "+" : ""}${(delta * 100).toFixed(0)}%`;

  return (
    <span className={`chip num ${colour}`} title="Against the previous period of equal length">
      <Icon size={12} strokeWidth={2.6} />
      {flat ? "No change" : shown}
    </span>
  );
}

export function KpiCard({
  label,
  value,
  unit,
  delta,
  deltaUnit = "pct",
  higherIsBetter = true,
  series,
  tone = "brand",
  icon: Icon,
  footer,
  index = 0,
}: {
  label: string;
  value: string;
  unit?: string;
  delta?: number | null;
  deltaUnit?: "pct" | "points";
  higherIsBetter?: boolean;
  series?: number[];
  tone?: Tone;
  icon: LucideIcon;
  footer?: ReactNode;
  index?: number;
}) {
  const palette = useChartTheme();
  const still = useReducedMotion();
  const t = TONE[tone];

  return (
    <motion.article
      initial={still ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.06, ease: [0.2, 0.7, 0.2, 1] }}
      className="card-lift group relative flex h-full flex-col overflow-hidden p-5 transition duration-200 hover:shadow-lift"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="eyebrow">{label}</div>
        <span className={`rounded-lg p-2 ${t.tint} ${t.text}`}>
          <Icon size={16} strokeWidth={2.2} />
        </span>
      </div>

      <div className="mt-3 flex items-baseline gap-1.5">
        <span className={`num text-4xl font-bold leading-none tracking-tight ${t.text}`}>
          {value}
        </span>
        {unit && <span className="text-sm font-semibold text-faint">{unit}</span>}
      </div>

      <div className="mt-3 flex items-center gap-2">
        {delta !== undefined && (
          <Delta delta={delta} unit={deltaUnit} higherIsBetter={higherIsBetter} />
        )}
      </div>

      {footer && <div className="mt-3 text-xs text-muted">{footer}</div>}

      {/* the shape of the metric, bled to the card's bottom edge */}
      {series && series.length > 1 && (
        <div className="-mx-5 -mb-5 mt-auto pt-4 opacity-90">
          <Sparkline series={series} color={palette[t.token]} />
        </div>
      )}
    </motion.article>
  );
}
