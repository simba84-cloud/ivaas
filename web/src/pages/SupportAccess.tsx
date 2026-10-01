import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState, PageHeader, dateTime } from "../components/ui";
import { GrantChip } from "./SupportConsole";

/**
 * The owner's say over support: every request Cassava's support has made of this
 * tenant, to approve or refuse, and access to end at any time. What support then
 * looked at is in the audit log, under "break_glass_used".
 */
export default function SupportAccess() {
  const qc = useQueryClient();
  const grants = useQuery({ queryKey: ["support-access"], queryFn: api.supportAccess, refetchInterval: 15_000 });
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "deny" | "end" }) =>
      api.decideSupportAccess(id, action),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["support-access"] }),
  });
  return (
    <>
      <PageHeader
        title="Support access"
        subtitle="Cassava's support sees nothing of yours unless you approve it, read-only, for the time it asked."
      />
      {decide.error && (
        <p role="alert" className="mb-3 text-sm text-bad">
          {(decide.error as Error).message}
        </p>
      )}
      <section className="card overflow-x-auto">
        {grants.isPending ? (
          <p className="px-4 py-6 text-sm text-muted">Loading…</p>
        ) : grants.error ? (
          <p className="px-4 py-6 text-sm text-bad">{(grants.error as Error).message}</p>
        ) : !grants.data!.length ? (
          <EmptyState title="No requests" body="Support has never asked for access to this account." />
        ) : (
          <ul className="divide-y divide-line">
            {grants.data!.map((g) => (
              <li key={g.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-semibold text-ink">{g.requested_by}</span>
                    <span className="text-muted">
                      asks for {g.minutes < 60 ? `${g.minutes} minutes` : `${g.minutes / 60} h`} ·{" "}
                      {dateTime(g.requested_at)}
                    </span>
                    <GrantChip g={g} />
                  </div>
                  <p className="mt-1 text-sm text-ink">{g.reason}</p>
                  {g.decided_by && (
                    <p className="mt-1 text-xs text-muted">
                      {g.state === "denied" ? "Refused" : "Approved"} by {g.decided_by}
                      {g.decided_at && ` · ${dateTime(g.decided_at)}`}
                      {g.ended_by && ` · ended by ${g.ended_by}`}
                    </p>
                  )}
                </div>
                <div className="flex gap-1.5">
                  {g.state === "pending" && (
                    <>
                      <button
                        className="btn-accent btn-sm"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: g.id, action: "approve" })}
                      >
                        Approve
                      </button>
                      <button
                        className="btn-ghost btn-sm"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: g.id, action: "deny" })}
                      >
                        Refuse
                      </button>
                    </>
                  )}
                  {g.state === "active" && (
                    <button
                      className="btn-ghost btn-sm"
                      disabled={decide.isPending}
                      onClick={() => decide.mutate({ id: g.id, action: "end" })}
                    >
                      End access now
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      <p className="mt-3 text-xs text-muted">
        Every page support opens is recorded in the{" "}
        <Link to="/audit" className="font-semibold text-ink hover:underline">
          audit log
        </Link>
        . Support can look at counts, loads and live video; it cannot change anything.
      </p>
    </>
  );
}
