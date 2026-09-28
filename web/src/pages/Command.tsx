/**
 * Command: the bay as a control room sees it. One large feed with a heads-up display,
 * the rest of the wall beneath it, what needs attention beside it, and the bay's
 * recent history below.
 *
 * Every figure comes from the API or the live event stream. Where the platform has
 * nothing to report, the screen says so: no invented counts, detections or alerts.
 */
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, animate, motion, useReducedMotion } from "framer-motion";
import { Boxes, Camera as CameraIcon, Gauge, MonitorPlay, Repeat, Truck } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { onLive } from "../api/live";
import { useMediaServerUp } from "../api/media";
import { useScope } from "../api/scope";
import type { Camera, Session } from "../api/types";
import { ThroughputChart } from "../components/charts";
import { AccuracyDots } from "../components/command/accuracy";
import {
  ACCURACY_TARGET,
  type FeedItem,
  type Known,
  type Mark,
  type Severity,
  attention,
  history,
  interpret,
  mergeActivity,
} from "../components/command/activity";
import { BayPlan, BayPlanLegend } from "../components/command/bayplan";
import { Clock, LIVE_CHIP, Reticle, StreamView, isStreaming } from "../components/command/feed";
import { Timeline, WINDOWS, type WindowKey } from "../components/command/timeline";
import { EmptyState, SessionBadge, pct, roleLabel, time } from "../components/ui";

const TOUR_MS = 12_000;

/** Live events for this bay, kept for as long as the page is open. */
function useLiveActivity(sessions: Session[] | undefined, bayId: string | undefined) {
  const known = useRef(new Map<string, Known>());
  const [live, setLive] = useState<{ items: FeedItem[]; marks: Mark[] }>({ items: [], marks: [] });

  useEffect(() => {
    for (const s of sessions ?? []) {
      if (!known.current.has(s.id)) known.current.set(s.id, { plate: s.plate, ai_count: s.ai_count });
    }
  }, [sessions]);

  useEffect(() => {
    setLive({ items: [], marks: [] }); // a different bay starts a different story
    return onLive((m) => {
      const r = interpret(m, known.current, bayId);
      if (!r.items.length && !r.marks.length) return;
      setLive((p) => ({
        items: [...r.items, ...p.items].slice(0, 60),
        marks: [...p.marks, ...r.marks].slice(-400),
      }));
    });
  }, [bayId]);

  return live;
}

const SEV_DOT: Record<Severity, string> = {
  bad: "text-bad",
  warn: "text-warn",
  info: "text-brand",
  good: "text-good",
};

function FeedList({
  items,
  empty,
  onPick,
}: {
  items: FeedItem[];
  empty: ReactNode;
  onPick: (cameraId: string) => void;
}) {
  const still = useReducedMotion();
  if (!items.length) return <div className="px-4 py-6 text-sm text-muted">{empty}</div>;
  return (
    <ul className="grid content-start gap-0.5 p-1.5">
      <AnimatePresence initial={false}>
        {items.map((i) => {
          const body = (
            <>
              {/* only a fault pulses, so the eye goes there first */}
              <span className={`dot relative mt-1.5 bg-current ${SEV_DOT[i.severity]} ${i.severity === "bad" ? "ping" : ""}`} />
              <span className="min-w-0">
                <span className="block text-[13px] font-bold leading-snug text-ink">{i.title}</span>
                <span className="block text-xs text-muted">{i.detail}</span>
                {i.at !== null && <span className="num block text-[11px] text-faint">{time(new Date(i.at).toISOString())}</span>}
              </span>
            </>
          );
          const cls = "grid w-full grid-cols-[auto_minmax(0,1fr)] gap-2.5 rounded-lg px-2.5 py-2 text-left";
          return (
            <motion.li
              key={i.key}
              layout={!still}
              initial={still ? false : { x: 28 }}
              animate={{ x: 0 }}
              transition={{ type: "spring", bounce: 0.25, duration: 0.5 }}
            >
              {i.cameraId ? (
                <button className={`${cls} transition hover:bg-ground`} onClick={() => onPick(i.cameraId!)}>
                  {body}
                </button>
              ) : (
                <div className={cls}>{body}</div>
              )}
            </motion.li>
          );
        })}
      </AnimatePresence>
    </ul>
  );
}

