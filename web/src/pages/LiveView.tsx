/**
 * The camera wall. Tiles glide when the density, filter or order changes; choosing
 * one morphs it into a focused view with its details alongside. The wall's order is
 * the operator's own, kept per bay in this browser, and can be changed by dragging
 * or from the keyboard.
 *
 * Tiles show only what the platform reports: the stream, its status, faults raised
 * against the camera, and the load at the bay. No detection boxes are drawn, because
 * the API does not carry them.
 */
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, LayoutGroup, motion } from "framer-motion";
import {
  AlertTriangle,
  Grid2x2,
  Grid3x3,
  GripVertical,
  LayoutGrid,
  Maximize2,
  RotateCcw,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useMediaServerUp } from "../api/media";
import { useScope } from "../api/scope";
import type { Camera, CameraRole, Session } from "../api/types";
import { LIVE_CHIP, Reticle, StreamView, isStreaming } from "../components/command/feed";
import { EmptyState, roleLabel, time } from "../components/ui";
import { type Alert, SEVERITY_LABEL, useAlerts } from "../live/alerts";
import { AnimatedNumber, Segmented, transition } from "../motion";

const ROLE_ORDER: CameraRole[] = ["chokepoint", "overhead", "side_high", "side_mid", "side_low", "lpr"];
/** Roles that watch the stacks, so the load's count belongs on their tiles. */
const COUNTING: CameraRole[] = ["chokepoint", "overhead", "side_high", "side_mid", "side_low"];

/** Wall density, the way a VMS offers it: fewer, larger tiles or more, smaller ones. */
const LAYOUTS = {
  large: { cols: "sm:grid-cols-2 xl:grid-cols-3", icon: Grid2x2, label: "Large tiles" },
  standard: { cols: "sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4", icon: Grid3x3, label: "Standard" },
  compact: { cols: "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6", icon: LayoutGrid, label: "Compact" },
} as const;
type Layout = keyof typeof LAYOUTS;

const ago = (iso: string | null) => {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
};

/** The wall's order for this bay, remembered in this browser. */
function useWallOrder(bayId: string | undefined, cameras: Camera[]) {
  const key = `ivaas.wall-order.${bayId ?? "none"}`;
  const read = useCallback((): string[] => {
    try {
      return JSON.parse(localStorage.getItem(key) ?? "[]");
    } catch {
      return [];
    }
  }, [key]);
  const [order, setOrder] = useState<string[]>(read);
  useEffect(() => setOrder(read()), [read]);

  const sorted = useMemo(() => {
    const pos = new Map(order.map((id, i) => [id, i]));
    const rank = (c: Camera, i: number) => pos.get(c.id) ?? order.length + i;
    return cameras.map((c, i) => [c, rank(c, i)] as const).sort((a, b) => a[1] - b[1]).map(([c]) => c);
  }, [cameras, order]);

  const save = (ids: string[]) => {
    setOrder(ids);
    try {
      if (ids.length) localStorage.setItem(key, JSON.stringify(ids));
      else localStorage.removeItem(key);
    } catch {
      /* storage blocked: the order lasts for this page */
    }
  };
  const move = (id: string, to: number) => {
    const ids = sorted.map((c) => c.id);
    const from = ids.indexOf(id);
    if (from < 0 || to < 0 || to >= ids.length || from === to) return;
    ids.splice(from, 1);
    ids.splice(to, 0, id);
    save(ids);
  };
  return { sorted, move, reset: () => save([]), custom: order.length > 0 };
}

