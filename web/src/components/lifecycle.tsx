/**
 * Two strips that sit under the KPI row.
 *
 * The lifecycle tiles are not four unrelated counts: they are the stages a load
 * passes through, left to right, so a pile-up at one stage is visible as a shape
 * rather than as a number you have to interpret.
 */
import { motion, useReducedMotion } from "framer-motion";
import { CheckCircle2, CircleDashed, XCircle } from "lucide-react";
import { Link } from "react-router-dom";

type Stage = {
  key: string;
  label: string;
  value: number;
  hint: string;
  fill: string;
  to: string;
};

export function LoadLifecycle({
  open,
  awaiting,
  disputed,
  approved,
  reconciled,
}: {
  open: number;
  awaiting: number;
  disputed: number;
  approved: number;
  reconciled: number;
}) {
  const still = useReducedMotion();
  const total = Math.max(1, open + awaiting + disputed + approved + reconciled);

  const stages: Stage[] = [
    {
      key: "open",
      label: "At the bay",
      value: open,
      hint: "Counting now",
      fill: "linear-gradient(135deg, #16234f 0%, #273c87 100%)",
      to: "/",
    },
    {
      key: "awaiting",
      label: "Awaiting count",
      value: awaiting,
      hint: "Needs a manual check",
      fill: "linear-gradient(135deg, #3b2a74 0%, #5b2a8c 100%)",
      to: "/sessions",
    },
    {
      key: "disputed",
      label: "Disputed",
      value: disputed,
      hint: "Variance beyond tolerance",
      fill: "linear-gradient(135deg, #8f1a6b 0%, #c8187d 100%)",
      to: "/sessions",
    },
    {
      key: "approved",
      label: "Approved",
      value: approved,
      hint: "Discrepancy signed off",
      fill: "linear-gradient(135deg, #273c87 0%, #4462c9 100%)",
      to: "/sessions",
    },
    {
      key: "reconciled",
      label: "Reconciled",
      value: reconciled,
      hint: "Agreed and closed",
      fill: "linear-gradient(135deg, #14614a 0%, #1f9d63 100%)",
      to: "/sessions",
    },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      {stages.map((s, i) => (
        <motion.div
          key={s.key}
          initial={still ? false : { y: 10 }}
          animate={{ y: 0 }}
          transition={{ duration: 0.3, delay: 0.25 + i * 0.05 }}
        >
          <Link
            to={s.to}
            style={{ background: s.fill }}
            className="relative block overflow-hidden rounded-xl px-4 py-3.5 text-white transition hover:brightness-110"
          >
            <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-white/70">
              {s.label}
            </div>
            <div className="num mt-1.5 text-3xl font-bold leading-none">{s.value}</div>
            {/* the rule carries this stage's share of all loads in the window */}
            <div className="mt-3 h-[3px] w-full overflow-hidden rounded-full bg-white/20">
              <motion.div
                className="h-full rounded-full bg-white/80"
                initial={still ? false : { width: 0 }}
                animate={{ width: `${(s.value / total) * 100}%` }}
                transition={{ duration: 0.6, delay: 0.3 + i * 0.05, ease: "easeOut" }}
              />
            </div>
            <div className="mt-2 text-[11px] text-white/70">{s.hint}</div>
          </Link>
        </motion.div>
      ))}
    </div>
  );
}

type Service = { name: string; ok: boolean | undefined; detail: string };

/** Platform health, from signals the portal already has rather than a status page. */
export function HealthStrip({ services }: { services: Service[] }) {
  return (
    <div className="card flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
      <span className="text-sm font-bold text-ink">Platform health</span>
      {services.map((s) => {
        const Icon = s.ok === undefined ? CircleDashed : s.ok ? CheckCircle2 : XCircle;
        const tone =
          s.ok === undefined ? "text-faint" : s.ok ? "text-good" : "text-bad";
        return (
          <span key={s.name} className="flex items-center gap-1.5 text-sm" title={s.detail}>
            <Icon size={14} className={tone} strokeWidth={2.4} />
            <span className="text-ink">{s.name}</span>
            <span className="text-xs text-muted">{s.detail}</span>
          </span>
        );
      })}
    </div>
  );
}
