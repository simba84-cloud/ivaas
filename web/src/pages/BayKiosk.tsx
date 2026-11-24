import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownToLine, ArrowUpFromLine, CheckCircle2, CircleAlert, CircleHelp, Minus, Plus, Truck } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { CrateDirection, CrateTransaction, CrateValidation, TransactionStatus } from "../api/types";
import { EmptyState } from "../components/ui";

export const STATUS: Record<TransactionStatus, { label: string; tone: string; say: string }> = {
  open: { label: "Counting", tone: "text-brand bg-brand/10", say: "The cameras are counting." },
  verified: { label: "Verified", tone: "text-good bg-good/10", say: "Every column met the standard, and the units match what was declared." },
  short: { label: "Short", tone: "text-bad bg-bad/10", say: "Fewer crates were measured than declared. A supervisor will review it." },
  over: { label: "Over", tone: "text-warn bg-warn/10", say: "More crates were measured than declared. A supervisor will review it." },
  non_standard: {
    label: "Not standard",
    tone: "text-warn bg-warn/10",
    say: "The total matches, but not in standard columns, so the standard cannot vouch for it. A supervisor will review it.",
  },
  unverified: {
    label: "Unverified",
    tone: "text-warn bg-warn/10",
    say: "At least one column could not be measured, so nothing is claimed. A supervisor will review it.",
  },
  accepted: { label: "Accepted", tone: "text-muted bg-ground", say: "A supervisor accepted this, on the record." },
};

const DIRECTIONS: { value: CrateDirection; label: string; icon: typeof Truck }[] = [
  { value: "loading", label: "Dispatch", icon: ArrowUpFromLine },
  { value: "offloading", label: "Return", icon: ArrowDownToLine },
];

