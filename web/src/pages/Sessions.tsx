import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Session } from "../api/types";
import { type Me, hasRole } from "../auth/session";
import { EmptyState, PageHeader, SessionBadge, dateTime, pct } from "../components/ui";

function ReconcileCell({ session, canOperate }: { session: Session; canOperate: boolean }) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const reconcile = useMutation({
    mutationFn: (n: number) => api.reconcile(session.id, n),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
    },
  });

  if (session.status === "open") return <span className="text-xs text-slate-400">In progress</span>;
  if (session.manual_count !== null)
    return <span className="tabular-nums">{session.manual_count.toLocaleString()}</span>;
  if (!canOperate) return <span className="text-xs text-slate-400">Awaiting verification</span>;

  const n = Number(value);
  const valid = value !== "" && Number.isInteger(n) && n >= 0;
  return (
    <form
      className="flex items-center justify-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) reconcile.mutate(n);
      }}
    >
      <input
        inputMode="numeric"
        value={value}
        onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))}
        placeholder="Manual count"
        aria-label="Manual count"
        className="w-28 rounded-lg border border-slate-300 px-2.5 py-1.5 text-right text-sm tabular-nums outline-none focus:border-brand-navy focus:ring-2 focus:ring-brand-navy/20"
      />
      <button className="btn-primary px-3 py-1.5" disabled={!valid || reconcile.isPending}>
        Verify
      </button>
      {reconcile.isError && (
        <span className="text-xs text-red-600">{(reconcile.error as Error).message}</span>
      )}
    </form>
  );
}

export default function Sessions({ me }: { me: Me | undefined }) {
  const canOperate = hasRole(me, "operator");
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });

  return (
    <>
      <PageHeader
        title="Reconciliation"
        subtitle="Compare AI crate counts against manual verification, per truck"
      />
      <div className="card overflow-hidden">
        {sessions.data?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-brand-navy-tint/60">
                <tr>
                  <th className="th">Plate</th>
                  <th className="th">Direction</th>
                  <th className="th">Opened</th>
                  <th className="th text-right">AI count</th>
                  <th className="th text-right">Manual count</th>
                  <th className="th text-right">Variance</th>
                  <th className="th text-right">Accuracy</th>
                  <th className="th">Status</th>
                </tr>
              </thead>
              <tbody>
                {sessions.data.map((s) => (
                  <tr key={s.id} className="border-t border-slate-100">
                    <td className="td font-mono font-semibold text-brand-navy">{s.plate ?? "—"}</td>
                    <td className="td capitalize">{s.direction}</td>
                    <td className="td text-slate-500">{dateTime(s.opened_at)}</td>
                    <td className="td text-right font-semibold tabular-nums">
                      {s.ai_count.toLocaleString()}
                    </td>
                    <td className="td text-right">
                      <ReconcileCell session={s} canOperate={canOperate} />
                    </td>
                    <td
                      className={`td text-right tabular-nums ${
                        s.variance ? "font-semibold text-amber-700" : ""
                      }`}
                    >
                      {s.variance === null ? "—" : s.variance > 0 ? `+${s.variance}` : s.variance}
                    </td>
                    <td className="td text-right tabular-nums">{pct(s.accuracy)}</td>
                    <td className="td">
                      <SessionBadge status={s.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="Nothing to reconcile yet"
            body="Closed truck sessions will be listed here for manual verification."
          />
        )}
      </div>
    </>
  );
}