function Tile({
  camera,
  mediaUp,
  compact,
  alert,
  load,
  position,
  total,
  onFocus,
  onMove,
  drag,
}: {
  camera: Camera;
  mediaUp: boolean | undefined;
  compact: boolean;
  alert: Alert | undefined;
  load: Session | undefined;
  position: number;
  total: number;
  onFocus: () => void;
  onMove: (delta: number) => void;
  drag: {
    onDragStart: () => void;
    onDragEnter: () => void;
    onDragEnd: () => void;
    dragging: boolean;
  };
}) {
  const streaming = isStreaming(camera, mediaUp);
  const chip = streaming ? LIVE_CHIP.online : camera.status === "degraded" ? LIVE_CHIP.degraded : LIVE_CHIP.offline;
  const flagged = alert && !alert.ack;

  return (
    <motion.figure
      layout
      layoutId={`tile-${camera.id}`}
      initial={{ scale: 0.96 }}
      animate={{ scale: drag.dragging ? 0.97 : 1 }}
      exit={{ scale: 0.96, transition: transition.fast }}
      transition={transition.spring}
      className={`video-well group aspect-video rounded-xl ring-1 transition-shadow ${
        flagged ? (alert.severity === "bad" ? "ring-2 ring-bad/70" : "ring-2 ring-warn/70") : "ring-line"
      } ${drag.dragging ? "shadow-lift" : "hover:shadow-lift"}`}
    >
      {/* native drag and drop lives on a plain element: on motion elements the
          onDrag* props belong to Motion's own gesture system */}
      <div
        className="absolute inset-0"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", camera.id);
          drag.onDragStart();
        }}
        onDragEnter={drag.onDragEnter}
        onDragOver={(e) => e.preventDefault()}
        onDragEnd={drag.onDragEnd}
      >
      {/* the player is not interactive on the wall: a click focuses the tile */}
      <div className="pointer-events-none absolute inset-0">
        <StreamView camera={camera} mediaUp={mediaUp} compact />
      </div>

      {/* the load the counting cameras are watching */}
      {load && COUNTING.includes(camera.role) && !compact && (
        <div className="pointer-events-none absolute left-2 top-2 z-[3] flex items-center gap-1.5 rounded-md bg-black/60 px-2 py-1 text-[11px] font-semibold text-white backdrop-blur">
          <span className="dot h-1.5 w-1.5 animate-pulse bg-accent" />
          <AnimatedNumber value={load.ai_count} className="num" /> crates
          {load.plate && <span className="num text-white/70">· {load.plate}</span>}
        </div>
      )}

      <figcaption className="pointer-events-none absolute inset-x-0 bottom-0 z-[3] flex items-center justify-between gap-2 bg-gradient-to-t from-black/80 to-transparent px-2.5 pb-2 pt-6">
        <span className="truncate text-xs font-semibold text-white">{camera.name}</span>
        <span className={`chip flex-none px-1.5 py-0 text-[10px] ${chip.cls}`}>
          {streaming && <span className="dot h-1.5 w-1.5 animate-pulse bg-white" />}
          {chip.label}
        </span>
      </figcaption>

      <button
        className="absolute inset-0 z-[2] rounded-xl"
        aria-label={`Focus ${camera.name}`}
        onClick={onFocus}
      />
      <button
        className="absolute right-2 top-2 z-[4] grid h-7 w-7 cursor-grab place-items-center rounded-md bg-black/50 text-white/80 opacity-0 backdrop-blur transition hover:bg-black/70 focus-visible:opacity-100 group-hover:opacity-100 active:cursor-grabbing"
        aria-label={`Move ${camera.name}, position ${position} of ${total}. Use the arrow keys to move it.`}
        title="Drag to reorder, or use the arrow keys"
        onKeyDown={(e) => {
          const delta = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key];
          if (delta) {
            e.preventDefault();
            onMove(delta);
          }
        }}
      >
        <GripVertical size={14} />
      </button>
      </div>
    </motion.figure>
  );
}

