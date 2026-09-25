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

const TONE: Record<
  Tone,
  { text: string; token: "brand" | "accent" | "good" | "warn"; corner: string }
> = {
  brand: { text: "text-brand", token: "brand", corner: "from-[#273c87] to-[#1d2d66]" },
  accent: { text: "text-accent", token: "accent", corner: "from-[#c8187d] to-[#8f1a6b]" },
  good: { text: "text-good", token: "good", corner: "from-[#1f9d63] to-[#147a4c]" },
  warn: { text: "text-warn", token: "warn", corner: "from-[#d98b13] to-[#a4660d]" },
  bad: { text: "text-bad", token: "warn", corner: "from-[#d13b3b] to-[#992b2b]" },
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
  if (delta === null) return <span className="text-xs text-faint">No prior period to compare</span>;

  const flat = Math.abs(delta) < (unit === "points" ? 0.001 : 0.005);
  const good = flat ? null : delta > 0 === higherIsBetter;
  const Icon = flat ? ArrowRight : delta > 0 ? ArrowUpRight : ArrowDownRight;
  const colour = flat
    ? "text-muted bg-ground"
    : good
      ? "text-good bg-good/10"
      : "text-bad bg-bad/10";
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
  series?: (number | null)[];
  tone?: Tone;
  icon: LucideIcon;
  footer?: ReactNode;
  index?: number;
}) {
  // Entrances move, they do not fade in: content parked at opacity 0 is invisible
  // if the animation never runs, and a sign-in card that does that is unusable.
  const palette = useChartTheme();
  const still = useReducedMotion();
  const t = TONE[tone];

  return (
    <motion.article
      initial={still ? false : { y: 12 }}
      animate={{ y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.06, ease: [0.2, 0.7, 0.2, 1] }}
      className="card-lift group relative flex h-full flex-col overflow-hidden p-5 transition duration-200 hover:shadow-lift"
    >
      {/* the icon sits on a corner cut at the angle of a crate lip */}
      <div
        aria-hidden
        className={`absolute -right-px -top-px h-[72px] w-[104px] bg-gradient-to-br ${t.corner}`}
        style={{ clipPath: "polygon(22% 0, 100% 0, 100% 100%, 0 100%)" }}
      />
      <span className="absolute right-4 top-3.5 text-white/90">
        <Icon size={17} strokeWidth={2.2} />
      </span>

      <div className="eyebrow max-w-[60%]">{label}</div>

      <div className="mt-3 flex items-baseline gap-1.5">
        <span className={`num text-[2.1rem] font-bold leading-none tracking-tight ${t.text}`}>
          {value}
        </span>
        {unit && <span className="text-sm font-semibold text-faint">{unit}</span>}
      </div>

      {delta !== undefined && (
        <div className="mt-3">
          <Delta delta={delta} unit={deltaUnit} higherIsBetter={higherIsBetter} />
        </div>
      )}

      {footer && <div className="mt-3 text-xs text-muted">{footer}</div>}

      {series && series.filter((v) => v !== null).length > 1 && (
        <div className="-mx-5 -mb-5 mt-auto pt-4 opacity-90">
          <Sparkline series={series} color={palette[t.token]} />
        </div>
      )}
    </motion.article>
  );
}
