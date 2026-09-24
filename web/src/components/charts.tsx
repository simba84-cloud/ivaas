/**
 * Charts drawn in the platform's own colours.
 *
 * Recharts writes its colours into SVG presentation attributes, which do not
 * resolve `var(--token)`. So we read the tokens off the document once and again
 * whenever the theme changes, and hand Recharts real rgb() strings.
 */
import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DayPoint } from "../api/types";

const TOKENS = ["brand", "accent", "line", "muted", "faint", "good", "warn", "surface"] as const;
type Token = (typeof TOKENS)[number];
type Palette = Record<Token, string>;

function read(): Palette {
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(
    TOKENS.map((t) => {
      const value = style.getPropertyValue(`--${t}`).trim();
      return [t, value ? `rgb(${value})` : "#94a3b8"];
    }),
  ) as Palette;
}

export function useChartTheme(): Palette {
  const [palette, setPalette] = useState<Palette>(read);
  useEffect(() => {
    const update = () => setPalette(read());
    update(); // the first paint may precede the stylesheet
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener?.("change", update);
    return () => {
      observer.disconnect();
      media.removeEventListener?.("change", update);
    };
  }, []);
  return palette;
}

/** The shape of a metric over the window, sized to sit inside a KPI card. */
export function Sparkline({
  series,
  color,
  height = 44,
}: {
  series: number[];
  color: string;
  height?: number;
}) {
  if (series.length < 2) return <div style={{ height }} />;
  const data = series.map((v, i) => ({ i, v }));
  const id = `spark-${color.replace(/\D/g, "")}`;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="v"
          stroke={color}
          strokeWidth={2}
          fill={`url(#${id})`}
          isAnimationActive={false}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

const dayLabel = (iso: string) =>
  new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });

/** Crates moved per day, with verified accuracy tracked against the 95% target. */
export function ThroughputChart({ daily, height = 260 }: { daily: DayPoint[]; height?: number }) {
  const c = useChartTheme();
  const data = daily.map((d) => ({
    day: dayLabel(d.day),
    crates: d.crates,
    accuracy: d.accuracy === null ? null : Math.round(d.accuracy * 1000) / 10,
  }));
  const anyAccuracy = daily.some((d) => d.accuracy !== null);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid stroke={c.line} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="day"
          tick={{ fill: c.muted, fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: c.line }}
          interval="preserveStartEnd"
          minTickGap={16}
        />
        <YAxis
          yAxisId="crates"
          tick={{ fill: c.muted, fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={48}
        />
        {anyAccuracy && (
          <YAxis
            yAxisId="accuracy"
            orientation="right"
            domain={[80, 100]}
            unit="%"
            tick={{ fill: c.muted, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={44}
          />
        )}
        <Tooltip
          cursor={{ fill: c.line, opacity: 0.35 }}
          contentStyle={{
            background: c.surface,
            border: `1px solid ${c.line}`,
            borderRadius: 10,
            fontSize: 12,
          }}
          formatter={(value, name) =>
            name === "accuracy"
              ? [`${value}%`, "Accuracy"]
              : [String(value ?? "—"), "Crates"]
          }
        />
        <Bar
          yAxisId="crates"
          dataKey="crates"
          fill={c.brand}
          radius={[4, 4, 0, 0]}
          maxBarSize={28}
          isAnimationActive={false}
        />
        {anyAccuracy && (
          <>
            <ReferenceLine
              yAxisId="accuracy"
              y={95}
              stroke={c.good}
              strokeDasharray="4 4"
              label={{ value: "95% target", fill: c.muted, fontSize: 10, position: "insideTopRight" }}
            />
            <Line
              yAxisId="accuracy"
              type="monotone"
              dataKey="accuracy"
              stroke={c.accent}
              strokeWidth={2}
              dot={{ r: 3, fill: c.accent, strokeWidth: 0 }}
              connectNulls
              isAnimationActive={false}
            />
          </>
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
