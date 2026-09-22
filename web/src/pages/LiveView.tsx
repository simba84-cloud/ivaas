import { useQuery } from "@tanstack/react-query";
import { VideoOff } from "lucide-react";
import { api } from "../api/client";
import type { Camera, CameraRole } from "../api/types";
import { CameraDot, PageHeader, roleLabel } from "../components/ui";

const ROLE_ORDER: CameraRole[] = [
  "chokepoint",
  "overhead",
  "side_high",
  "side_mid",
  "side_low",
  "lpr",
];

// MediaMTX serves each path as a WebRTC (WHEP) player page on :8889.
const MEDIA_BASE = import.meta.env.VITE_MEDIA_URL ?? "http://localhost:8889";

/** An iframe cannot report a refused connection, so probe the media server once. */
function useMediaServerUp(): boolean | undefined {
  const probe = useQuery({
    queryKey: ["media-server"],
    queryFn: () =>
      fetch(MEDIA_BASE, { mode: "no-cors" }).then(
        () => true,
        () => false,
      ),
    refetchInterval: 30_000,
  });
  return probe.data;
}

function Tile({ camera, mediaUp }: { camera: Camera; mediaUp: boolean | undefined }) {
  return (
    <div className="card overflow-hidden">
      <div className="relative aspect-video bg-slate-900">
        {camera.status === "online" && mediaUp ? (
          <iframe
            title={camera.name}
            src={`${MEDIA_BASE}/${camera.stream_path}`}
            className="h-full w-full"
            allow="autoplay"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-slate-500">
            <VideoOff size={28} />
            <span className="text-xs">
              {camera.status === "online" && mediaUp === false
                ? "Media server unreachable"
                : "No signal"}
            </span>
          </div>
        )}
      </div>
      <div className="flex items-center justify-between px-3 py-2">
        <span className="truncate text-sm font-medium text-brand-navy">{camera.name}</span>
        <CameraDot status={camera.status} />
      </div>
    </div>
  );
}

export default function LiveView() {
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const bayId = bays.data?.[0]?.id;
  const cameras = useQuery({
    queryKey: ["cameras", bayId],
    queryFn: () => api.cameras(bayId!),
    enabled: !!bayId,
  });

  const mediaUp = useMediaServerUp();

  return (
    <>
      <PageHeader title="Live View" subtitle="All camera feeds for the POC loading bay, by position" />
      {ROLE_ORDER.map((role) => {
        const group = cameras.data?.filter((c) => c.role === role) ?? [];
        if (!group.length) return null;
        return (
          <section key={role} className="mb-8">
            <div className="mb-3 flex items-center gap-3">
              <h2 className="text-sm font-bold uppercase tracking-wide text-brand-navy">
                {roleLabel(role)}
              </h2>
              <span className="rounded-full bg-brand-navy-tint px-2 py-0.5 text-xs font-semibold text-brand-navy">
                {group.length}
              </span>
              <div className="h-px flex-1 bg-slate-200" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {group.map((c) => (
                <Tile key={c.id} camera={c} mediaUp={mediaUp} />
              ))}
            </div>
          </section>
        );
      })}
    </>
  );
}
