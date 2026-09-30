import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/ui";
import { MotionRow, SkeletonRows } from "../motion";

type By = "truck" | "route" | "day";
const GROUPS: { value: By; label: string; none: string }[] = [
  { value: "truck", label: "By truck", none: "No plate read" },
  { value: "route", label: "By route", none: "No manifest route" },
  { value: "day", label: "By day", none: "—" },
];
const WINDOWS = [1, 7, 30];

/**
 * Crates out, crates back, and what is still out: per truck, route or day. From each
 * load's count of record, so a person's correction counts; open loads are shown as in
 * progress and never added in, so a figure never includes a load still being counted.
 */
export default function Balances() {
  const [by, setBy] = useState<By>("truck");
  const [days, setDays] = useState(7);
  const rows = useQuery({ queryKey: ["balances", by, days], queryFn: () => api.balances(by, days) });
  const data = rows.data ?? [];
  const totals = data.reduce(
    (t, b) => ({ out: t.out + b.dispatched, back: t.back + b.returned, open: t.open + b.in_progress }),
    { out: 0, back: 0, open: 0 },
  );
  const group = GROUPS.find((g) => g.value === by)!;
  return (
    <>
      <div className="mb-4">
        <h1 className="text-2xl font-extrabold tracking-tight text-ink">Balances</h1>
        <p className="mt-0.5 text-sm text-muted">
          Crates dispatched, returned and still outstanding. Corrected counts are used where a
          person made one; loads still at the bay are shown, not added in.
        </p>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {GROUPS.map((g) => (
          <button
            key={g.value}
            className={`btn-ghost btn-sm ${by === g.value ? "ring-1 ring-brand" : ""}`}
            aria-pressed={by === g.value}
            onClick={() => setBy(g.value)}
          >
            {g.label}
          </button>
        ))}
        <span className="mx-1 text-faint">·</span>
        {WINDOWS.map((d) => (
          <button
            key={d}
            className={`btn-ghost btn-sm ${days === d ? "ring-1 ring-brand" : ""}`}
            aria-pressed={days === d}
            onClick={() => setDays(d)}
          >
            {d === 1 ? "Today" : `${d} days`}
          </button>
        ))}
      </div>
      <div className="card overflow-hidden">
        {rows.isPending ? (
          <table className="w-full">
            <SkeletonRows rows={4} cols={6} />
          </table>
        ) : data.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">{group.label.replace("By ", "").replace(/^\w/, (c) => c.toUpperCase())}</th>
                  <th className="th text-right">Dispatched</th>
                  <th className="th text-right">Returned</th>
                  <th className="th text-right">Outstanding</th>
                  <th className="th text-right">Loads</th>
                  <th className="th">Notes</th>
                </tr>
              </thead>
              <tbody>
                {data.map((b, i) => (
                  <MotionRow key={b.key || "none"} index={i} className="border-b border-line last:border-0">
                    <td className="td num font-semibold text-ink">{b.key || <span className="text-muted">{group.none}</span>}</td>
                    <td className="td num text-right">{b.dispatched.toLocaleString()}</td>
                    <td className="td num text-right">{b.returned.toLocaleString()}</td>
                    <td className={`td num text-right font-semibold ${b.outstanding > 0 ? "text-warn" : "text-ink"}`}>
                      {b.outstanding.toLocaleString()}
                    </td>
                    <td className="td num text-right text-muted">
                      {b.loads_out} out · {b.loads_back} back
                    </td>
                    <td className="td text-xs text-muted">
                      {[
                        b.in_progress ? `${b.in_progress} still at the bay` : "",
                        b.corrected ? `${b.corrected} corrected by a person` : "",
                      ]
                        .filter(Boolean)
                        .join(" · ") || "—"}
                    </td>
                  </MotionRow>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-line bg-ground/40">
                  <td className="td font-semibold">Total</td>
                  <td className="td num text-right font-semibold">{totals.out.toLocaleString()}</td>
                  <td className="td num text-right font-semibold">{totals.back.toLocaleString()}</td>
                  <td className="td num text-right font-semibold">{(totals.out - totals.back).toLocaleString()}</td>
                  <td className="td" colSpan={2}>
                    {totals.open > 0 && <span className="text-xs text-muted">{totals.open} load(s) still being counted</span>}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        ) : (
          <EmptyState title="No loads in this window" body="Balances appear once trucks have been loaded or unloaded at the bay." />
        )}
      </div>
    </>
  );
}