/** A headline figure whose digits roll when the value changes. */
function Stat({
  label,
  value,
  unit,
  footer,
  icon: Icon,
  tone = "text-ink",
  children,
}: {
  label: string;
  value: string;
  unit?: string;
  footer: ReactNode;
  icon: typeof Boxes;
  tone?: string;
  children?: ReactNode;
}) {
  const still = useReducedMotion();
  return (
    <div className="card min-w-0 px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="eyebrow">{label}</span>
        <Icon size={15} className="text-faint" />
      </div>
      {/* not clipped: if the animation never runs (a hidden tab gets no frames), the
          figure rests a few pixels low and is still whole */}
      <div className="mt-1 flex items-baseline gap-2">
        {/* a new value rises into place; the old one is simply replaced */}
        <motion.span
          key={value}
          initial={still ? false : { y: 8 }}
          animate={{ y: 0 }}
          transition={{ type: "spring", bounce: 0.3, duration: 0.45 }}
          className={`num inline-block text-[28px] font-bold leading-tight ${tone}`}
        >
          {value}
        </motion.span>
        {unit && <span className="text-xs font-semibold text-faint">{unit}</span>}
      </div>
      {children}
      <div className="mt-1 text-xs text-muted">{footer}</div>
    </div>
  );
}

export default function Command() {
  const still = useReducedMotion();
  const { bay } = useScope();
  const mediaUp = useMediaServerUp();

  const cameras = useQuery({
    queryKey: ["cameras", bay?.id],
    queryFn: () => api.cameras(bay!.id),
    enabled: !!bay,
  });
  const sessions = useQuery({
    queryKey: ["sessions", bay?.id],
    queryFn: () => api.sessions(bay?.id),
    enabled: !!bay,
  });
  const overview = useQuery({
    queryKey: ["overview", bay?.id],
    queryFn: () => api.overview(14, bay?.id),
    enabled: !!bay,
    refetchInterval: 30_000,
  });

  const cams = useMemo(() => cameras.data ?? [], [cameras.data]);
  const list = useMemo(() => sessions.data ?? [], [sessions.data]);
  const o = overview.data;
  const open = list.find((s) => s.status === "open");
  const live = useLiveActivity(sessions.data, bay?.id);

  // ---- which camera fills the main view
  const [chosen, setChosen] = useState<string | null>(null);
  const fallback =
    cams.find((c) => c.role === "chokepoint" && c.status === "online") ??
    cams.find((c) => c.status === "online") ??
    cams.find((c) => c.role === "chokepoint") ??
    cams[0];
  const main: Camera | undefined = cams.find((c) => c.id === chosen) ?? fallback;

  const wellRef = useRef<HTMLDivElement>(null);
  const wipeRef = useRef<HTMLDivElement>(null);
  const tiles = useRef(new Map<string, HTMLElement>());

  const select = (id: string) => {
    if (id === main?.id) return;
    const from = tiles.current.get(id)?.getBoundingClientRect();
    setChosen(id);
    const well = wellRef.current;
    if (still || !well) return;
    // FLIP: the main view grows out of the tile it was chosen from
    requestAnimationFrame(() => {
      const to = well.getBoundingClientRect();
      if (from && to.width) {
        animate(
          well,
          {
            x: [from.left - to.left, 0],
            y: [from.top - to.top, 0],
            scaleX: [from.width / to.width, 1],
            scaleY: [from.height / to.height, 1],
          },
          { type: "spring", bounce: 0.12, duration: 0.7 },
        );
      }
      if (wipeRef.current) animate(wipeRef.current, { x: ["-101%", "101%"] }, { duration: 0.65, ease: [0.6, 0, 0.2, 1] });
    });
  };

  // ---- tour the streaming cameras
  const streaming = cams.filter((c) => isStreaming(c, mediaUp));
  const [tour, setTour] = useState(false);
  const canTour = streaming.length > 1;
  const next = useRef<() => void>(() => {});
  next.current = () => {
    const i = streaming.findIndex((c) => c.id === main?.id);
    select(streaming[(i + 1) % streaming.length].id);
  };
  useEffect(() => {
    if (!tour || !canTour) return;
    const id = window.setInterval(() => next.current(), TOUR_MS);
    return () => window.clearInterval(id);
  }, [tour, canTour]);
  const pick = (id: string) => {
    setTour(false);
    select(id);
  };

  // ---- what the rail says
  const needs = attention({ cameras: cameras.data, mediaUp, sessions: sessions.data, insights: o?.insights });
  const activity = mergeActivity(live.items, history(sessions.data));

  const [windowKey, setWindowKey] = useState<WindowKey>("4h");
  const online = o?.cameras_online ?? cams.filter((c) => c.status === "online").length;
  const total = o?.cameras_total ?? cams.length;
  const accuracy = o?.mean_accuracy ?? null;
  const chip = main
    ? main.status === "online" && mediaUp === false
      ? LIVE_CHIP.offline
      : LIVE_CHIP[main.status]
    : LIVE_CHIP.offline;

  return (
    <>
      {/* header */}
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Command</h1>
          <p className="mt-0.5 text-sm text-muted">
            {bay ? bay.name : "Loading bay"} · live feed, coverage and activity in one view ·{" "}
            <Clock className="text-ink" />
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className={`btn-ghost btn-sm ${tour ? "border-brand/60 bg-brand-tint" : ""}`}
            aria-pressed={tour}
            disabled={!canTour}
            title={canTour ? "Step through the streaming cameras" : "Needs two or more cameras streaming"}
            onClick={() => setTour((t) => !t)}
          >
            <Repeat size={14} /> Tour cameras
          </button>
          <Link to="/live" className="btn-ghost btn-sm">
            <MonitorPlay size={14} /> Camera wall
          </Link>
        </div>
      </div>

      {/* headline figures */}
      <section aria-label="Today" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Crates today"
          value={o ? o.crates_today.toLocaleString() : "—"}
          icon={Boxes}
          footer={open ? `${open.ai_count.toLocaleString()} on the truck at the bay` : "No truck at the bay"}
        />
        <Stat
          label="Loads today"
          value={o ? String(o.sessions_today) : "—"}
          icon={Truck}
          footer={o ? `${o.unverified_sessions} awaiting a manual count` : "Loading"}
        />
        <Stat
          label="Verified accuracy"
          value={pct(accuracy)}
          icon={Gauge}
          tone={accuracy === null ? "text-ink" : accuracy >= ACCURACY_TARGET ? "text-good" : "text-warn"}
          footer={
            accuracy === null
              ? "Awaiting the first manual verification"
              : `${o?.verified_sessions} verified · ${pct(ACCURACY_TARGET)} target`
          }
        />
        <Stat
          label="Cameras streaming"
          value={total ? `${online}/${total}` : "—"}
          icon={CameraIcon}
          tone={!total ? "text-ink" : online === total ? "text-good" : online ? "text-warn" : "text-bad"}
          footer={total ? `${Math.round((online / total) * 100)}% of this bay's cameras` : "No cameras registered"}
        >
          {total > 0 && (
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line">
              <div
                className={`h-full origin-left rounded-full transition-transform duration-700 ease-out ${
                  online === total ? "bg-good" : online ? "bg-warn" : "bg-bad"
                }`}
                style={{ transform: `scaleX(${online / total})` }}
              />
            </div>
          )}
        </Stat>
      </section>

      {/* the feed, and what needs attention */}
      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="card-lift relative z-[1] min-w-0">
          <div
            ref={wellRef}
            className="video-well scanlines aspect-video w-full rounded-t-xl"
            style={{ transformOrigin: "0 0" }}
          >
            <StreamView camera={main} mediaUp={mediaUp} />
            <div aria-hidden className="hud-sweep" />
            <Reticle />
            <div
              ref={wipeRef}
              aria-hidden
              className="pointer-events-none absolute inset-0 z-[3]"
              style={{
                transform: "translateX(-101%)",
                background:
                  "linear-gradient(90deg, transparent 30%, rgb(160 180 255 / .35) 48%, rgb(9 13 28 / .96) 52%, rgb(9 13 28) 100%)",
              }}
            />

            {/* heads-up display: always light, it sits on footage */}
            <div className="pointer-events-none absolute inset-0 z-[4] flex flex-col justify-between p-3 text-white sm:p-6">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={`chip tracking-[0.08em] ${chip.cls}`}>
                    {chip === LIVE_CHIP.online && <span className="dot animate-pulse bg-white" />}
                    {chip.label}
                  </span>
                  {main && (
                    <span className="chip bg-black/55 text-white/85 backdrop-blur">
                      {main.name} · {roleLabel(main.role)}
                    </span>
                  )}
                </div>
                <Clock className="chip bg-black/55 text-white/80 backdrop-blur" />
              </div>

              <div className="flex flex-wrap items-end justify-between gap-3">
                {open ? (
                  <div role="status" aria-live="polite" className="rounded-xl border border-white/10 bg-black/55 px-4 py-2.5 backdrop-blur">
                    <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/60">
                      Crates counted · {open.direction}
                    </div>
                    <motion.div
                      key={open.ai_count}
                      initial={still ? false : { y: 8 }}
                      animate={{ y: 0 }}
                      transition={{ type: "spring", bounce: 0.3, duration: 0.4 }}
                      className="num text-4xl font-bold leading-tight sm:text-5xl"
                    >
                      {open.ai_count.toLocaleString()}
                    </motion.div>
                  </div>
                ) : (
                  <span className="chip bg-black/55 text-white/75 backdrop-blur">Bay clear · no session open</span>
                )}
                {open && (
                  <span
                    className={`num rounded-md px-2.5 py-1 text-sm font-bold tracking-[0.15em] ${
                      open.plate ? "bg-warn text-black shadow-[0_0_22px_-4px_rgb(var(--warn))]" : "bg-black/55 text-white/80"
                    }`}
                  >
                    {open.plate ?? "Plate not read yet"}
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
            {open ? (
              <span className="flex items-center gap-2">
                <SessionBadge status={open.status} />
                <span className="text-muted">
                  {open.direction} since {time(open.opened_at)}
                </span>
              </span>
            ) : (
              <span className="text-muted">A session opens itself when the plate reader reads a truck.</span>
            )}
            <span className="text-xs text-faint">
              On-screen: count and plate. Detection boxes need the pipeline's frame stream.
            </span>
          </div>
        </section>

        <aside className="relative min-w-0">
          <div className="flex flex-col gap-3 xl:absolute xl:inset-0">
            <section className="card flex max-h-[45%] min-h-0 flex-col max-xl:max-h-none">
              <div className="panel-head">
                <h2 className="panel-title">Needs attention</h2>
                <span className={`chip ${needs.some((n) => n.severity === "bad") ? "bg-bad/10 text-bad" : "bg-ground text-muted"}`}>
                  {needs.filter((n) => n.severity === "bad" || n.severity === "warn").length}
                </span>
              </div>
              <div className="min-h-0 overflow-y-auto">
                <FeedList
                  items={needs}
                  onPick={pick}
                  empty={cameras.isSuccess ? "Nothing needs attention at this bay." : "Checking the bay…"}
                />
              </div>
            </section>
            <section className="card flex min-h-0 flex-1 flex-col max-xl:max-h-[420px]">
              <div className="panel-head">
                <h2 className="panel-title">Activity</h2>
                <span className="text-xs text-faint">live events and recent loads</span>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <FeedList items={activity} onPick={pick} empty="No loads recorded at this bay yet." />
              </div>
            </section>
          </div>
        </aside>
      </div>

      {/* the rest of the wall */}
      {cams.length > 0 ? (
        <section aria-label="Cameras" className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6 2xl:grid-cols-8">
          {cams.map((c) => {
            const sel = c.id === main?.id;
            const live = isStreaming(c, mediaUp);
            return (
              <figure
                key={c.id}
                ref={(el) => {
                  if (el) tiles.current.set(c.id, el);
                  else tiles.current.delete(c.id);
                }}
                className={`video-well aspect-video rounded-lg ring-1 transition duration-200 hover:-translate-y-0.5 ${
                  sel ? "shadow-[0_0_20px_-4px_rgb(var(--accent))] ring-2 ring-accent" : "ring-line"
                }`}
              >
                <StreamView camera={c} mediaUp={mediaUp} compact />
                <figcaption className="pointer-events-none absolute inset-x-0 bottom-0 z-[3] flex items-center justify-between gap-1.5 bg-gradient-to-t from-black/80 to-transparent px-2 pb-1.5 pt-5">
                  <span className="truncate text-[11px] font-semibold text-white">{c.name}</span>
                  <span className={`chip flex-none px-1.5 py-0 text-[9.5px] ${live ? LIVE_CHIP.online.cls : LIVE_CHIP[c.status === "online" ? "offline" : c.status].cls}`}>
                    {live ? "LIVE" : c.status === "degraded" ? "DEGRADED" : "OFFLINE"}
                  </span>
                </figcaption>
                <button
                  className="absolute inset-0 z-[4] rounded-lg"
                  aria-label={`Show ${c.name} in the main view`}
                  aria-pressed={sel}
                  onClick={() => pick(c.id)}
                />
              </figure>
            );
          })}
        </section>
      ) : (
        cameras.isSuccess && (
          <div className="card mt-3">
            <EmptyState title="No cameras registered" body="Add cameras to this bay to see their feeds here." />
          </div>
        )
      )}

      {/* coverage and trends */}
      <div className="mt-3 grid gap-3 xl:grid-cols-2">
        <section className="card min-w-0">
          <div className="panel-head">
            <h2 className="panel-title">Bay coverage</h2>
            <span className="text-xs text-faint">select a camera to view it</span>
          </div>
          <div className="px-3 pt-3">
            <BayPlan bay={bay} cameras={cams} session={open} selectedId={main?.id} onSelect={pick} />
          </div>
          <div className="px-4 pb-3 pt-1">
            <BayPlanLegend />
          </div>
        </section>

        <section className="card grid min-w-0 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <div className="min-w-0 p-4">
            <h2 className="panel-title">Crates per day</h2>
            <p className="text-xs text-muted">Last {o?.days ?? 14} days, with verified accuracy</p>
            <div className="mt-2 -ml-2">
              {o?.daily.length ? (
                <ThroughputChart daily={o.daily} height={210} />
              ) : (
                <EmptyState title="No activity yet" body="The chart fills as trucks are counted." />
              )}
            </div>
          </div>
          <div className="min-w-0 border-t border-line p-4 sm:border-l sm:border-t-0">
            <h2 className="panel-title">Accuracy per verified load</h2>
            <p className="text-xs text-muted">AI count against the manual sheet</p>
            <div className="mt-3">
              <AccuracyDots sessions={list} />
            </div>
          </div>
        </section>
      </div>

      {/* history */}
      <section className="card mt-3 min-w-0">
        <div className="panel-head">
          <h2 className="panel-title">Timeline</h2>
          <div className="segment" role="group" aria-label="Timeline window">
            {(Object.keys(WINDOWS) as WindowKey[]).map((k) => (
              <button
                key={k}
                aria-pressed={windowKey === k}
                className={`segment-item ${windowKey === k ? "segment-item-on" : ""}`}
                onClick={() => setWindowKey(k)}
              >
                {k}
              </button>
            ))}
          </div>
        </div>
        <Timeline sessions={list} marks={live.marks} windowKey={windowKey} />
      </section>
    </>
  );
}
