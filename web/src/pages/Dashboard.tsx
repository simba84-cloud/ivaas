import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, Camera, Gauge, Truck, VideoOff } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Camera as Cam, Direction } from "../api/types";
import { type Me, hasRole } from "../auth/session";
import { EmptyState, Metric, PageHeader, SessionBadge, VarianceBar, pct, time } from "../components/ui";

const TARGET = 0.95;
const MEDIA_BASE = import.meta.env.VITE_MEDIA_URL ?? "http://localhost:8889";

function LiveBay({ camera }: { camera: Cam | undefined }) {
  if (!camera || camera.status !== "online") {
    return (
      <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 bg-ink/95 text-faint">
        <VideoOff size={28} />
        <span className="text-xs">{camera ? "No signal from the bay camera" : "No bay camera registered"}</span>
      </div>
    );
  }
  return (
    <iframe
      title={camera.name}
      src={`${MEDIA_BASE}/${camera.stream_path}`}
      className="aspect-video w-full bg-black"
      allow="autoplay"
    />
  );
}

export default function Dashboard({ me }: { me: Me | undefined }) {
  const qc = useQueryClient();
  const canOperate = hasRole(me, "operator");
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });
  const bay = bays.data?.[0];
  const cameras = useQuery({
    queryKey: ["cameras", bay?.id],
    queryFn: () => api.cameras(bay!.id),
    enabled: !!bay,
  });

  const active = sessions.data?.find((s) => s.status === "open");
  const bayCam =
    cameras.data?.find((c) => c.role === "chokepoint" && c.status === "online") ??
    cameras.data?.find((c) => c.status === "online") ??
    cameras.data?.find((c) => c.role === "chokepoint");

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
      <PageHeader title="Loading bay" subtitle={bay ? bay.name : "Live counting and reconciliation"} />

      {/* summary strip: quiet metrics, one line */}
      <div className="card mb-6 grid grid-cols-2 gap-x-6 gap-y-4 px-5 py-4 md:grid-cols-4">
        <Metric
          label="Crates today"
          value={(s?.crates_today ?? 0).toLocaleString()}
          hint={`${s?.sessions_today ?? 0} truck sessions`}
          icon={Boxes}
        />
        <Metric
          label="Accuracy"
          value={pct(accuracy)}
          hint={
            accuracy === null
              ? "Awaiting manual verification"
              : `${s?.verified_sessions} verified · target ${pct(TARGET)}`
          }
          icon={Gauge}
          tone={accuracy === null ? "neutral" : accuracy >= TARGET ? "good" : "warn"}
        />
        <Metric
          label="Trucks at bay"
          value={String(s?.open_sessions ?? 0)}
          hint={active ? `${active.plate ?? "plate pending"}` : "Bay is clear"}
          icon={Truck}
        />
        <Metric
          label="Cameras online"
          value={`${s?.cameras_online ?? 0} / ${s?.cameras_total ?? 0}`}
          hint={(s?.cameras_online ?? 0) === 0 ? "Nothing streaming" : "Streaming to the platform"}
          icon={Camera}
          tone={(s?.cameras_online ?? 0) === 0 ? "warn" : "neutral"}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-5">
        {/* the bay itself: video with the live count beside it */}
        <section className="card-lift overflow-hidden xl:col-span-3">
          <div className="grid md:grid-cols-[1fr_auto]">
            <LiveBay camera={bayCam} />
            <div className="flex min-w-0 flex-col justify-between border-t border-line p-5 md:w-64 md:border-l md:border-t-0">
              {active ? (
                <>
                  <div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="eyebrow">Now {active.direction}</span>
                      <SessionBadge status={active.status} />
                    </div>
                    <div className="num mt-2 inline-block rounded-md border-2 border-ink/80 bg-warn/15 px-2.5 py-0.5 text-base font-semibold tracking-[0.15em] text-ink">
                      {active.plate ?? "READING…"}
                    </div>
                  </div>
                  <div className="my-6">
                    <div className="num text-6xl font-bold leading-none text-accent">
                      {active.ai_count.toLocaleString()}
                    </div>
                    <div className="eyebrow mt-2">crates counted</div>
                  </div>
                  <div className="flex items-center justify-between text-xs text-muted">
                    <span>Since {time(active.opened_at)}</span>
                    <button
                      className="btn-ghost btn-sm"
                      disabled={close.isPending || !canOperate}
                      onClick={() => close.mutate(active.id)}
                    >
                      End session
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <div className="eyebrow">Bay clear</div>
                    <p className="mt-2 text-sm text-muted">
                      A session opens itself when the LPR camera reads a plate. Start one by hand if it
                      doesn't.
                    </p>
                  </div>
                  <div className="mt-6 grid gap-2">
                    <button
                      className="btn-primary"
                      disabled={!bay || open.isPending || !canOperate}
                      onClick={() => open.mutate("loading")}
                    >
                      Start loading
                    </button>
                    <button
                      className="btn-ghost"
                      disabled={!bay || open.isPending || !canOperate}
                      onClick={() => open.mutate("offloading")}
                    >
                      Start offloading
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </section>

        {/* recent sessions */}
        <section className="card overflow-hidden xl:col-span-2">
          <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
            <h2 className="text-sm font-bold text-ink">Recent trucks</h2>
            <Link to="/sessions" className="text-xs font-semibold text-brand hover:underline">
              Reconciliation →
            </Link>
          </div>
          {sessions.data?.length ? (
            <ul className="divide-y divide-line">
              {sessions.data.slice(0, 6).map((row) => (
                <li key={row.id} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-5 py-3">
                  <span className="num w-20 text-sm font-semibold text-ink">{row.plate ?? "—"}</span>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="num font-semibold">{row.ai_count.toLocaleString()}</span>
                      <span className="text-muted">crates</span>
                      <span className="capitalize text-faint">· {row.direction}</span>
                    </div>
                    <div className="mt-1">
                      <VarianceBar variance={row.variance} manual={row.manual_count} />
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <SessionBadge status={row.status} />
                    <span className="num text-xs text-faint">{time(row.opened_at)}</span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No trucks yet" body="Sessions appear here as trucks arrive at the bay." />
          )}
        </section>
      </div>
    </>
  );
}
