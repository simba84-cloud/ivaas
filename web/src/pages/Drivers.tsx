import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, KeyRound, Plus, Printer, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api/client";
import type { Driver, DriverImport } from "../api/types";
import { type Me, can } from "../auth/session";
import { EmptyState } from "../components/ui";
import { MotionRow, SkeletonRows } from "../motion";

function AddDriver() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [badge, setBadge] = useState("");
  const [ref, setRef] = useState("");
  const [pin, setPin] = useState("");
  const add = useMutation({
    mutationFn: () =>
      api.addDriver({ name: name.trim(), badge_code: badge.trim(), employee_ref: ref.trim(), pin: pin || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drivers"] });
      setName("");
      setBadge("");
      setRef("");
      setPin("");
      setOpen(false);
    },
  });
  if (!open) {
    return (
      <button className="btn-accent" onClick={() => setOpen(true)}>
        <Plus size={16} /> Add driver
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
        <label htmlFor="driver-name" className="label">
          Name
        </label>
        <input id="driver-name" className="input w-44" value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <div>
        <label htmlFor="driver-badge" className="label">
          Badge code
        </label>
        <input
          id="driver-badge"
          className="input w-32"
          value={badge}
          placeholder="e.g. BI0042"
          onChange={(e) => setBadge(e.target.value)}
          required
        />
      </div>
      <div>
        <label htmlFor="driver-ref" className="label">
          Employee no.
        </label>
        <input id="driver-ref" className="input w-28" value={ref} onChange={(e) => setRef(e.target.value)} />
      </div>
      <div>
        <label htmlFor="driver-pin" className="label">
          PIN (optional)
        </label>
        <input
          id="driver-pin"
          className="input w-24"
          inputMode="numeric"
          type="password"
          autoComplete="new-password"
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 8))}
        />
      </div>
      <button className="btn-accent" disabled={add.isPending || name.trim().length < 2 || badge.trim().length < 3}>
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
  const [result, setResult] = useState<DriverImport | null>(null);
  const upload = useMutation({
    mutationFn: (file: File) => api.importDrivers(file),
    onSuccess: (r) => {
      setResult(r);
      qc.invalidateQueries({ queryKey: ["drivers"] });
    },
  });
  return (
    <div>
      <input
        ref={input}
        type="file"
        accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        aria-label="Driver register, CSV or Excel"
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

/** Sets or removes a PIN. The PIN is sent once and never shown again: the API keeps only a hash. */
function PinEditor({ driver }: { driver: Driver }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [pin, setPin] = useState("");
  const save = useMutation({
    mutationFn: (value: string | null) => api.setDriverPin(driver.id, value),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drivers"] });
      setPin("");
      setOpen(false);
    },
  });
  if (!open) {
    return (
      <button className="btn-ghost btn-sm" onClick={() => setOpen(true)} aria-label={`PIN for ${driver.name}`}>
        <KeyRound size={13} /> {driver.has_pin ? "Change PIN" : "Set PIN"}
      </button>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <input
        className="input w-24"
        aria-label="New PIN"
        inputMode="numeric"
        type="password"
        autoComplete="new-password"
        value={pin}
        onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 8))}
      />
      <button className="btn-accent btn-sm" disabled={pin.length < 4 || save.isPending} onClick={() => save.mutate(pin)}>
        Save
      </button>
      {driver.has_pin && (
        <button className="btn-ghost btn-sm" disabled={save.isPending} onClick={() => save.mutate(null)}>
          Remove
        </button>
      )}
      <button className="btn-ghost btn-sm" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {save.error && <span className="text-xs text-bad">{(save.error as Error).message}</span>}
    </span>
  );
}

/** Badge cards to print: the code in large type, so a scanner or a person can read it at the kiosk. */
function BadgeSheet({ drivers }: { drivers: Driver[] }) {
  return (
    <div className="hidden print:grid print:grid-cols-2 print:gap-4" aria-hidden>
      {drivers.map((d) => (
        <div key={d.id} className="rounded-xl border border-line p-4">
          <div className="text-xs uppercase tracking-wide text-muted">Driver badge</div>
          <div className="mt-1 text-lg font-bold text-ink">{d.name}</div>
          <div className="mt-2 font-mono text-3xl font-extrabold tracking-widest text-ink">{d.badge_code}</div>
          {d.employee_ref && <div className="mt-1 text-xs text-muted">Employee {d.employee_ref}</div>}
        </div>
      ))}
    </div>
  );
}

/** The drivers who take crates out and bring them back (ADR 0004). */
export default function Drivers({ me }: { me: Me | undefined }) {
  const drivers = useQuery({ queryKey: ["drivers"], queryFn: api.drivers });
  const rows = drivers.data ?? [];
  const canEdit = can(me, "site.manage");
  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3 print:hidden">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Drivers</h1>
          <p className="mt-0.5 text-sm text-muted">
            Who takes crates out and brings them back. A driver checks in at a controlled bay with their badge, and
            their PIN if they have one, so every crate is traced to a person.
          </p>
        </div>
        <div className="flex items-start gap-2">
          {rows.length > 0 && (
            <button className="btn-ghost" onClick={() => window.print()}>
              <Printer size={15} /> Print badges
            </button>
          )}
          {canEdit && (
            <>
              <ImportCsv />
              <AddDriver />
            </>
          )}
        </div>
      </div>
      <div className="card overflow-hidden print:hidden">
        {drivers.isPending ? (
          <table className="w-full">
            <SkeletonRows rows={3} cols={5} />
          </table>
        ) : rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Name</th>
                  <th className="th">Badge</th>
                  <th className="th">Employee no.</th>
                  <th className="th">PIN</th>
                  <th className="th">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d, i) => (
                  <MotionRow key={d.id} index={i} className="border-b border-line last:border-0">
                    <td className="td font-semibold text-ink">
                      <span className="inline-flex items-center gap-2">
                        <BadgeCheck size={14} className="text-muted" />
                        {d.name}
                      </span>
                    </td>
                    <td className="td num font-mono text-ink">{d.badge_code}</td>
                    <td className="td text-muted">{d.employee_ref || "—"}</td>
                    <td className="td text-xs">
                      {canEdit ? <PinEditor driver={d} /> : d.has_pin ? "Set" : <span className="text-muted">None</span>}
                    </td>
                    <td className="td text-xs">
                      {d.active ? <span className="text-good">Active</span> : <span className="text-muted">Inactive</span>}
                    </td>
                  </MotionRow>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No drivers registered"
            body="Controlled bays check drivers in by badge. Add drivers, or import a CSV or Excel sheet with name and badge_code columns. PINs are set here, one by one, never imported."
          />
        )}
      </div>
      <BadgeSheet drivers={rows.filter((d) => d.active)} />
    </>
  );
}
