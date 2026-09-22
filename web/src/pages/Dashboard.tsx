import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, Camera, Gauge, Truck } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Direction } from "../api/types";
import { type Me, hasRole } from "../auth/session";
import { EmptyState, PageHeader, SessionBadge, StatCard, pct, time } from "../components/ui";

const TARGET = 0.95;

export default function Dashboard({ me }: { me: Me | undefined }) {
  const canOperate = hasRole(me, "operator");
  const qc = useQueryClient();
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });

  const bay = bays.data?.[0];
  const active = sessions.data?.find((s) => s.status === "open");
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["sessions"] });
    qc.invalidateQueries({ queryKey: ["summary"] });
  };
  const open = useMutation({
    mutationFn: (d: Direction) => api.openSession(bay!.id, d),
    onSuccess: refresh,
  });
  const close = useMutation({ mutationFn: api.closeSession, onSuccess: refresh });

  const s = summary.data;
  const accuracy = s?.mean_accuracy ?? null;

  return (
    <>
      <PageHeader
        title="Operations Dashboard"
        subtitle="Real-time crate counting and reconciliation for the POC loading bay"
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Crates counted today"
          value={(s?.crates_today ?? 0).toLocaleString()}
          hint={`${s?.sessions_today ?? 0} truck sessions`}
          icon={Boxes}
        />
        <StatCard
          label="Counting accuracy"
          value={pct(accuracy)}
          hint={
            accuracy === null
              ? "Awaiting first manual verification"
              : `${accuracy >= TARGET ? "Meets" : "Below"} the 95% POC target · ${s?.verified_sessions} verified`
          }
          icon={Gauge}
          accent
        />
        <StatCard
          label="Active sessions"
          value={String(s?.open_sessions ?? 0)}
          hint="Trucks currently at the bay"
          icon={Truck}
        />
        <StatCard
          label="Cameras online"
          value={`${s?.cameras_online ?? 0} / ${s?.cameras_total ?? 0}`}
          hint="16 volumetric + 1 LPR"
          icon={Camera}
        />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <section className="card overflow-hidden xl:col-span-1">
          <div className="bg-brand-navy px-5 py-4 text-white">
            <div className="text-xs font-semibold uppercase tracking-widest text-white/70">
              Current truck
            </div>
            <div className="mt-1 text-lg font-bold">{bay?.name ?? "—"}</div>
          </div>
          {active ? (
            <div className="p-5">
              <div className="flex items-center justify-between">
                <span className="rounded-md border-2 border-brand-navy bg-amber-50 px-3 py-1 font-mono text-lg font-bold tracking-widest text-brand-navy">
                  {active.plate ?? "READING…"}
                </span>
                <SessionBadge status={active.status} />
              </div>
              <div className="mt-6 text-center">
                <div className="text-6xl font-bold tabular-nums text-brand-magenta">
                  {active.ai_count.toLocaleString()}
                </div>
                <div className="mt-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  crates {active.direction === "loading" ? "loaded" : "offloaded"}
                </div>
              </div>
              <div className="mt-6 flex items-center justify-between text-sm text-slate-500">
                <span>Started {time(active.opened_at)}</span>
                <button
                  className="btn-accent"
                  disabled={close.isPending || !canOperate}
                  onClick={() => close.mutate(active.id)}
                >
                  End session
                </button>
              </div>
            </div>
          ) : (
            <div className="p-5">
              <p className="text-sm text-slate-500">
                No truck at the bay. Start a session when a truck arrives; its plate is attached
                from the LPR camera.
              </p>
              <div className="mt-4 flex gap-3">
                <button
                  className="btn-primary flex-1"
                  disabled={!bay || open.isPending || !canOperate}
                  onClick={() => open.mutate("loading")}
                >
                  Start loading
                </button>
                <button
                  className="btn-ghost flex-1"
                  disabled={!bay || open.isPending || !canOperate}
                  onClick={() => open.mutate("offloading")}
                >
                  Start offloading
                </button>
              </div>
            </div>
          )}
        </section>

        <section className="card xl:col-span-2">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
            <h2 className="font-semibold text-brand-navy">Recent sessions</h2>
            <Link to="/sessions" className="text-sm font-semibold text-brand-magenta hover:underline">
              View all
            </Link>
          </div>
          {sessions.data?.length ? (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-slate-100">
                    <th className="th">Plate</th>
                    <th className="th">Direction</th>
                    <th className="th text-right">AI count</th>
                    <th className="th text-right">Variance</th>
                    <th className="th">Status</th>
                    <th className="th">Opened</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.data.slice(0, 6).map((row) => (
                    <tr key={row.id} className="border-b border-slate-50 last:border-0">
                      <td className="td font-mono font-semibold text-brand-navy">
                        {row.plate ?? "—"}
                      </td>
                      <td className="td capitalize">{row.direction}</td>
                      <td className="td text-right tabular-nums">{row.ai_count.toLocaleString()}</td>
                      <td className="td text-right tabular-nums">
                        {row.variance === null
                          ? "—"
                          : row.variance > 0
                            ? `+${row.variance}`
                            : row.variance}
                      </td>
                      <td className="td">
                        <SessionBadge status={row.status} />
                      </td>
                      <td className="td text-slate-500">{time(row.opened_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              title="No sessions yet"
              body="Counts appear here as soon as the first truck is processed."
            />
          )}
        </section>
      </div>
    </>
  );
}
