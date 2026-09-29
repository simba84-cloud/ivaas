/**
 * Incidents with their evidence. Each card shows the snapshot the edge node took, what
 * was seen, where and when; an operator acknowledges it, then resolves it with what
 * was found. Nothing is ever deleted.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ImageOff, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import { useScope } from "../../api/scope";
import type { Incident, IncidentKind, IncidentStatus } from "../../api/types";
import { INCIDENT_TITLE } from "../../live/activity";
import { Rise, Segmented, Skeleton, transition } from "../../motion";
import { useToast } from "../toast";
import { EmptyState, dateTime } from "../ui";

const CRITICAL: IncidentKind[] = ["fire", "smoke", "intrusion"];
const KINDS: IncidentKind[] = ["intrusion", "unbadged", "no_ppe", "unknown_face", "fire", "smoke"];

const STATUS_CHIP: Record<IncidentStatus, string> = {
  open: "bg-bad/10 text-bad",
  acknowledged: "bg-warn/10 text-warn",
  resolved: "bg-good/10 text-good",
};

function IncidentCard({
  incident,
  index,
  cameraName,
  canAct,
}: {
  incident: Incident;
  index: number;
  cameraName: string;
  canAct: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [resolving, setResolving] = useState(false);
  const [note, setNote] = useState("");
  const critical = CRITICAL.includes(incident.kind);
  const done = () => {
    qc.invalidateQueries({ queryKey: ["incidents"] });
    qc.invalidateQueries({ queryKey: ["audit"] });
  };
  const ack = useMutation({
    mutationFn: () => api.acknowledgeIncident(incident.id),
    onSuccess: () => {
      toast({ severity: "success", title: "Acknowledged", detail: INCIDENT_TITLE[incident.kind] });
      done();
    },
    onError: (e) => toast({ severity: "critical", title: "Not acknowledged", detail: (e as Error).message }),
  });
  const resolve = useMutation({
    mutationFn: () => api.resolveIncident(incident.id, note),
    onSuccess: () => {
      toast({ severity: "success", title: "Resolved", detail: note });
      setResolving(false);
      done();
    },
    onError: (e) => toast({ severity: "critical", title: "Not resolved", detail: (e as Error).message }),
  });

  return (
    <Rise as="article" index={index} className="card card-hover flex min-w-0 flex-col overflow-hidden">
      {/* the evidence: a video well, dark in both themes */}
      <div className="video-well aspect-video">
        {incident.snapshot_url ? (
          <img
            src={incident.snapshot_url}
            alt={`Snapshot: ${INCIDENT_TITLE[incident.kind]} in ${incident.zone_name ?? "a zone"}`}
            className="absolute inset-0 h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-white/45">
            <ImageOff size={22} />
          </div>
        )}
        <span
          className={`chip absolute left-2.5 top-2.5 ${critical ? "bg-bad/90 text-white" : "bg-warn/90 text-black"}`}
        >
          {critical && incident.status === "open" && <span className="dot ping relative bg-white text-white" />}
          {critical ? "Critical" : "Warning"}
        </span>
        <span className="chip num absolute right-2.5 top-2.5 bg-black/60 text-white/85 backdrop-blur">
          {Math.round(incident.confidence * 100)}%
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-1 p-3.5">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-sm font-bold text-ink">{INCIDENT_TITLE[incident.kind]}</h3>
          <span className={`chip flex-none py-0 text-[10.5px] capitalize ${STATUS_CHIP[incident.status]}`}>
            {incident.status}
          </span>
        </div>
        <p className="text-xs text-muted">
          {incident.zone_name ?? "No zone"} · {cameraName}
        </p>
        <p className="num text-xs text-faint">{dateTime(incident.detected_at)}</p>
        {incident.acknowledged_by && incident.status !== "resolved" && (
          <p className="text-xs text-muted">Acknowledged by {incident.acknowledged_by}</p>
        )}
        {incident.status === "resolved" && (
          <p className="text-xs text-muted">
            <span className="font-semibold text-ink">{incident.resolved_by}:</span> “{incident.resolution_note}”
          </p>
        )}

        {canAct && incident.status !== "resolved" && (
          <div className="mt-auto pt-2">
            <AnimatePresence initial={false} mode="wait">
              {resolving ? (
                <motion.form
                  key="resolve"
                  initial={{ y: 6 }}
                  animate={{ y: 0 }}
                  exit={{ y: 6, transition: transition.fast }}
                  className="flex gap-1.5"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (note.trim()) resolve.mutate();
                  }}
                >
                  <input
                    id={`resolve-${incident.id}`}
                    autoFocus
                    value={note}
                    maxLength={1000}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="What was found?"
                    aria-label="What was found"
                    className="input h-8 min-w-0 flex-1 text-xs"
                  />
                  <button className="btn-primary btn-sm" disabled={!note.trim() || resolve.isPending}>
                    Resolve
                  </button>
                </motion.form>
              ) : (
                <motion.div key="actions" initial={{ y: 6 }} animate={{ y: 0 }} className="flex gap-1.5">
                  {incident.status === "open" && (
                    <button className="btn-ghost btn-sm" onClick={() => ack.mutate()} disabled={ack.isPending}>
                      <Check size={13} /> Acknowledge
                    </button>
                  )}
                  <button className="btn-ghost btn-sm" onClick={() => setResolving(true)}>
                    <ShieldCheck size={13} /> Resolve…
                  </button>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}
      </div>
    </Rise>
  );
}

