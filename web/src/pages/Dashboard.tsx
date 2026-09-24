import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  Boxes,
  Camera,
  Gauge,
  Maximize2,
  Radio,
  Truck,
  VideoOff,
} from "lucide-react";
import { Link } from "react-router-dom";
import { MEDIA_BASE, useMediaServerUp } from "../api/media";
import { api } from "../api/client";
import type { Camera as Cam, Direction } from "../api/types";
import { type Me, hasRole } from "../auth/session";
import { ThroughputChart } from "../components/charts";
import { InsightFeed } from "../components/insights";
import { KpiCard } from "../components/kpi";
import { HealthStrip, LoadLifecycle } from "../components/lifecycle";
import { CameraDot, EmptyState, SessionBadge, VarianceBar, pct, time } from "../components/ui";

const TARGET = 0.95;

/** The bay itself, as large as the page allows: this is what the room watches. */
function LiveBay({
  camera,
  count,
  plate,
  direction,
}: {
  camera: Cam | undefined;
  count: number | null;
  plate: string | null;
  direction: string | null;
}) {
  const live = camera?.status === "online";
  return (
    <div className="video-well aspect-video w-full">
      {live ? (
        <iframe
          title={camera.name}
          src={`${MEDIA_BASE}/${camera.stream_path}`}
          className="absolute inset-0 h-full w-full"
          allow="autoplay"
        />
      ) : (
        <div className="absolute inset-0 grid place-items-center text-center">
          <div className="text-white/40">
            <VideoOff size={34} className="mx-auto" />
            <p className="mt-3 text-sm font-semibold text-white/70">
              {camera ? "No signal from the bay camera" : "No bay camera registered"}
            </p>
            <p className="mt-1 text-xs">
              {camera ? camera.name : "Add a camera to start counting"}
            </p>
          </div>
        </div>
      )}

      {/* overlay chrome — never intercepts clicks meant for the player */}
      <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-4">
        <div className="flex items-start justify-between gap-3">
          <span
            className={`chip backdrop-blur ${
              live ? "bg-bad/90 text-white" : "bg-black/50 text-white/70"
            }`}
          >
            <span className={`dot ${live ? "animate-pulse bg-white" : "bg-white/50"}`} />
            {live ? "LIVE" : "OFFLINE"}
          </span>
          {camera && (
            <span className="chip bg-black/50 text-white/80 backdrop-blur">{camera.name}</span>
          )}
        </div>

        <div className="flex items-end justify-between gap-3">
          <div>
            {plate && (
              <div className="num mb-2 inline-block rounded-md bg-warn px-2.5 py-0.5 text-sm font-bold tracking-[0.15em] text-black">
                {plate}
              </div>
            )}
            {count !== null && (
              <div
                role="status"
                aria-live="polite"
                aria-label={`Crates counted: ${count}`}
                className="rounded-xl bg-black/55 px-4 py-2.5 backdrop-blur"
              >
                <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-white/60">
                  Crates counted {direction ? `· ${direction}` : ""}
                </div>
                <AnimatePresence mode="popLayout" initial={false}>
                  <motion.div
                    key={count}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.2 }}
                    className="num text-5xl font-bold leading-none text-white"
                  >
                    {count.toLocaleString()}
                  </motion.div>
                </AnimatePresence>
              </div>
            )}
          </div>
          <Link
            to="/live"
            className="pointer-events-auto chip bg-black/50 text-white/80 backdrop-blur transition hover:bg-black/70"
          >
            <Maximize2 size={12} /> Camera wall
          </Link>
        </div>
      </div>
    </div>
  );
}

/** Every camera at a glance, so a gap in coverage is visible from across the room. */
function CameraStrip({ cameras }: { cameras: Cam[] }) {
  if (!cameras.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5 border-t border-line px-4 py-3">
      {cameras.map((c) => (
        <span
          key={c.id}
          title={`${c.name} · ${c.status}`}
          className={`chip border ${
            c.status === "online"
              ? "border-good/30 bg-good/10 text-good"
              : "border-line bg-ground text-faint"
          }`}
        >
          <CameraDot status={c.status} />
          <span className="max-w-[9rem] truncate">{c.name}</span>
        </span>
      ))}
    </div>
  );
}

