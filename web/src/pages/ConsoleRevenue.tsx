import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { PageHeader } from "../components/ui";

const label = (period: string) =>
  new Date(`${period}-01T00:00:00Z`).toLocaleDateString([], { month: "short", year: "numeric", timeZone: "UTC" });

/**
 * What Cassava has issued, month by month: to its own customers and to partners
 * wholesale. A month with nothing issued shows no figure, not zero: it may simply not
 * be invoiced yet. Drafts are not revenue.
 */
export default function ConsoleRevenue() {
  const [months, setMonths] = useState(6);
  const r = useQuery({ queryKey: ["console", "revenue", months], queryFn: () => api.revenue(months) });
  const d = r.data;
  return (
    <>
      <PageHeader
        title="Revenue"
        subtitle="Invoices Cassava has issued, by the month they bill for. Before tax unless it says total."
        actions={
          <div>
            <label htmlFor="revenue-months" className="label">
              Months
            </label>
            <select
              id="revenue-months"
              className="input h-9 w-28"
              value={months}
              onChange={(e) => setMonths(Number(e.target.value))}
            >
              {[3, 6, 12, 24].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
        }
      />
      {d?.placeholder && (
        <p role="note" className="mb-4 flex items-center gap-2 rounded-lg bg-warn/10 px-3 py-2 text-sm text-warn">
          <AlertTriangle size={15} /> Issued at placeholder prices: these are not real revenue.
        </p>
      )}
      {d && d.overdue > 0 && (
        <p role="alert" className="mb-4 rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad">
          {d.overdue} invoice{d.overdue === 1 ? " is" : "s are"} overdue: {d.overdue_amount} {d.currency} unpaid
          past the due date.
        </p>
      )}
      <section className="card overflow-x-auto">
        {r.isPending ? (
          <p className="px-4 py-6 text-sm text-muted">Loading…</p>
        ) : r.error ? (
          <p className="px-4 py-6 text-sm text-bad">{(r.error as Error).message}</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line">
                <th className="th">Month</th>
                <th className="th text-right">Direct</th>
                <th className="th text-right">Partners</th>
                <th className="th text-right">Before tax</th>
                <th className="th text-right">Total</th>
                <th className="th text-right">Paid</th>
                <th className="th text-right">Outstanding</th>
              </tr>
            </thead>
            <tbody>
              {d!.months.map((m) => (
                <tr key={m.period} className="border-b border-line last:border-0">
                  <td className="td text-ink">{label(m.period)}</td>
                  {m.invoices === 0 ? (
                    <td className="td text-right text-muted" colSpan={6}>
                      Nothing issued
                    </td>
                  ) : (
                    <>
                      <td className="td num text-right">{m.direct}</td>
                      <td className="td num text-right">{m.wholesale}</td>
                      <td className="td num text-right font-semibold">{m.subtotal}</td>
                      <td className="td num text-right">{m.total}</td>
                      <td className="td num text-right text-good">{m.paid}</td>
                      <td className={`td num text-right ${Number(m.outstanding) > 0 ? "text-warn" : "text-muted"}`}>
                        {m.outstanding}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      {d && Object.keys(d.by_payer).length > 0 && (
        <section className="card mt-4 p-4">
          <h2 className="mb-2 text-sm font-bold text-ink">By who was invoiced, over these months, before tax</h2>
          <ul className="divide-y divide-line text-sm">
            {Object.entries(d.by_payer).map(([payer, amount]) => (
              <li key={payer} className="flex justify-between py-1.5">
                <span className="text-ink">{payer}</span>
                <span className="num">
                  {amount} {d.currency}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