export function IncidentsTab({ canAct }: { canAct: boolean }) {
  const { bay } = useScope();
  const [status, setStatus] = useState<IncidentStatus | "all">("open");
  const [kind, setKind] = useState<IncidentKind | "">("");
  const [days, setDays] = useState(7);
  const incidents = useQuery({
    queryKey: ["incidents", bay?.id, days],
    queryFn: () => api.incidents({ bayId: bay?.id, days }),
    enabled: !!bay,
  });
  const cameras = useQuery({
    queryKey: ["cameras", bay?.id],
    queryFn: () => api.cameras(bay!.id),
    enabled: !!bay,
  });
  const cameraName = (id: string) => cameras.data?.find((c) => c.id === id)?.name ?? "Camera";
  const all = incidents.data ?? [];
  const count = (s: IncidentStatus) => all.filter((i) => i.status === s).length;
  const shown = all.filter((i) => (status === "all" || i.status === status) && (!kind || i.kind === kind));

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Segmented<IncidentStatus | "all">
          label="Status"
          value={status}
          onChange={setStatus}
          options={[
            { value: "open", label: "Open", count: count("open") },
            { value: "acknowledged", label: "Acknowledged", count: count("acknowledged") },
            { value: "resolved", label: "Resolved", count: count("resolved") },
            { value: "all", label: "All", count: all.length },
          ]}
        />
        <select
          id="incident-kind"
          aria-label="Kind"
          className="input h-8 w-auto py-0 text-xs"
          value={kind}
          onChange={(e) => setKind(e.target.value as IncidentKind | "")}
        >
          <option value="">Every kind</option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {INCIDENT_TITLE[k]}
            </option>
          ))}
        </select>
        <Segmented
          label="Period"
          value={String(days)}
          onChange={(v) => setDays(Number(v))}
          options={[
            { value: "1", label: "24 h" },
            { value: "7", label: "7 days" },
            { value: "30", label: "30 days" },
          ]}
        />
      </div>

      {incidents.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-72 rounded-xl" />
          ))}
        </div>
      ) : shown.length ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {shown.map((i, n) => (
            <IncidentCard key={i.id} incident={i} index={n} cameraName={cameraName(i.camera_id)} canAct={canAct} />
          ))}
        </div>
      ) : (
        <div className="card">
          <EmptyState
            title={status === "open" ? "No open incidents" : "No incidents match"}
            body={`Nothing ${status === "all" ? "" : `${status} `}in the last ${days === 1 ? "24 hours" : `${days} days`}. Incidents appear here only for zones that are drawn and armed.`}
          />
        </div>
      )}
    </>
  );
}
