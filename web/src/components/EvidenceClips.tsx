import { useQuery } from "@tanstack/react-query";
import { Film } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { EvidenceClip } from "../api/types";
import { dateTime, time } from "./ui";

const KIND: Record<EvidenceClip["kind"], string> = { crossing: "Crossing", plate: "Number plate" };

/**
 * The load's evidence: video of each counted crossing and of the plate read, as the
 * camera saw it. What a disputed count is checked against. A load with none says so
 * and why, rather than showing an empty box.
 */
export function EvidenceClips({ sessionId }: { sessionId: string }) {
  const clips = useQuery({
    queryKey: ["evidence", sessionId],
    queryFn: () => api.sessionEvidence(sessionId),
  });
  const [playing, setPlaying] = useState<string | null>(null);

  if (clips.isPending) return <div className="text-xs text-muted">Loading evidence…</div>;
  if (clips.isError) {
    return <div className="text-xs text-bad">Could not load evidence: {(clips.error as Error).message}</div>;
  }
  const rows = clips.data ?? [];
  if (!rows.length) {
    return (
      <div className="text-xs text-muted">
        No evidence clips for this load. Clips are recorded only from the chokepoint and plate
        cameras, by an enrolled edge node, and arrive within about a minute of each count.
      </div>
    );
  }
  const current = rows.find((c) => c.id === playing);
  return (
    <div>
      <div className="eyebrow mb-1.5">Evidence · {rows.length} clip{rows.length === 1 ? "" : "s"}</div>
      <div className="flex flex-wrap gap-1.5">
        {rows.map((c) => (
          <button
            key={c.id}
            className={`btn-ghost btn-sm ${playing === c.id ? "ring-1 ring-brand" : ""}`}
            onClick={() => setPlaying(playing === c.id ? null : c.id)}
            aria-pressed={playing === c.id}
          >
            <Film size={13} /> {KIND[c.kind]} {time(c.started_at)} · {Math.round(c.seconds)} s
          </button>
        ))}
      </div>
      {current && (
        <div className="mt-2 max-w-2xl">
          {/* a video well: deliberately single-theme, like every video surface */}
          <video
            key={current.id}
            src={current.url}
            controls
            autoPlay
            muted
            className="w-full rounded-md bg-black"
            aria-label={`${KIND[current.kind]} at ${time(current.started_at)}`}
          />
          <div className="num mt-1 text-[11px] text-faint">
            SHA-256 {current.sha256.slice(0, 16)}… · kept until {dateTime(current.expires_at)}
          </div>
        </div>
      )}
    </div>
  );
}
