import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { Camera, CameraRole } from "../api/types";
import { type Me, hasRole } from "../auth/session";
import { AddCamera } from "../components/AddCamera";
import { CameraDot, EmptyState, dateTime, roleLabel } from "../components/ui";

const ROLE_ORDER: CameraRole[] = [
  "chokepoint",
  "overhead",
  "side_high",
  "side_mid",
  "side_low",
  "lpr",
];

/** Fleet state in one line, so the table below is detail rather than discovery. */
function FleetBar({ cameras }: { cameras: Camera[] }) {
  const online = cameras.filter((c) => c.status === "online").length;
  const degraded = cameras.filter((c) => c.status === "degraded").length;
  const offline = cameras.length - online - degraded;
  const protocols = [...new Set(cameras.map((c) => c.protocol))];

  const stat = (label: string, value: number, tone: string) => (
    <div className="flex items-baseline gap-2">
      <span className={`num text-xl font-bold ${tone}`}>{value}</span>
      <span className="eyebrow">{label}</span>
    </div>
  );

  return (
    <div className="card mb-4 flex flex-wrap items-center gap-x-8 gap-y-3 px-4 py-3">
      {stat("Registered", cameras.length, "text-ink")}
      {stat("Streaming", online, online ? "text-good" : "text-faint")}
      {degraded > 0 && stat("Degraded", degraded, "text-warn")}
      {stat("Offline", offline, offline ? "text-bad" : "text-faint")}
      <div className="ml-auto flex items-center gap-1.5">
        <span className="eyebrow">Protocols</span>
        {protocols.map((p) => (
          <span key={p} className="chip bg-brand-tint text-brand">
            {p.toUpperCase()}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function Cameras({ me }: { me: Me | undefined }) {
  const isAdmin = hasRole(me, "admin");
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const bayId = bays.data?.[0]?.id;
  const cameras = useQuery({
    queryKey: ["cameras", bayId],
    queryFn: () => api.cameras(bayId!),
    enabled: !!bayId,
  });

  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const remove = useMutation({
    mutationFn: api.removeCamera,
    onSuccess: () => {
      setConfirming(null);
      qc.invalidateQueries({ queryKey: ["cameras"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });

  const all = cameras.data ?? [];
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? all.filter(
        (c) =>
          c.name.toLowerCase().includes(needle) ||
          roleLabel(c.role).toLowerCase().includes(needle) ||
          (c.source_url ?? c.stream_path).toLowerCase().includes(needle),
      )
    : all;
  const groups = ROLE_ORDER.map((role) => ({
    role,
    rows: shown.filter((c) => c.role === role),
  })).filter((g) => g.rows.length);

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Cameras</h1>
          <p className="mt-0.5 text-sm text-muted">
            Any make or model: connect by stream URL or find devices over ONVIF
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search
              size={14}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
            />
            <input
              id="camera-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search cameras"
              aria-label="Search cameras"
              className="input w-56 pl-8"
            />
          </div>
          {!adding && isAdmin && (
            <button className="btn-accent" disabled={!bayId} onClick={() => setAdding(true)}>
              <Plus size={16} /> Add camera
            </button>
          )}
        </div>
      </div>

      {adding && bayId && <AddCamera bayId={bayId} onClose={() => setAdding(false)} />}

      {all.length > 0 && <FleetBar cameras={all} />}

      <div className="card overflow-hidden">
        {groups.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Camera</th>
                  <th className="th">Protocol</th>
                  <th className="th">Source</th>
                  <th className="th">Status</th>
                  <th className="th">Last seen</th>
                  <th className="th" />
                </tr>
              </thead>
              {groups.map(({ role, rows }) => (
                <tbody key={role} className="border-b border-line last:border-0">
                  {/* position is a property of the group, so it is stated once, not per row */}
                  <tr className="bg-ground">
                    <td className="px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.12em] text-muted" colSpan={6}>
                      {roleLabel(role)}
                      <span className="num ml-2 text-faint">{rows.length}</span>
                    </td>
                  </tr>
                  {rows.map((c) => (
                    <tr key={c.id} className="border-t border-line transition hover:bg-ground/50">
                      <td className="td font-semibold text-ink">{c.name}</td>
                      <td className="td">
                        <span className="chip bg-brand-tint text-brand">
                          {c.protocol.toUpperCase()}
                        </span>
                      </td>
                      <td className="td num max-w-sm truncate text-xs text-muted">
                        {c.source_url ?? `publish to ${c.stream_path}`}
                      </td>
                      <td className="td">
                        <span className="inline-flex items-center gap-1.5 capitalize">
                          <CameraDot status={c.status} />
                          <span className={c.status === "online" ? "text-good" : "text-muted"}>
                            {c.status}
                          </span>
                        </span>
                      </td>
                      <td className="td text-muted">
                        {c.last_seen_at ? dateTime(c.last_seen_at) : "Never"}
                      </td>
                      <td className="td text-right">
                        {!isAdmin ? null : confirming === c.id ? (
                          <span className="inline-flex items-center gap-2 text-xs">
                            <button
                              className="font-semibold text-bad hover:underline"
                              disabled={remove.isPending}
                              onClick={() => remove.mutate(c.id)}
                            >
                              Remove
                            </button>
                            <button className="text-muted" onClick={() => setConfirming(null)}>
                              Keep
                            </button>
                          </span>
                        ) : (
                          <button
                            aria-label={`Remove ${c.name}`}
                            className="rounded p-1 text-faint transition hover:bg-bad/10 hover:text-bad"
                            onClick={() => setConfirming(c.id)}
                          >
                            <Trash2 size={15} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              ))}
            </table>
          </div>
        ) : (
          <EmptyState
            title={all.length ? "No cameras match that search" : "No cameras registered"}
            body={
              all.length
                ? "Try a different name, position or address."
                : "Add cameras to this bay to begin."
            }
          />
        )}
      </div>
    </>
  );
}
