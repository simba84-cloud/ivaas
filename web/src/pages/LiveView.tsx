import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Grid2x2, Grid3x3, LayoutGrid, VideoOff } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { Camera, CameraRole } from "../api/types";
import { MEDIA_BASE, useMediaServerUp } from "../api/media";
import { EmptyState, roleLabel } from "../components/ui";

const ROLE_ORDER: CameraRole[] = [
  "chokepoint",
  "overhead",
  "side_high",
  "side_mid",
  "side_low",
  "lpr",
];

/** Wall density, the way a VMS offers it: fewer, larger tiles or more, smaller ones. */
const LAYOUTS = {
  large: { cols: "sm:grid-cols-2 xl:grid-cols-3", icon: Grid2x2, label: "Large tiles" },
  standard: {
    cols: "sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4",
    icon: Grid3x3,
    label: "Standard",
  },
  compact: {
    cols: "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6",
    icon: LayoutGrid,
    label: "Compact",
  },
} as const;
type Layout = keyof typeof LAYOUTS;

function Tile({
  camera,
  mediaUp,
  compact,
}: {
  camera: Camera;
  mediaUp: boolean | undefined;
  compact: boolean;
}) {
  const streaming = camera.status === "online" && mediaUp;
  return (
    <figure className="video-well group aspect-video rounded-xl ring-1 ring-line">
      {streaming ? (
        <iframe
          title={camera.name}
          src={`${MEDIA_BASE}/${camera.stream_path}`}
          className="absolute inset-0 h-full w-full"
          allow="autoplay"
        />
      ) : (
        <div className="absolute inset-0 grid place-items-center text-center">
          <div>
            <VideoOff size={compact ? 18 : 24} className="mx-auto text-white/30" />
            {!compact && (
              <p className="mt-2 text-xs text-white/50">
                {camera.status === "online" && mediaUp === false
                  ? "Media server unreachable"
                  : "No signal"}
              </p>
            )}
          </div>
        </div>
      )}

      {/* chrome over the well is always light: the well is dark in both themes */}
      <figcaption className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-between gap-2 bg-gradient-to-t from-black/80 to-transparent px-2.5 pb-2 pt-6">
        <span className="truncate text-xs font-semibold text-white">{camera.name}</span>
        <span
          className={`chip flex-none px-1.5 py-0 text-[10px] ${
            streaming ? "bg-bad/90 text-white" : "bg-white/15 text-white/70"
          }`}
        >
          {streaming && <span className="dot h-1.5 w-1.5 animate-pulse bg-white" />}
          {streaming ? "LIVE" : camera.status === "degraded" ? "DEGRADED" : "OFFLINE"}
        </span>
      </figcaption>
    </figure>
  );
}

export default function LiveView() {
  const [layout, setLayout] = useState<Layout>("standard");
  const [role, setRole] = useState<CameraRole | "all">("all");
  const [onlyOffline, setOnlyOffline] = useState(false);

  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const bayId = bays.data?.[0]?.id;
  const cameras = useQuery({
    queryKey: ["cameras", bayId],
    queryFn: () => api.cameras(bayId!),
    enabled: !!bayId,
  });
  const mediaUp = useMediaServerUp();

  const all = cameras.data ?? [];
  const online = all.filter((c) => c.status === "online").length;
  const shown = all.filter(
    (c) => (role === "all" || c.role === role) && (!onlyOffline || c.status !== "online"),
  );
  const roles = ROLE_ORDER.filter((r) => all.some((c) => c.role === r));

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
          </p>
        </div>
        <div className="segment" role="group" aria-label="Wall density">
          {(Object.keys(LAYOUTS) as Layout[]).map((key) => {
            const { icon: Icon, label } = LAYOUTS[key];
            return (
              <button
                key={key}
                onClick={() => setLayout(key)}
                aria-pressed={layout === key}
                title={label}
                aria-label={label}
                className={`segment-item ${layout === key ? "segment-item-on" : ""}`}
              >
                <Icon size={14} />
              </button>
            );
          })}
        </div>
      </div>

      {/* filters */}
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        <button
          onClick={() => setRole("all")}
          className={`chip h-7 px-2.5 ${
            role === "all" ? "bg-ink text-surface" : "bg-surface text-muted hover:text-ink"
          }`}
        >
          All <span className="num">{all.length}</span>
        </button>
        {roles.map((r) => (
          <button
            key={r}
            onClick={() => setRole(r)}
            className={`chip h-7 px-2.5 ${
              role === r ? "bg-ink text-surface" : "bg-surface text-muted hover:text-ink"
            }`}
          >
            {roleLabel(r)} <span className="num">{all.filter((c) => c.role === r).length}</span>
          </button>
        ))}
        <span className="mx-1 h-4 w-px bg-line" />
        <button
          onClick={() => setOnlyOffline((v) => !v)}
          aria-pressed={onlyOffline}
          className={`chip h-7 px-2.5 ${
            onlyOffline ? "bg-warn text-white" : "bg-surface text-muted hover:text-ink"
          }`}
        >
          <AlertTriangle size={12} /> Needs attention
          <span className="num">{all.length - online}</span>
        </button>
      </div>

      {shown.length ? (
        <div className={`grid gap-2.5 ${LAYOUTS[layout].cols}`}>
          {shown.map((c) => (
            <Tile key={c.id} camera={c} mediaUp={mediaUp} compact={layout === "compact"} />
          ))}
        </div>
      ) : (
        <div className="card">
          <EmptyState
            title={all.length ? "No cameras match this filter" : "No cameras registered"}
            body={
              all.length
                ? "Clear the filter to see the rest of the wall."
                : "Add cameras to this bay to see their feeds here."
            }
          />
        </div>
      )}
    </>
  );
}
