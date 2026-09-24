import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { useScope } from "../api/scope";
import type { ApprovalReason, Session, SessionStatus } from "../api/types";
import { type Me, hasRole } from "../auth/session";
import {
  APPROVAL_REASONS,
  EmptyState,
  PageHeader,
  SessionBadge,
  VarianceBar,
  dateTime,
  pct,
  reasonLabel,
} from "../components/ui";

const FILTERS: { key: SessionStatus | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "closed", label: "Awaiting count" },
  { key: "disputed", label: "Disputed" },
  { key: "reconciled", label: "Reconciled" },
  { key: "approved", label: "Approved" },
  { key: "open", label: "In progress" },
];

/**
 * Signing off a disputed load. The counts are left alone on purpose: an approval
 * records that a person accepted the discrepancy and why, so the accuracy figure
 * still reflects what actually happened at the bay.
 */
function ApproveCell({ session, isAdmin }: { session: Session; isAdmin: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<ApprovalReason>("damaged_removed");
  const [note, setNote] = useState("");

  const approve = useMutation({
    mutationFn: () => api.approve(session.id, reason, note),
    onSuccess: () => {
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["sessions"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
    },
  });

  if (session.status === "approved") {
    return (
      <div className="text-xs">
        <div className="font-semibold text-ink">{reasonLabel(session.approval_reason)}</div>
        <div className="text-muted">
          {session.approved_by}
          {session.approved_at ? ` · ${dateTime(session.approved_at)}` : ""}
        </div>
        {session.approval_note && (
          <div className="mt-0.5 italic text-faint">“{session.approval_note}”</div>
        )}
      </div>
    );
  }

  if (session.status !== "disputed") return <span className="text-faint">—</span>;
  if (!isAdmin) return <span className="text-xs text-faint">Awaiting sign-off</span>;

  if (!open) {
    return (
      <button className="btn-ghost btn-sm" onClick={() => setOpen(true)}>
        <ShieldCheck size={13} /> Approve
      </button>
    );
  }

  return (
    <form
      className="flex flex-col gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        approve.mutate();
      }}
    >
      <select
        aria-label="Approval reason"
        id={`reason-${session.id}`}
        className="input h-8 py-0 text-xs"
        value={reason}
        onChange={(e) => setReason(e.target.value as ApprovalReason)}
      >
        {APPROVAL_REASONS.map((r) => (
          <option key={r.value} value={r.value}>
            {r.label}
          </option>
        ))}
      </select>
      <input
        id={`note-${session.id}`}
        aria-label="Approval note"
        className="input h-8 text-xs"
        placeholder="Note (optional)"
        maxLength={280}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="flex gap-1.5">
        <button className="btn-primary btn-sm" disabled={approve.isPending}>
          <Check size={13} /> {approve.isPending ? "Saving…" : "Confirm"}
        </button>
        <button type="button" className="btn-ghost btn-sm" onClick={() => setOpen(false)}>
          <X size={13} />
        </button>
      </div>
      {approve.isError && (
        <span className="text-xs text-bad">{(approve.error as Error).message}</span>
      )}
    </form>
  );
}

function VerifyCell({ session, canOperate }: { session: Session; canOperate: boolean }) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const reconcile = useMutation({
    mutationFn: (n: number) => api.reconcile(session.id, n),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
    },
  });

  if (session.status === "open") return <span className="text-xs text-faint">In progress</span>;
  if (session.manual_count !== null)
    return <span className="num text-sm">{session.manual_count.toLocaleString()}</span>;
  if (!canOperate) return <span className="text-xs text-faint">Awaiting count</span>;

  const n = Number(value);
  const valid = value !== "" && Number.isInteger(n) && n >= 0;
  return (
    <form
      className="flex items-center justify-end gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) reconcile.mutate(n);
      }}
    >
      <input
        id={`manual-${session.id}`}
        inputMode="numeric"
        value={value}
        onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))}
        placeholder="Manual"
        aria-label="Manual count"
        className="input num h-8 w-24 px-2 text-right text-sm"
      />
      <button
        className="btn-primary btn-sm px-2"
        disabled={!valid || reconcile.isPending}
        aria-label="Verify"
        title="Verify"
      >
        <Check size={14} />
      </button>
      {reconcile.isError && (
        <span className="text-xs text-bad">{(reconcile.error as Error).message}</span>
      )}
    </form>
  );
}

