import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AcceptReason, Bay, BayStandard, CrateTransaction } from "../api/types";
import { type Me, can } from "../auth/session";
import { EmptyState, dateTime } from "../components/ui";
import { MotionRow, SkeletonRows } from "../motion";
import { STATUS } from "./BayKiosk";

type Tab = "balances" | "transactions" | "standards";
const WINDOWS = [7, 30, 90];
const REASONS: { value: AcceptReason; label: string }[] = [
  { value: "damaged_crates", label: "Damaged crates" },
  { value: "camera_blocked", label: "Camera blocked" },
  { value: "counted_by_hand", label: "Counted by hand" },
  { value: "declaration_error", label: "Declaration error" },
  { value: "other", label: "Other" },
];

/** Crates out, back and outstanding per driver. Unmeasured transactions sit beside the totals, never in them. */
function DriverBalances({ days }: { days: number }) {
  const rows = useQuery({ queryKey: ["driver-balances", days], queryFn: () => api.driverBalances(days) });
  const data = rows.data ?? [];
  if (rows.isPending) {
    return (
      <table className="w-full">
        <SkeletonRows rows={4} cols={6} />
      </table>
    );
  }
  if (!data.length) {
    return (
      <EmptyState
        title="No crate transactions in this window"
        body="Balances appear once drivers check in at a controlled bay and their crates are validated."
      />
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-line">
            <th className="th">Driver</th>
            <th className="th text-right">Issued</th>
            <th className="th text-right">Returned</th>
            <th className="th text-right">Outstanding</th>
            <th className="th text-right">Exceptions</th>
            <th className="th">Not measured</th>
          </tr>
        </thead>
        <tbody>
          {data.map((b, i) => (
            <MotionRow key={b.driver_id} index={i} className="border-b border-line last:border-0">
              <td className="td">
                <div className="font-semibold text-ink">{b.driver_name ?? "Removed driver"}</div>
                <div className="font-mono text-xs text-muted">{b.badge_code ?? "—"}</div>
              </td>
              <td className="td num text-right">{b.issued.toLocaleString()}</td>
              <td className="td num text-right">{b.returned.toLocaleString()}</td>
              <td className={`td num text-right font-semibold ${b.outstanding > 0 ? "text-warn" : "text-ink"}`}>
                {b.outstanding.toLocaleString()}
              </td>
              <td className={`td num text-right ${b.exceptions ? "text-bad" : "text-muted"}`}>{b.exceptions}</td>
              <td className="td text-xs text-muted">
                {[
                  b.unmeasured
                    ? `${b.unmeasured} transaction(s): ${b.unmeasured_declared_out} declared out, ${b.unmeasured_declared_back} declared back`
                    : "",
                  b.in_progress ? `${b.in_progress} at the bay now` : "",
                ]
                  .filter(Boolean)
                  .join(" · ") || "—"}
              </td>
            </MotionRow>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Accept({ txn }: { txn: CrateTransaction }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<AcceptReason>("damaged_crates");
  const [note, setNote] = useState("");
  const accept = useMutation({
    mutationFn: () => api.acceptTransaction(txn.id, reason, note),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["crate-transactions"] });
      qc.invalidateQueries({ queryKey: ["driver-balances"] });
      setOpen(false);
    },
  });
  if (!open) {
    return (
      <button className="btn-ghost btn-sm" onClick={() => setOpen(true)}>
        Accept…
      </button>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <select className="input" aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value as AcceptReason)}>
        {REASONS.map((r) => (
          <option key={r.value} value={r.value}>
            {r.label}
          </option>
        ))}
      </select>
      <input className="input w-40" aria-label="Note" placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} />
      <button
        className="btn-accent btn-sm"
        disabled={accept.isPending || (reason === "other" && !note.trim())}
        onClick={() => accept.mutate()}
      >
        Accept
      </button>
      <button className="btn-ghost btn-sm" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {accept.error && <span className="text-xs text-bad">{(accept.error as Error).message}</span>}
    </span>
  );
}

function Transactions({ days, me }: { days: number; me: Me | undefined }) {
  const [onlyExceptions, setOnlyExceptions] = useState(true);
  const rows = useQuery({
    queryKey: ["crate-transactions", days, onlyExceptions],
    queryFn: () => api.crateTransactions(days, onlyExceptions),
  });
  const data = rows.data ?? [];
  const canAccept = can(me, "reconciliation.resolve");
  return (
    <>
      <label className="flex items-center gap-2 border-b border-line p-3 text-sm text-muted">
        <input type="checkbox" checked={onlyExceptions} onChange={(e) => setOnlyExceptions(e.target.checked)} />
        Exceptions only (short, over, not standard, unverified)
      </label>
      {rows.isPending ? (
        <table className="w-full">
          <SkeletonRows rows={4} cols={6} />
        </table>
      ) : data.length ? (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-line">
                <th className="th">When</th>
                <th className="th">Driver</th>
                <th className="th">Bay · plate</th>
                <th className="th text-right">Declared</th>
                <th className="th text-right">Measured</th>
                <th className="th">Result</th>
                <th className="th" />
              </tr>
            </thead>
            <tbody>
              {data.map((t, i) => {
                const s = STATUS[t.status];
                return (
                  <MotionRow key={t.id} index={i} className="border-b border-line last:border-0">
                    <td className="td text-xs text-muted">{dateTime(t.opened_at)}</td>
                    <td className="td">
                      <div className="font-semibold text-ink">{t.driver_name ?? "—"}</div>
                      <div className="text-xs text-muted">{t.direction === "loading" ? "Dispatch" : "Return"}</div>
                    </td>
                    <td className="td text-sm text-muted">
                      {t.bay_name ?? "—"} · {t.plate ?? "no plate"}
                    </td>
                    <td className="td num text-right">{t.expected_crates}</td>
                    <td className="td num text-right">
                      {t.counted_crates ?? <span className="text-xs text-warn">not measured</span>}
                    </td>
                    <td className="td">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${s.tone}`}>{s.label}</span>
                      {t.validated_status && t.status === "accepted" && (
                        <div className="mt-1 text-xs text-muted">
                          was {STATUS[t.validated_status].label.toLowerCase()} · {t.accepted_by}
                          {t.accept_note ? `: ${t.accept_note}` : ""}
                        </div>
                      )}
                      {t.unsure_columns ? <div className="mt-1 text-xs text-warn">{t.unsure_columns} column(s) unmeasured</div> : null}
                    </td>
                    <td className="td text-right">
                      {canAccept && ["short", "over", "non_standard", "unverified"].includes(t.status) && <Accept txn={t} />}
                    </td>
                  </MotionRow>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          title={onlyExceptions ? "No exceptions in this window" : "No transactions in this window"}
          body={onlyExceptions ? "Every transaction in this window met its standard, or was accepted." : "Transactions appear once drivers check in at a controlled bay."}
        />
      )}
    </>
  );
}

function StandardRow({ bay, standard, canEdit }: { bay: Bay; standard: BayStandard | undefined; canEdit: boolean }) {
  const qc = useQueryClient();
  const [columns, setColumns] = useState(standard?.columns ?? 1);
  const [layers, setLayers] = useState(standard?.layers ?? 14);
  const [controlled, setControlled] = useState(standard?.controlled ?? true);
  const save = useMutation({
    mutationFn: () => api.setBayStandard(bay.id, { columns, layers, controlled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bay-standards"] }),
  });
  return (
    <tr className="border-b border-line last:border-0">
      <td className="td font-semibold text-ink">{bay.name}</td>
      <td className="td">
        {canEdit ? (
          <input
            className="input w-20"
            type="number"
            min={1}
            max={20}
            aria-label={`Columns per unit at ${bay.name}`}
            value={columns}
            onChange={(e) => setColumns(Number(e.target.value))}
          />
        ) : (
          (standard?.columns ?? "—")
        )}
      </td>
      <td className="td">
        {canEdit ? (
          <input
            className="input w-20"
            type="number"
            min={1}
            max={40}
            aria-label={`Crates per column at ${bay.name}`}
            value={layers}
            onChange={(e) => setLayers(Number(e.target.value))}
          />
        ) : (
          (standard?.layers ?? "—")
        )}
      </td>
      <td className="td num">{columns * layers}</td>
      <td className="td text-xs">
        {canEdit ? (
          <label className="inline-flex items-center gap-1">
            <input type="checkbox" checked={controlled} onChange={(e) => setControlled(e.target.checked)} /> Controlled
          </label>
        ) : standard?.controlled ? (
          <span className="text-good">Controlled</span>
        ) : (
          <span className="text-muted">Not controlled</span>
        )}
      </td>
      <td className="td text-right">
        {canEdit && (
          <button className="btn-ghost btn-sm" disabled={save.isPending} onClick={() => save.mutate()}>
            {standard ? "Save" : "Set standard"}
          </button>
        )}
        {standard?.controlled && (
          <Link className="btn-ghost btn-sm ml-1" to={`/bay/${bay.id}`}>
            Open kiosk
          </Link>
        )}
        {save.error && <div className="text-xs text-bad">{(save.error as Error).message}</div>}
      </td>
    </tr>
  );
}

function Standards({ me }: { me: Me | undefined }) {
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const standards = useQuery({ queryKey: ["bay-standards"], queryFn: api.bayStandards });
  const byBay = new Map((standards.data ?? []).map((s) => [s.bay_id, s]));
  const canEdit = can(me, "site.manage");
  if (bays.isPending || standards.isPending) {
    return (
      <table className="w-full">
        <SkeletonRows rows={2} cols={6} />
      </table>
    );
  }
  return (
    <>
      <p className="border-b border-line p-3 text-sm text-muted">
        A standard unit is a number of columns side by side, each a fixed number of crates high. At a controlled bay
        every column the cameras count is checked against that height.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-line">
              <th className="th">Bay</th>
              <th className="th">Columns per unit</th>
              <th className="th">Crates per column</th>
              <th className="th">Crates per unit</th>
              <th className="th">Mode</th>
              <th className="th" />
            </tr>
          </thead>
          <tbody>
            {(bays.data ?? []).map((b) => (
              <StandardRow key={`${b.id}-${byBay.get(b.id)?.columns}-${byBay.get(b.id)?.layers}`} bay={b} standard={byBay.get(b.id)} canEdit={canEdit} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** Controlled bays (ADR 0004): who has crates out, what needs a supervisor, and what each bay's standard is. */
export default function CrateControl({ me }: { me: Me | undefined }) {
  const [tab, setTab] = useState<Tab>("balances");
  const [days, setDays] = useState(30);
  const TABS: { value: Tab; label: string }[] = [
    { value: "balances", label: "Balances by driver" },
    { value: "transactions", label: "Transactions" },
    { value: "standards", label: "Bay standards" },
  ];
  return (
    <>
      <div className="mb-4">
        <h1 className="text-2xl font-extrabold tracking-tight text-ink">Crate Control</h1>
        <p className="mt-0.5 text-sm text-muted">
          Drivers check in at controlled bays and present crates in standard units. Each column is checked against the
          standard, and every crate is traced to the driver. What the cameras could not measure is shown as such, never
          counted as zero.
        </p>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {TABS.map((t) => (
          <button
            key={t.value}
            className={`btn-ghost btn-sm ${tab === t.value ? "ring-1 ring-brand" : ""}`}
            aria-pressed={tab === t.value}
            onClick={() => setTab(t.value)}
          >
            {t.label}
          </button>
        ))}
        {tab !== "standards" && (
          <>
            <span className="mx-1 text-faint">·</span>
            {WINDOWS.map((d) => (
              <button
                key={d}
                className={`btn-ghost btn-sm ${days === d ? "ring-1 ring-brand" : ""}`}
                aria-pressed={days === d}
                onClick={() => setDays(d)}
              >
                {d} days
              </button>
            ))}
          </>
        )}
      </div>
      <div className="card overflow-hidden">
        {tab === "balances" && <DriverBalances days={days} />}
        {tab === "transactions" && <Transactions days={days} me={me} />}
        {tab === "standards" && <Standards me={me} />}
      </div>
    </>
  );
}
