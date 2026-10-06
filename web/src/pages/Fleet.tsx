import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Truck, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api/client";
import type { FleetImport } from "../api/types";
import { type Me, can } from "../auth/session";
import { EmptyState } from "../components/ui";
import { MotionRow, SkeletonRows } from "../motion";

function AddTruck() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [plate, setPlate] = useState("");
  const [fleetNumber, setFleetNumber] = useState("");
  const [operator, setOperator] = useState("");
  const add = useMutation({
    mutationFn: () => api.addVehicle({ plate: plate.trim(), fleet_number: fleetNumber.trim(), operator: operator.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fleet"] });
      setPlate("");
      setFleetNumber("");
      setOpen(false);
    },
  });
  if (!open) {
    return (
      <button className="btn-accent" onClick={() => setOpen(true)}>
        <Plus size={16} /> Add truck
      </button>
    );
  }
  return (
    <form
      className="card flex flex-wrap items-end gap-2 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <div>
        <label htmlFor="truck-plate" className="label">
          Plate
        </label>
        <input id="truck-plate" className="input w-36" value={plate} onChange={(e) => setPlate(e.target.value)} required />
      </div>
      <div>
        <label htmlFor="truck-number" className="label">
          Fleet number
        </label>
        <input id="truck-number" className="input w-28" value={fleetNumber} onChange={(e) => setFleetNumber(e.target.value)} />
      </div>
      <div>
        <label htmlFor="truck-operator" className="label">
          Operator
        </label>
        <input
          id="truck-operator"
          className="input w-36"
          value={operator}
          placeholder="e.g. Superlink"
          onChange={(e) => setOperator(e.target.value)}
        />
      </div>
      <button className="btn-accent" disabled={add.isPending || plate.trim().length < 2}>
        Add
      </button>
      <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {add.error && (
        <div role="alert" className="w-full text-xs text-bad">
          {(add.error as Error).message}
        </div>
      )}
    </form>
  );
}

function ImportCsv() {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<FleetImport | null>(null);
  const upload = useMutation({
    mutationFn: (file: File) => api.importFleet(file),
    onSuccess: (r) => {
      setResult(r);
      qc.invalidateQueries({ queryKey: ["fleet"] });
    },
  });
  return (
    <div>
      <input
        ref={input}
        type="file"
        accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        aria-label="Fleet register, CSV or Excel"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload.mutate(f);
          e.target.value = "";
        }}
      />
      <button className="btn-ghost" onClick={() => input.current?.click()} disabled={upload.isPending}>
        <Upload size={15} /> Import CSV or Excel
      </button>
      {result && (
        <div className="mt-1 text-xs text-muted" role="status">
          {result.added} added, {result.updated} updated
          {result.errors.length > 0 && <span className="text-bad"> · {result.errors.length} line(s) not used: {result.errors.join("; ")}</span>}
        </div>
      )}
      {upload.error && (
        <div role="alert" className="mt-1 text-xs text-bad">
          {(upload.error as Error).message}
        </div>
      )}
    </div>
  );
}

/** The trucks this site loads. Plate reads are matched against it, forgiving OCR slips. */
export default function Fleet({ me }: { me: Me | undefined }) {
  const fleet = useQuery({ queryKey: ["fleet"], queryFn: api.fleet });
  const rows = fleet.data ?? [];
  const canEdit = can(me, "site.manage");
  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Fleet</h1>
          <p className="mt-0.5 text-sm text-muted">
            The trucks this site loads. A plate the camera reads is matched here, so each load is
            filed under its truck.
          </p>
        </div>
        {canEdit && (
          <div className="flex items-start gap-2">
            <ImportCsv />
            <AddTruck />
          </div>
        )}
      </div>
      <div className="card overflow-hidden">
        {fleet.isPending ? (
          <table className="w-full">
            <SkeletonRows rows={3} cols={4} />
          </table>
        ) : rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Plate</th>
                  <th className="th">Fleet number</th>
                  <th className="th">Operator</th>
                  <th className="th">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((v, i) => (
                  <MotionRow key={v.id} index={i} className="border-b border-line last:border-0">
                    <td className="td num font-semibold text-ink">
                      <span className="inline-flex items-center gap-2">
                        <Truck size={14} className="text-muted" />
                        {v.plate}
                      </span>
                    </td>
                    <td className="td text-muted">{v.fleet_number || "—"}</td>
                    <td className="td text-muted">{v.operator || "—"}</td>
                    <td className="td text-xs">{v.active ? <span className="text-good">In service</span> : <span className="text-muted">Retired</span>}</td>
                  </MotionRow>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No trucks registered"
            body="Until trucks are registered, plate reads are kept as read and not checked against a fleet. Add trucks, or import a CSV or Excel sheet with a plate column."
          />
        )}
      </div>
    </>
  );
}