export default function Sessions({ me }: { me: Me | undefined }) {
  const canOperate = hasRole(me, "operator");
  const isAdmin = hasRole(me, "admin");
  const [filter, setFilter] = useState<SessionStatus | "all">("all");
  const { bay } = useScope();
  const sessions = useQuery({
    queryKey: ["sessions", bay?.id],
    queryFn: () => api.sessions(bay?.id),
    enabled: !!bay,
  });
  const rows = (sessions.data ?? []).filter((s) => filter === "all" || s.status === filter);
  const counts = Object.fromEntries(
    FILTERS.map((f) => [f.key, (sessions.data ?? []).filter((s) => f.key === "all" || s.status === f.key).length]),
  );
  const verified = (sessions.data ?? []).filter((s) => s.accuracy !== null);
  const mean = verified.length ? verified.reduce((a, s) => a + (s.accuracy ?? 0), 0) / verified.length : null;

  return (
    <>
      <PageHeader
        title="Reconciliation"
        subtitle="AI count against the manual count, per truck. Type the manual count to verify a load; an admin signs off any load that disputes."
        actions={
          mean !== null ? (
            <div className="text-right">
              <div className="eyebrow">Mean accuracy</div>
              <div className={`num text-2xl font-bold ${mean >= 0.95 ? "text-good" : "text-warn"}`}>{pct(mean)}</div>
            </div>
          ) : undefined
        }
      />

      <div className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`chip h-8 px-3 transition ${
              filter === f.key ? "bg-ink text-surface" : "bg-surface text-muted hover:text-ink"
            }`}
          >
            {f.label}
            <span className={`num ${filter === f.key ? "text-surface/70" : "text-faint"}`}>{counts[f.key]}</span>
          </button>
        ))}
      </div>

      <div className="card overflow-hidden">
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-ground">
                <tr>
                  <th className="th">Plate</th>
                  <th className="th">Direction</th>
                  <th className="th">Opened</th>
                  <th className="th text-right">AI count</th>
                  <th className="th text-right">Manual</th>
                  <th className="th">Variance</th>
                  <th className="th text-right">Accuracy</th>
                  <th className="th">Status</th>
                  <th className="th">Sign-off</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((s) => (
                  <tr key={s.id} className="transition hover:bg-ground/60">
                    <td className="td num font-semibold text-ink">{s.plate ?? "—"}</td>
                    <td className="td capitalize text-muted">{s.direction}</td>
                    <td className="td text-muted">{dateTime(s.opened_at)}</td>
                    <td className="td num text-right font-semibold">{s.ai_count.toLocaleString()}</td>
                    <td className="td text-right">
                      <VerifyCell session={s} canOperate={canOperate} />
                    </td>
                    <td className="td">
                      <VarianceBar variance={s.variance} manual={s.manual_count} />
                    </td>
                    <td className={`td num text-right ${s.accuracy !== null && s.accuracy < 0.95 ? "font-semibold text-warn" : ""}`}>
                      {pct(s.accuracy)}
                    </td>
                    <td className="td">
                      <SessionBadge status={s.status} />
                    </td>
                    <td className="td whitespace-normal">
                      <ApproveCell session={s} isAdmin={isAdmin} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title={filter === "all" ? "Nothing to reconcile yet" : "No sessions match this filter"}
            body="Closed truck sessions are listed here for manual verification."
          />
        )}
      </div>
    </>
  );
}
