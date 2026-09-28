/**
 * Pieces of the command view's video wells. Wells are dark in both themes, so their
 * chrome uses white/black literals, as the camera wall does.
 */
import { VideoOff } from "lucide-react";
import { useEffect, useState } from "react";
import { MEDIA_BASE } from "../../api/media";
import type { Camera } from "../../api/types";

export const isStreaming = (c: Camera | undefined, mediaUp: boolean | undefined) =>
  c?.status === "online" && mediaUp !== false;

/** Why a well is dark, in the words the operator needs. */
function darkReason(camera: Camera | undefined, mediaUp: boolean | undefined): string {
  if (!camera) return "No camera selected";
  if (camera.status === "online" && mediaUp === false) return "Media server unreachable";
  if (camera.status === "degraded") return "Signal degraded";
  return "No signal";
}

/** The feed itself, or an honest no-signal state: never a frozen or invented picture. */
export function StreamView({
  camera,
  mediaUp,
  compact = false,
}: {
  camera: Camera | undefined;
  mediaUp: boolean | undefined;
  compact?: boolean;
}) {
  if (camera && isStreaming(camera, mediaUp)) {
    return (
      <iframe
        key={camera.id}
        title={camera.name}
        src={`${MEDIA_BASE}/${camera.stream_path}`}
        className="absolute inset-0 h-full w-full"
        allow="autoplay"
        loading="lazy"
      />
    );
  }
  const last = camera?.last_seen_at
    ? new Date(camera.last_seen_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;
  return (
    <div className="absolute inset-0 overflow-hidden">
      <div aria-hidden className="no-signal" />
      <div className="absolute inset-0 grid place-items-center text-center">
        <div>
          <VideoOff size={compact ? 18 : 30} className="mx-auto text-white/35" />
          {!compact && (
            <>
              <p className="num mt-3 text-xs font-bold uppercase tracking-[0.18em] text-white/75">
                {darkReason(camera, mediaUp)}
              </p>
              {camera && (
                <p className="mt-1 text-xs text-white/45">
                  {last ? `Last frame at ${last}` : "No frame received yet"}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** Corner brackets that frame the main feed like a viewfinder. */
export function Reticle() {
  const corner = "absolute h-5 w-5 border-white/35";
  return (
    <div aria-hidden className="pointer-events-none absolute inset-4 z-[3] hidden sm:block">
      <i className={`${corner} left-0 top-0 border-l-[1.5px] border-t-[1.5px]`} />
      <i className={`${corner} right-0 top-0 border-r-[1.5px] border-t-[1.5px]`} />
      <i className={`${corner} bottom-0 right-0 border-b-[1.5px] border-r-[1.5px]`} />
      <i className={`${corner} bottom-0 left-0 border-b-[1.5px] border-l-[1.5px]`} />
    </div>
  );
}

/** Ticks on its own so the rest of the page does not re-render every second. */
export function Clock({ className = "" }: { className?: string }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <span className={`num ${className}`}>
      {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
    </span>
  );
}

export const LIVE_CHIP = {
  online: { label: "LIVE", cls: "bg-bad/90 text-white" },
  degraded: { label: "DEGRADED", cls: "bg-warn/90 text-black" },
  offline: { label: "OFFLINE", cls: "bg-white/15 text-white/75" },
} as const;