/** One camera, large, with what the platform knows about it alongside. */
function Focused({
  camera,
  mediaUp,
  alerts,
  load,
  onClose,
}: {
  camera: Camera;
  mediaUp: boolean | undefined;
  alerts: Alert[];
  load: Session | undefined;
  onClose: () => void;
}) {
  const streaming = isStreaming(camera, mediaUp);
  const chip = streaming ? LIVE_CHIP.online : camera.status === "degraded" ? LIVE_CHIP.degraded : LIVE_CHIP.offline;
  // "last frame 3 min ago" has to keep moving while it is on screen
  const [, tick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => tick((n) => n + 1), 30_000);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const facts: [string, string][] = [
    ["Position", roleLabel(camera.role)],
    ["Status", camera.status],
    ["Last frame", ago(camera.last_seen_at)],
    ["Protocol", camera.protocol.toUpperCase()],
    ["Stream", camera.stream_path],
  ];

  return (
    <section aria-label={`${camera.name}, focused`} className="mb-4 grid gap-3 xl:grid-cols-[minmax(0,1fr)_320px]">
      <motion.div
        layoutId={`tile-${camera.id}`}
        transition={transition.spring}
        className="video-well scanlines aspect-video w-full rounded-xl ring-1 ring-accent/60"
      >
        <StreamView camera={camera} mediaUp={mediaUp} />
        <div aria-hidden className="hud-sweep" />
        <Reticle />
        <div className="pointer-events-none absolute inset-x-0 top-0 z-[4] flex items-start justify-between gap-2 p-4">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={`chip tracking-[0.08em] ${chip.cls}`}>
              {streaming && <span className="dot animate-pulse bg-white" />}
              {chip.label}
            </span>
            <span className="chip bg-black/55 text-white/85 backdrop-blur">{camera.name}</span>
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Close focused view"
          className="absolute right-3 top-3 z-[5] grid h-8 w-8 place-items-center rounded-lg bg-black/55 text-white/85 backdrop-blur transition hover:bg-black/75"
        >
          <X size={16} />
        </button>
      </motion.div>

      <motion.aside
        initial={{ x: 32 }}
        animate={{ x: 0 }}
        transition={transition.spring}
        className="card flex min-w-0 flex-col"
      >
        <div className="panel-head">
          <h2 className="panel-title truncate">{camera.name}</h2>
          <span className={`chip py-0 text-[10.5px] ${streaming ? "bg-good/10 text-good" : "bg-bad/10 text-bad"}`}>
            {streaming ? "Streaming" : "Not streaming"}
          </span>
        </div>
        <dl className="divide-y divide-line text-sm">
          {facts.map(([k, v], i) => (
            <motion.div
              key={k}
              initial={{ x: 12 }}
              animate={{ x: 0 }}
              transition={{ ...transition.normal, delay: 0.05 + i * 0.03 }}
              className="flex items-center justify-between gap-3 px-4 py-2"
            >
              <dt className="text-xs text-muted">{k}</dt>
              <dd className={`num truncate text-right text-xs ${k === "Status" ? "capitalize" : ""} text-ink`}>{v}</dd>
            </motion.div>
          ))}
        </dl>

        <div className="border-t border-line px-4 py-3">
          <div className="eyebrow">Load at the bay</div>
          {load ? (
            <div className="mt-1 flex items-baseline gap-2">
              <AnimatedNumber value={load.ai_count} className="num text-2xl font-bold text-ink" />
              <span className="text-xs text-muted">
                crates · {load.plate ?? "plate not read"} · since {time(load.opened_at)}
              </span>
            </div>
          ) : (
            <p className="mt-1 text-sm text-muted">Bay clear. No session is open.</p>
          )}
        </div>

        <div className="border-t border-line px-4 py-3">
          <div className="flex items-center justify-between">
            <span className="eyebrow">Alerts on this camera</span>
            <Link to="/alerts" className="text-xs font-semibold text-brand hover:underline">
              All alerts →
            </Link>
          </div>
          {alerts.length ? (
            <ul className="mt-2 space-y-2">
              {alerts.map((a) => (
                <li key={a.key} className="flex gap-2 text-xs">
                  <AlertTriangle size={13} className={`mt-0.5 flex-none ${a.severity === "bad" ? "text-bad" : "text-warn"}`} />
                  <span>
                    <span className="font-semibold text-ink">{a.title}</span>{" "}
                    <span className="text-muted">
                      {SEVERITY_LABEL[a.severity]}
                      {a.ack ? ` · acknowledged by ${a.ack.acknowledged_by}` : ""}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm text-muted">None.</p>
          )}
        </div>
        <p className="mt-auto border-t border-line px-4 py-2.5 text-[11px] text-faint">Esc closes this view.</p>
      </motion.aside>
    </section>
  );
}

export default function LiveView() {
  const [layout, setLayout] = useState<Layout>("standard");
  const [role, setRole] = useState<CameraRole | "all">("all");
  const [onlyAttention, setOnlyAttention] = useState(false);
  const [focused, setFocused] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [said, setSaid] = useState("");

  const bayId = useScope().bay?.id;
  const cameras = useQuery({
    queryKey: ["cameras", bayId],
    queryFn: () => api.cameras(bayId!),
    enabled: !!bayId,
  });
  const sessions = useQuery({
    queryKey: ["sessions", bayId],
    queryFn: () => api.sessions(bayId),
    enabled: !!bayId,
  });
  const mediaUp = useMediaServerUp();
  const alerts = useAlerts();

  const all = useMemo(() => cameras.data ?? [], [cameras.data]);
  const wall = useWallOrder(bayId, all);
  const load = sessions.data?.find((s) => s.status === "open");
  const alertFor = (id: string) => alerts.all.filter((a) => a.cameraId === id);
  const online = all.filter((c) => c.status === "online").length;
  const shown = wall.sorted.filter(
    (c) => c.id !== focused && (role === "all" || c.role === role) && (!onlyAttention || c.status !== "online"),
  );
  const roles = ROLE_ORDER.filter((r) => all.some((c) => c.role === r));
  const focusedCamera = all.find((c) => c.id === focused);
  const lastEnter = useRef<string | null>(null);

  const moveBy = (c: Camera, delta: number) => {
    const ids = wall.sorted.map((x) => x.id);
    const to = ids.indexOf(c.id) + delta;
    if (to < 0 || to >= ids.length) return;
    wall.move(c.id, to);
    setSaid(`${c.name} moved to position ${to + 1} of ${ids.length}`);
  };

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Camera wall</h1>
          <p className="mt-0.5 text-sm text-muted">
            <span className={`num font-bold ${online ? "text-good" : "text-bad"}`}>
              {online}/{all.length}
            </span>{" "}
            cameras streaming
            {mediaUp === false && " · media server unreachable"}
            {load && (
              <>
                {" · "}
                <span className="text-ink">
                  load at the bay: <AnimatedNumber value={load.ai_count} className="num font-bold" /> crates
                </span>
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {wall.custom && (
            <button className="btn-ghost btn-sm" onClick={() => wall.reset()} title="Back to the order cameras were added in">
              <RotateCcw size={13} /> Reset order
            </button>
          )}
          <Segmented<Layout>
            label="Wall density"
            value={layout}
            onChange={setLayout}
            options={(Object.keys(LAYOUTS) as Layout[]).map((k) => {
              const { icon: Icon, label } = LAYOUTS[k];
              return { value: k, title: label, label: <Icon size={14} /> };
            })}
          />
        </div>
      </div>

      {/* filters */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="overflow-x-auto">
          <Segmented<CameraRole | "all">
            label="Camera position"
            value={role}
            onChange={setRole}
            options={[
              { value: "all", label: "All", count: all.length },
              ...roles.map((r) => ({ value: r, label: roleLabel(r), count: all.filter((c) => c.role === r).length })),
            ]}
          />
        </div>
        <button
          onClick={() => setOnlyAttention((v) => !v)}
          aria-pressed={onlyAttention}
          className={`chip h-8 px-3 transition ${
            onlyAttention ? "bg-warn text-white" : "bg-surface text-muted ring-1 ring-line hover:text-ink"
          }`}
        >
          <AlertTriangle size={12} /> Needs attention
          <span className="num">{all.length - online}</span>
        </button>
      </div>

      <p className="sr-only" aria-live="polite">
        {said}
      </p>

      <LayoutGroup>
        {/* no exit wrapper: the shared layoutId already carries the view back into its
            tile, and an exit that waits on that animation can strand the panel */}
        {focusedCamera && (
          <Focused
            key={focusedCamera.id}
            camera={focusedCamera}
            mediaUp={mediaUp}
            alerts={alertFor(focusedCamera.id)}
            load={COUNTING.includes(focusedCamera.role) ? load : undefined}
            onClose={() => setFocused(null)}
          />
        )}

        {shown.length ? (
          <div className={`grid gap-2.5 ${LAYOUTS[layout].cols}`}>
            <AnimatePresence mode="popLayout" initial={false}>
              {shown.map((c) => (
                <Tile
                  key={c.id}
                  camera={c}
                  mediaUp={mediaUp}
                  compact={layout === "compact"}
                  alert={alertFor(c.id)[0]}
                  load={load}
                  position={wall.sorted.indexOf(c) + 1}
                  total={wall.sorted.length}
                  onFocus={() => setFocused(c.id)}
                  onMove={(d) => moveBy(c, d)}
                  drag={{
                    dragging: dragging === c.id,
                    onDragStart: () => setDragging(c.id),
                    onDragEnter: () => {
                      // follow the pointer across tiles, once per tile entered
                      if (!dragging || dragging === c.id || lastEnter.current === c.id) return;
                      lastEnter.current = c.id;
                      wall.move(dragging, wall.sorted.findIndex((x) => x.id === c.id));
                    },
                    onDragEnd: () => {
                      lastEnter.current = null;
                      setDragging(null);
                    },
                  }}
                />
              ))}
            </AnimatePresence>
          </div>
        ) : (
          !focusedCamera && (
            <div className="card">
              <EmptyState
                title={all.length ? "No cameras match this filter" : "No cameras registered"}
                body={all.length ? "Clear the filter to see the rest of the wall." : "Add cameras to this bay to see their feeds here."}
              />
            </div>
          )
        )}
      </LayoutGroup>

      {focusedCamera && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-faint">
          <Maximize2 size={12} /> Choose another tile to switch the focused camera.
        </p>
      )}
    </>
  );
}
