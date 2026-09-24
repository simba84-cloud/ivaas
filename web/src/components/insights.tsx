/**
 * The operations feed: what the platform noticed in the data and what it means
 * for the person reading it. Every item comes from the API's overview endpoint,
 * computed from stored sessions and camera state — none of it is generated
 * text, so it can be trusted enough to act on.
 */
import { motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Info, OctagonAlert, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import type { Insight, Severity } from "../api/types";

const STYLE: Record<
  Severity,
  { icon: typeof Info; text: string; tint: string; rail: string; label: string }
> = {
  critical: {
    icon: OctagonAlert,
    text: "text-bad",
    tint: "bg-bad/10",
    rail: "bg-bad",
    label: "Critical",
  },
  warn: {
    icon: AlertTriangle,
    text: "text-warn",
    tint: "bg-warn/10",
    rail: "bg-warn",
    label: "Attention",
  },
  info: { icon: Info, text: "text-brand", tint: "bg-brand-tint", rail: "bg-brand", label: "Note" },
  good: {
    icon: CheckCircle2,
    text: "text-good",
    tint: "bg-good/10",
    rail: "bg-good",
    label: "Healthy",
  },
};

export function InsightFeed({ insights }: { insights: Insight[] }) {
  const still = useReducedMotion();
  const needsAction = insights.filter((i) => i.severity === "critical" || i.severity === "warn");

  return (
    <section className="card flex h-full flex-col overflow-hidden">
      <header className="panel-head">
        <div className="flex items-center gap-2">
          <Sparkles size={15} className="text-accent" />
          <h2 className="panel-title">Operational insights</h2>
        </div>
        {needsAction.length > 0 && (
          <span className="chip num bg-warn/10 text-warn">{needsAction.length} to action</span>
        )}
      </header>

      <div className="flex-1 divide-y divide-line overflow-y-auto">
        {insights.length ? (
          insights.map((insight, i) => {
            const s = STYLE[insight.severity];
            const Icon = s.icon;
            return (
              <motion.article
                key={insight.key}
                initial={still ? false : { opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.3, delay: i * 0.05 }}
                className="relative flex gap-3 px-5 py-3.5"
              >
                <span className={`absolute inset-y-0 left-0 w-0.5 ${s.rail}`} aria-hidden />
                <span className={`mt-0.5 h-7 w-7 flex-none rounded-lg ${s.tint} ${s.text} grid place-items-center`}>
                  <Icon size={15} strokeWidth={2.2} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="text-sm font-semibold leading-snug text-ink">{insight.title}</h3>
                    {insight.metric && (
                      <span className={`num flex-none text-sm font-bold ${s.text}`}>
                        {insight.metric}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-muted">{insight.detail}</p>
                  <span className="sr-only">Severity: {s.label}</span>
                </div>
              </motion.article>
            );
          })
        ) : (
          <div className="px-5 py-10 text-center">
            <CheckCircle2 size={22} className="mx-auto text-good" />
            <p className="mt-2 text-sm font-semibold text-ink">Nothing needs attention</p>
            <p className="mt-1 text-xs text-muted">
              Cameras are streaming and no loads are outstanding.
            </p>
          </div>
        )}
      </div>

      <footer className="border-t border-line px-5 py-3">
        <Link to="/assistant" className="text-xs font-semibold text-brand hover:underline">
          Ask the assistant about this data →
        </Link>
      </footer>
    </section>
  );
}