/** One square per expected column, filled as the cameras judge each one. */
function Columns({ v }: { v: CrateValidation }) {
  const slots = Math.max(v.expected_columns, v.columns.length);
  return (
    <div className="flex flex-wrap gap-2" aria-label="Columns">
      {Array.from({ length: slots }, (_, i) => {
        const c = v.columns[i];
        const tone = !c
          ? "border-dashed border-line text-faint"
          : c.verdict === "full"
            ? "border-good bg-good/10 text-good"
            : c.verdict === "non_standard"
              ? "border-bad bg-bad/10 text-bad"
              : "border-warn bg-warn/10 text-warn";
        return (
          <div
            key={i}
            className={`flex h-16 w-14 flex-col items-center justify-center rounded-lg border-2 ${tone}`}
            title={c ? `${c.crates} crates, ${c.verdict.replace("_", " ")}` : "not yet counted"}
          >
            {c ? (
              <>
                <span className="num text-lg font-extrabold">{c.crates}</span>
                {c.verdict === "unsure" ? <CircleHelp size={14} /> : c.verdict === "full" ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}
              </>
            ) : (
              <span className="text-xs">{i + 1}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function CheckInForm({ bayId, crates }: { bayId: string; crates: { columns: number; layers: number } }) {
  const qc = useQueryClient();
  const [badge, setBadge] = useState("");
  const [pin, setPin] = useState("");
  const [direction, setDirection] = useState<CrateDirection>("loading");
  const [units, setUnits] = useState(1);
  const go = useMutation({
    mutationFn: () => api.checkIn(bayId, { badge: badge.trim(), pin: pin || undefined, direction, declared_units: units }),
    onSuccess: () => {
      setBadge("");
      setPin("");
      qc.invalidateQueries({ queryKey: ["bay-transaction", bayId] });
    },
  });
  const perUnit = crates.columns * crates.layers;
  return (
    <form
      className="card mx-auto max-w-xl space-y-5 p-6"
      onSubmit={(e) => {
        e.preventDefault();
        go.mutate();
      }}
    >
      <h2 className="text-xl font-extrabold text-ink">Check in</h2>
      <div>
        <label htmlFor="kiosk-badge" className="label">
          Scan or type your badge
        </label>
        <input
          id="kiosk-badge"
          className="input w-full font-mono text-2xl tracking-widest"
          autoFocus
          autoComplete="off"
          value={badge}
          onChange={(e) => setBadge(e.target.value)}
          required
        />
      </div>
      <div>
        <label htmlFor="kiosk-pin" className="label">
          PIN, if you have one
        </label>
        <input
          id="kiosk-pin"
          className="input w-40 text-2xl tracking-widest"
          type="password"
          inputMode="numeric"
          autoComplete="off"
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 8))}
        />
      </div>
      <div className="grid grid-cols-2 gap-3" role="radiogroup" aria-label="Direction">
        {DIRECTIONS.map((d) => (
          <button
            key={d.value}
            type="button"
            role="radio"
            aria-checked={direction === d.value}
            className={`flex items-center justify-center gap-2 rounded-xl border-2 p-4 text-lg font-bold ${
              direction === d.value ? "border-brand bg-brand/10 text-brand" : "border-line text-muted"
            }`}
            onClick={() => setDirection(d.value)}
          >
            <d.icon size={20} /> {d.label}
          </button>
        ))}
      </div>
      <div>
        <div className="label">Standard units</div>
        <div className="flex items-center gap-3">
          <button type="button" className="btn-ghost" aria-label="One fewer unit" onClick={() => setUnits((u) => Math.max(1, u - 1))}>
            <Minus size={18} />
          </button>
          <span className="num w-12 text-center text-3xl font-extrabold text-ink" aria-live="polite">
            {units}
          </span>
          <button type="button" className="btn-ghost" aria-label="One more unit" onClick={() => setUnits((u) => Math.min(50, u + 1))}>
            <Plus size={18} />
          </button>
          <span className="text-sm text-muted">
            × {crates.columns} columns of {crates.layers} = <strong className="text-ink">{units * perUnit} crates</strong>
          </span>
        </div>
      </div>
      <button className="btn-accent w-full py-3 text-lg" disabled={go.isPending || badge.trim().length < 3}>
        Start
      </button>
      {go.error && (
        <div role="alert" className="text-sm text-bad">
          {(go.error as Error).message}
        </div>
      )}
    </form>
  );
}

function InProgress({ txn, onDone }: { txn: CrateTransaction; onDone: (t: CrateTransaction) => void }) {
  const done = useMutation({ mutationFn: () => api.closeTransaction(txn.id), onSuccess: onDone });
  const v = txn.progress;
  const dir = DIRECTIONS.find((d) => d.value === txn.direction)!;
  return (
    <div className="card mx-auto max-w-2xl space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-muted">{dir.label}</div>
          <div className="text-2xl font-extrabold text-ink">{txn.driver_name ?? "Unknown driver"}</div>
          <div className="mt-1 flex items-center gap-2 text-sm text-muted">
            <Truck size={14} /> {txn.plate ?? "No plate read yet"}
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs uppercase tracking-wide text-muted">Declared</div>
          <div className="num text-2xl font-extrabold text-ink">{txn.expected_crates} crates</div>
          <div className="text-xs text-muted">
            {txn.declared_units} unit(s) of {txn.standard_columns} × {txn.standard_layers}
          </div>
        </div>
      </div>
      {v && (
        <>
          <Columns v={v} />
          <div className="text-sm text-muted" role="status">
            {v.full_columns} of {v.expected_columns} columns at the standard height
            {v.non_standard_columns > 0 && <span className="text-bad"> · {v.non_standard_columns} not standard</span>}
            {v.unsure_columns > 0 && <span className="text-warn"> · {v.unsure_columns} could not be measured</span>}
          </div>
        </>
      )}
      <button className="btn-accent w-full py-3 text-lg" disabled={done.isPending} onClick={() => done.mutate()}>
        Done
      </button>
      {done.error && (
        <div role="alert" className="text-sm text-bad">
          {(done.error as Error).message}
        </div>
      )}
    </div>
  );
}

function Result({ txn, onNext }: { txn: CrateTransaction; onNext: () => void }) {
  const s = STATUS[txn.status];
  return (
    <div className="card mx-auto max-w-xl space-y-4 p-6 text-center" role="status">
      <span className={`inline-block rounded-full px-4 py-1 text-lg font-extrabold ${s.tone}`}>{s.label}</span>
      <div className="text-xl font-bold text-ink">{txn.driver_name}</div>
      <div className="num text-3xl font-extrabold text-ink">
        {txn.counted_crates === null ? "Not measured" : `${txn.counted_crates} of ${txn.expected_crates} crates`}
      </div>
      <p className="text-sm text-muted">{s.say}</p>
      <button className="btn-accent w-full py-3 text-lg" onClick={onNext}>
        Next driver
      </button>
    </div>
  );
}

/** The tablet at a controlled bay: a driver checks in, presents standard units, and sees the result (ADR 0004). */
export default function BayKiosk() {
  const { bayId = "" } = useParams();
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const standards = useQuery({ queryKey: ["bay-standards"], queryFn: api.bayStandards });
  const [result, setResult] = useState<CrateTransaction | null>(null);
  const current = useQuery({
    queryKey: ["bay-transaction", bayId],
    queryFn: () => api.bayTransaction(bayId),
    enabled: !!bayId && !result,
    refetchInterval: 2000,
  });
  const controlled = (standards.data ?? []).filter((s) => s.controlled);

  if (!bayId) {
    const names = new Map((bays.data ?? []).map((b) => [b.id, b.name]));
    return (
      <>
        <h1 className="mb-4 text-2xl font-extrabold tracking-tight text-ink">Bay kiosk</h1>
        {controlled.length ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {controlled.map((s) => (
              <Link key={s.bay_id} to={`/bay/${s.bay_id}`} className="card p-5 hover:shadow-lift">
                <div className="text-lg font-bold text-ink">{names.get(s.bay_id) ?? "Bay"}</div>
                <div className="text-sm text-muted">
                  Standard unit: {s.columns} columns × {s.layers} crates = {s.crates_per_unit}
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <div className="card">
            <EmptyState
              title="No controlled bays"
              body="A bay becomes a controlled bay once its standard unit is set, under Crate Control → Bay standards."
            />
          </div>
        )}
      </>
    );
  }

  const standard = controlled.find((s) => s.bay_id === bayId);
  const bay = (bays.data ?? []).find((b) => b.id === bayId);
  return (
    <>
      <div className="mb-4">
        <h1 className="text-2xl font-extrabold tracking-tight text-ink">{bay?.name ?? "Bay"}</h1>
        {standard && (
          <p className="text-sm text-muted">
            Standard unit: {standard.columns} columns of {standard.layers} crates
          </p>
        )}
      </div>
      {standards.isPending ? null : !standard ? (
        <div className="card">
          <EmptyState title="Not a controlled bay" body="Set this bay's standard unit before drivers can check in here." />
        </div>
      ) : result ? (
        <Result
          txn={result}
          onNext={() => {
            setResult(null);
          }}
        />
      ) : current.data ? (
        <InProgress txn={current.data} onDone={setResult} />
      ) : current.isPending ? null : (
        <CheckInForm bayId={bayId} crates={standard} />
      )}
    </>
  );
}
