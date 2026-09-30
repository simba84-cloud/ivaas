import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { OverrideReason, Session } from "../api/types";

/** Only the states that need a person say anything; a registered truck is just its plate. */
const FLAG: Partial<Record<NonNullable<Session["identification"]>, { label: string; why: string }>> = {
  unidentified: { label: "Unidentified", why: "No plate was read for this load. Say which truck it was." },
  unregistered: { label: "Not in fleet", why: "The plate read is not in the fleet register." },
};

export function PlateCell({ session }: { session: Session }) {
  const flag = session.identification ? FLAG[session.identification] : undefined;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="num font-semibold text-ink">{session.plate ?? "—"}</span>
      {flag && (
        <span title={flag.why} className="rounded-full bg-warn/10 px-1.5 py-0.5 text-[10.5px] font-semibold text-warn">
          {flag.label}
        </span>
      )}
    </span>
  );
}

/** The AI count, and beside it a person's correction if there is one. */
export function CountCell({ session }: { session: Session }) {
  if (session.override_count == null) return <>{session.ai_count.toLocaleString()}</>;
  return (
    <span title={`AI counted ${session.ai_count}; corrected by ${session.override_by}`}>
      <span className="text-faint line-through">{session.ai_count.toLocaleString()}</span>{" "}
      {session.override_count.toLocaleString()}
      <span className="ml-1 text-[10.5px] font-semibold text-warn">corrected</span>
    </span>
  );
}

export function AssignTruck({ session }: { session: Session }) {
  const qc = useQueryClient();
  const fleet = useQuery({ queryKey: ["fleet"], queryFn: api.fleet });
  const [choice, setChoice] = useState("");
  const [plate, setPlate] = useState("");
  const assign = useMutation({
    mutationFn: () =>
      api.assignVehicle(session.id, choice ? { vehicle_id: choice } : { plate: plate.trim() }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
  const vehicles = (fleet.data ?? []).filter((v) => v.active);
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        assign.mutate();
      }}
    >
      {vehicles.length > 0 && (
        <select
          aria-label="Registered truck"
          className="input h-8 w-44 py-0 text-xs"
          value={choice}
          onChange={(e) => setChoice(e.target.value)}
        >
          <option value="">Registered truck…</option>
          {vehicles.map((v) => (
            <option key={v.id} value={v.id}>
              {v.plate}
              {v.fleet_number ? ` · ${v.fleet_number}` : ""}
            </option>
          ))}
        </select>
      )}
      {!choice && (
        <input
          aria-label="Plate"
          className="input h-8 w-32 text-xs"
          placeholder="or type the plate"
          value={plate}
          onChange={(e) => setPlate(e.target.value)}
        />
      )}
      <button className="btn-ghost btn-sm" disabled={assign.isPending || (!choice && plate.trim().length < 2)}>
        Set truck
      </button>
      {assign.error && (
        <span role="alert" className="text-xs text-bad">
          {(assign.error as Error).message}
        </span>
      )}
    </form>
  );
}

const REASONS: { value: OverrideReason; label: string }[] = [
  { value: "person_or_forklift", label: "A person or forklift was counted" },
  { value: "double_counted", label: "Counted twice" },
  { value: "missed_by_camera", label: "The camera missed crates" },
  { value: "camera_blocked", label: "The camera's view was blocked" },
  { value: "damaged_removed", label: "Damaged crates removed" },
  { value: "other", label: "Other (say what)" },
];

/**
 * Correct the count of record. The AI count is kept as it was, and accuracy is still
 * measured on it: a correction explains a load, it does not improve the AI's score.
 */
export function CorrectCount({ session }: { session: Session }) {
  const qc = useQueryClient();
  const [count, setCount] = useState(String(session.override_count ?? session.ai_count));
  const [reason, setReason] = useState<OverrideReason>("person_or_forklift");
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () => api.overrideCount(session.id, { count: Number(count), reason, note: note || undefined }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <input
        aria-label="Corrected count"
        type="number"
        min={0}
        className="input h-8 w-24 text-xs"
        value={count}
        onChange={(e) => setCount(e.target.value)}
      />
      <select
        aria-label="Reason"
        className="input h-8 w-56 py-0 text-xs"
        value={reason}
        onChange={(e) => setReason(e.target.value as OverrideReason)}
      >
        {REASONS.map((r) => (
          <option key={r.value} value={r.value}>
            {r.label}
          </option>
        ))}
      </select>
      <input
        aria-label="Note"
        className="input h-8 w-48 text-xs"
        placeholder={reason === "other" ? "what happened (required)" : "note (optional)"}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <button className="btn-ghost btn-sm" disabled={save.isPending || count === ""}>
        Record correction
      </button>
      {save.error && (
        <span role="alert" className="text-xs text-bad">
          {(save.error as Error).message}
        </span>
      )}
    </form>
  );
}