export default function Dashboard({ me }: { me: Me | undefined }) {
  const qc = useQueryClient();
  const still = useReducedMotion();
  const canOperate = hasRole(me, "operator");

  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: () => api.overview(14),
    refetchInterval: 30_000,
  });
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });
  const assistant = useQuery({ queryKey: ["assistant-status"], queryFn: api.assistantStatus });
  const mediaUp = useMediaServerUp();
  const bay = bays.data?.[0];
  const cameras = useQuery({
    queryKey: ["cameras", bay?.id],
    queryFn: () => api.cameras(bay!.id),
    enabled: !!bay,
  });

  const o = overview.data;
  const active = sessions.data?.find((s) => s.status === "open");
  const bayCam =
    cameras.data?.find((c) => c.role === "chokepoint" && c.status === "online") ??
    cameras.data?.find((c) => c.status === "online") ??
    cameras.data?.find((c) => c.role === "chokepoint");

  const refresh = () => {
    for (const key of ["sessions", "summary", "overview"]) {
      qc.invalidateQueries({ queryKey: [key] });
    }
  };
  const open = useMutation({
    mutationFn: (d: Direction) => api.openSession(bay!.id, d),
    onSuccess: refresh,
  });
  const close = useMutation({ mutationFn: api.closeSession, onSuccess: refresh });

  const accuracy = o?.mean_accuracy ?? null;
  const onlineRatio = o && o.cameras_total ? o.cameras_online / o.cameras_total : 0;

  return (
    <>
      {/* header */}
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-extrabold tracking-tight text-ink">Operations</h1>
            <span className="chip bg-good/10 text-good">
              <span className="dot animate-pulse bg-good" />
              Live
            </span>
          </div>
          <p className="mt-1 text-sm text-muted">
            {bay ? bay.name : "Loading bay"} · crate counting, plate matching and reconciliation
            {o && ` · last ${o.days} days`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/sessions" className="btn-ghost btn-sm">
            <Activity size={14} /> Reconciliation
          </Link>
          <Link to="/analysis" className="btn-primary btn-sm">
            <Radio size={14} /> Analyse footage
          </Link>
        </div>
      </div>

      {/* KPI row */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          index={0}
          label="Crates today"
          value={(o?.crates_today ?? 0).toLocaleString()}
          delta={o?.crates.delta_pct ?? null}
          series={o?.crates.series}
          tone="brand"
          icon={Boxes}
          footer={`${o?.sessions_today ?? 0} truck session${o?.sessions_today === 1 ? "" : "s"} today`}
        />
        <KpiCard
          index={1}
          label="Counting accuracy"
          value={pct(accuracy)}
          delta={o?.accuracy.delta_pct ?? null}
          deltaUnit="points"
          series={o?.accuracy.series}
          tone={accuracy === null ? "brand" : accuracy >= TARGET ? "good" : "warn"}
          icon={Gauge}
          footer={
            accuracy === null
              ? "Awaiting the first manual verification"
              : `${o?.verified_sessions} verified · ${pct(TARGET)} target`
          }
        />
        <KpiCard
          index={2}
          label="Truck throughput"
          value={(o?.throughput.value ?? 0).toLocaleString()}
          unit={`in ${o?.days ?? 14}d`}
          delta={o?.throughput.delta_pct ?? null}
          series={o?.throughput.series}
          tone="accent"
          icon={Truck}
          footer={`${o?.open_sessions ?? 0} at the bay now · ${o?.unverified_sessions ?? 0} awaiting count`}
        />
        <KpiCard
          index={3}
          label="Camera health"
          value={`${o?.cameras_online ?? 0}/${o?.cameras_total ?? 0}`}
          tone={onlineRatio === 1 ? "good" : onlineRatio > 0 ? "warn" : "bad"}
          icon={Camera}
          delta={undefined}
          footer={
            <div className="mt-1">
              <div className="h-1.5 overflow-hidden rounded-full bg-line">
                <motion.div
                  initial={still ? false : { width: 0 }}
                  animate={{ width: `${onlineRatio * 100}%` }}
                  transition={{ duration: 0.6, ease: "easeOut" }}
                  className={`h-full rounded-full ${
                    onlineRatio === 1 ? "bg-good" : onlineRatio > 0 ? "bg-warn" : "bg-bad"
                  }`}
                />
              </div>
              <span className="mt-1.5 block">
                {o?.cameras_total
                  ? `${Math.round(onlineRatio * 100)}% of cameras streaming`
                  : "No cameras registered"}
              </span>
            </div>
          }
        />
      </div>

      {/* a load's journey, left to right: a pile-up at one stage shows as a shape */}
      <div className="mt-3">
        <LoadLifecycle
          open={o?.open_sessions ?? 0}
          awaiting={o?.unverified_sessions ?? 0}
          disputed={o?.disputed_sessions ?? 0}
          approved={o?.approved_sessions ?? 0}
          reconciled={o?.reconciled_sessions ?? 0}
        />
      </div>

      <div className="mt-3">
        <HealthStrip
          services={[
            {
              name: "API",
              ok: overview.isError ? false : overview.isSuccess ? true : undefined,
              detail: overview.isError ? "unreachable" : "responding",
            },
            {
              name: "Cameras",
              ok: (o?.cameras_online ?? 0) > 0,
              detail: `${o?.cameras_online ?? 0}/${o?.cameras_total ?? 0} streaming`,
            },
            {
              name: "Media server",
              ok: mediaUp,
              detail: mediaUp === false ? "unreachable" : mediaUp ? "reachable" : "checking",
            },
            {
              name: "Assistant",
              ok: assistant.data?.enabled,
              detail: assistant.data?.enabled ? (assistant.data.model ?? "ready") : "not configured",
            },
          ]}
        />
      </div>

      {/* the bay, and what needs attention */}
      <div className="mt-3 grid gap-3 xl:grid-cols-3">
        <section className="card-lift overflow-hidden xl:col-span-2">
          <LiveBay
            camera={bayCam}
            count={active ? active.ai_count : null}
            plate={active?.plate ?? null}
            direction={active?.direction ?? null}
          />
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            {active ? (
              <>
                <div className="flex items-center gap-2 text-sm">
                  <SessionBadge status={active.status} />
                  <span className="text-muted">since {time(active.opened_at)}</span>
                </div>
                <button
                  className="btn-ghost btn-sm"
                  disabled={close.isPending || !canOperate}
                  onClick={() => close.mutate(active.id)}
                >
                  End session
                </button>
              </>
            ) : (
              <>
                <p className="text-sm text-muted">
                  Bay clear. A session opens itself when the LPR camera reads a plate.
                </p>
                <div className="flex gap-2">
                  <button
                    className="btn-primary btn-sm"
                    disabled={!bay || open.isPending || !canOperate}
                    onClick={() => open.mutate("loading")}
                  >
                    Start loading
                  </button>
                  <button
                    className="btn-ghost btn-sm"
                    disabled={!bay || open.isPending || !canOperate}
                    onClick={() => open.mutate("offloading")}
                  >
                    Start offloading
                  </button>
                </div>
              </>
            )}
          </div>
          <CameraStrip cameras={cameras.data ?? []} />
        </section>

        <InsightFeed insights={o?.insights ?? []} />
      </div>

      {/* trend and recent activity */}
      <div className="mt-3 grid gap-3 xl:grid-cols-3">
        <section className="card overflow-hidden xl:col-span-2">
          <div className="panel-head">
            <div>
              <h2 className="text-sm font-bold text-ink">Throughput and accuracy</h2>
              <p className="text-xs text-muted">
                Crates counted per day, with verified accuracy against the 95% target
              </p>
            </div>
            <div className="hidden items-center gap-3 text-xs text-muted sm:flex">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-brand" /> Crates
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-accent" /> Accuracy
              </span>
            </div>
          </div>
          <div className="px-2 py-4">
            {o?.daily.length ? (
              <ThroughputChart daily={o.daily} />
            ) : (
              <EmptyState title="No activity yet" body="The chart fills as trucks are counted." />
            )}
          </div>
        </section>

        <section className="card overflow-hidden">
          <div className="panel-head">
            <h2 className="panel-title">Recent trucks</h2>
            <Link to="/sessions" className="text-xs font-semibold text-brand hover:underline">
              View all →
            </Link>
          </div>
          {sessions.data?.length ? (
            <ul className="divide-y divide-line">
              {sessions.data.slice(0, 6).map((row) => (
                <li
                  key={row.id}
                  className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-5 py-3 transition hover:bg-ground/60"
                >
                  <span className="num w-20 text-sm font-semibold text-ink">
                    {row.plate ?? "—"}
                  </span>
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
            <EmptyState title="No trucks yet" body="Sessions appear here as trucks arrive." />
          )}
        </section>
      </div>
    </>
  );
}
