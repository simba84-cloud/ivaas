import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { type Me, hasRole } from "../auth/session";
import { AddCamera } from "../components/AddCamera";
import { CameraDot, EmptyState, PageHeader, dateTime, roleLabel } from "../components/ui";

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
  const remove = useMutation({
    mutationFn: api.removeCamera,
    onSuccess: () => {
      setConfirming(null);
      qc.invalidateQueries({ queryKey: ["cameras"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
    },
  });

  return (
    <>
      <PageHeader
        title="Cameras"
        subtitle="Any make or model: connect by stream URL or find devices over ONVIF"
        actions={
          !adding &&
          isAdmin && (
            <button className="btn-accent" disabled={!bayId} onClick={() => setAdding(true)}>
              <Plus size={16} /> Add camera
            </button>
          )
        }
      />
      {adding && bayId && <AddCamera bayId={bayId} onClose={() => setAdding(false)} />}
      <div className="card overflow-hidden">
        {cameras.data?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-brand-navy-tint/60">
                <tr>
                  <th className="th">Camera</th>
                  <th className="th">Position</th>
                  <th className="th">Protocol</th>
                  <th className="th">Source</th>
                  <th className="th">Status</th>
                  <th className="th">Last seen</th>
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {cameras.data.map((c) => (
                  <tr key={c.id} className="border-t border-slate-100">
                    <td className="td font-semibold text-brand-navy">{c.name}</td>
                    <td className="td">{roleLabel(c.role)}</td>
                    <td className="td">
                      <span className="rounded bg-brand-navy-tint px-2 py-0.5 text-xs font-semibold uppercase text-brand-navy">
                        {c.protocol}
                      </span>
                    </td>
                    <td className="td max-w-xs truncate font-mono text-xs text-slate-500">
                      {c.source_url ?? `publish to ${c.stream_path}`}
                    </td>
                    <td className="td">
                      <span className="inline-flex items-center gap-2 capitalize">
                        <CameraDot status={c.status} />
                        {c.status}
                      </span>
                    </td>
                    <td className="td text-slate-500">
                      {c.last_seen_at ? dateTime(c.last_seen_at) : "Never"}
                    </td>
                    <td className="td text-right">
                      {!isAdmin ? null : confirming === c.id ? (
                        <span className="inline-flex items-center gap-2 text-xs">
                          <button
                            className="font-semibold text-red-600 hover:underline"
                            disabled={remove.isPending}
                            onClick={() => remove.mutate(c.id)}
                          >
                            Remove
                          </button>
                          <button className="text-slate-500" onClick={() => setConfirming(null)}>
                            Keep
                          </button>
                        </span>
                      ) : (
                        <button
                          aria-label={`Remove ${c.name}`}
                          className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600"
                          onClick={() => setConfirming(c.id)}
                        >
                          <Trash2 size={16} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No cameras registered" body="Add cameras to this bay to begin." />
        )}
      </div>
    </>
  );
}
