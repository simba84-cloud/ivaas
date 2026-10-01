import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { BreakGlassGrant, GrantState } from "../api/types";
import { switchBreakGlass } from "../auth/session";
import { EmptyState, PageHeader, dateTime } from "../components/ui";

export const STATE_TONE: Record<GrantState, string> = {
  pending: "bg-warn/10 text-warn",
  active: "bg-good/10 text-good",
  denied: "bg-bad/10 text-bad",
  ended: "bg-ground text-muted",
  expired: "bg-ground text-muted",
  lapsed: "bg-ground text-muted",
};

export function GrantChip({ g }: { g: BreakGlassGrant }) {
  return (
    <span className={`chip ${STATE_TONE[g.state]}`}>
      {g.state}
      {g.state === "active" && g.expires_at && ` until ${dateTime(g.expires_at)}`}
    </span>
  );
}

function Ask() {
  const qc = useQueryClient();
  const tenants = useQuery({ queryKey: ["console", "tenants"], queryFn: api.tenants });
  const [tenant, setTenant] = useState("");
  const [reason, setReason] = useState("");
  const [minutes, setMinutes] = useState("60");
  const chosen = tenant || tenants.data?.[0]?.id || "";
  const ask = useMutation({
    mutationFn: () => api.requestBreakGlass(chosen, reason, Number(minutes)),
    onSuccess: () => {
      setReason("");
      qc.invalidateQueries({ queryKey: ["break-glass"] });
    },
  });
  return (
    <form
      className="card mb-4 space-y-3 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        ask.mutate();
      }}
    >
      <h2 className="text-sm font-bold text-ink">Ask a tenant for access</h2>
      <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
        <div>
          <label htmlFor="glass-tenant" className="label">
            Tenant
          </label>
          <select id="glass-tenant" className="input" value={chosen} onChange={(e) => setTenant(e.target.value)}>
            {tenants.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="glass-minutes" className="label">
            For
          </label>
          <select id="glass-minutes" className="input w-36" value={minutes} onChange={(e) => setMinutes(e.target.value)}>
            {[15, 30, 60, 120, 240, 480].map((m) => (
              <option key={m} value={m}>
                {m < 60 ? `${m} minutes` : `${m / 60} hour${m === 60 ? "" : "s"}`}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label htmlFor="glass-reason" className="label">
          Why
        </label>
        <textarea
          id="glass-reason"
          className="input min-h-[4.5rem]"
          value={reason}
          minLength={10}
          maxLength={500}
          onChange={(e) => setReason(e.target.value)}
          placeholder="What you need to look at, and the ticket it is for. The owner decides on this."
          required
        />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-accent" disabled={!chosen || ask.isPending}>
          Ask the owner
        </button>
        <span className="text-xs text-muted">
          Read-only. The time starts when the owner approves. Every page you open is recorded in the tenant's audit
          log.
        </span>
        {ask.error && (
          <span role="alert" className="w-full text-xs text-bad">
            {(ask.error as Error).message}
          </span>
        )}
      </div>
    </form>
  );
}

/**
 * Platform support's break-glass requests: ask a tenant, then, once its owner approves,
 * open the tenant read-only until the grant ends.
 */
export default function SupportConsole() {
  const qc = useQueryClient();
  const mine = useQuery({ queryKey: ["break-glass"], queryFn: api.myBreakGlass, refetchInterval: 15_000 });
  const end = useMutation({
    mutationFn: (id: string) => api.endMyBreakGlass(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["break-glass"] }),
  });
  return (
    <>
      <PageHeader
        title="Support access"
        subtitle="You hold no access to a tenant's data. Ask its owner, and look while the grant lasts."
      />
      <Ask />
      <section className="card overflow-x-auto">
        {mine.isPending ? (
          <p className="px-4 py-6 text-sm text-muted">Loading…</p>
        ) : !mine.data?.length ? (
          <EmptyState title="No requests yet" body="Requests you make, and what each owner decided, appear here." />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line">
                <th className="th">Tenant</th>
                <th className="th">Reason</th>
                <th className="th">Asked</th>
                <th className="th text-right">State</th>
                <th className="th" />
              </tr>
            </thead>
            <tbody>
              {mine.data.map((g) => (
                <tr key={g.id} className="border-b border-line last:border-0">
                  <td className="td font-semibold text-ink">{g.tenant_name}</td>
                  <td className="td min-w-[16rem] max-w-md whitespace-normal text-muted">{g.reason}</td>
                  <td className="td text-muted">{dateTime(g.requested_at)}</td>
                  <td className="td text-right">
                    <GrantChip g={g} />
                  </td>
                  <td className="td text-right">
                    <div className="flex justify-end gap-1.5">
                      {g.state === "active" && (
                        <button className="btn-accent btn-sm" onClick={() => switchBreakGlass(g.id, "/")}>
                          Open {g.tenant_name}
                        </button>
                      )}
                      {(g.state === "active" || g.state === "pending") && (
                        <button className="btn-ghost btn-sm" disabled={end.isPending} onClick={() => end.mutate(g.id)}>
                          {g.state === "pending" ? "Withdraw" : "End"}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
