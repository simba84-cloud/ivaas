import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronRight, ShieldCheck, X } from "lucide-react";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useScope } from "../api/scope";
import type { ApprovalReason, Session, SessionStatus } from "../api/types";
import { type Me, can } from "../auth/session";
import {
  APPROVAL_REASONS,
  EmptyState,
  PageHeader,
  SessionBadge,
  VarianceBar,
  dateTime,
  pct,
  reasonLabel,
  time,
} from "../components/ui";
import { useToast } from "../components/toast";
import { MotionRow, Segmented, SkeletonRows, transition } from "../motion";

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
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<ApprovalReason>("damaged_removed");
  const [note, setNote] = useState("");

  const approve = useMutation({
    mutationFn: () => api.approve(session.id, reason, note),
    onSuccess: () => {
      setOpen(false);
      toast({
        severity: "success",
        title: `Signed off · ${session.plate ?? "no plate"}`,
        detail: "The discrepancy is accepted; the counts are unchanged.",
      });
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

/**
 * The manual count. Operators enter it blind, from the tally sheet; typing a figure in
 * here, beside the AI's, is an admin's correction path only.
 */
function VerifyCell({ session, canCorrect }: { session: Session; canCorrect: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState("");
  const reconcile = useMutation({
    mutationFn: (n: number) => api.reconcile(session.id, n),
    onSuccess: (s) => {
      toast({
        severity: s.status === "disputed" ? "warning" : "success",
        title: s.status === "disputed" ? `Disputed · ${s.plate ?? "no plate"}` : `Verified · ${s.plate ?? "no plate"}`,
        detail: `AI ${s.ai_count.toLocaleString()}, sheet ${s.manual_count?.toLocaleString()} · accuracy ${pct(s.accuracy)}`,
      });
      qc.invalidateQueries({ queryKey: ["sessions"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
    },
  });

  if (session.status === "open") return <span className="text-xs text-faint">In progress</span>;
  if (session.manual_count !== null)
    return <span className="num text-sm">{session.manual_count.toLocaleString()}</span>;
  if (!canCorrect)
    return (
      <Link to="/tally" className="text-xs text-faint underline-offset-2 hover:text-ink hover:underline">
        Awaiting tally sheet
      </Link>
    );

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
  const isAdmin = can(me, "reconciliation.resolve");
  const [filter, setFilter] = useState<SessionStatus | "all">("all");
  const [expanded, setExpanded] = useState<string | null>(null);
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
        subtitle="AI count against the manual count, per truck. Manual counts come from the tally sheets; an admin signs off any load that disputes."
        actions={
          mean !== null ? (
            <div className="text-right">
              <div className="eyebrow">Mean accuracy</div>
              <div className={`num text-2xl font-bold ${mean >= 0.95 ? "text-good" : "text-warn"}`}>{pct(mean)}</div>
            </div>
          ) : undefined
        }
      />

      <div className="mb-4 overflow-x-auto">
        <Segmented<SessionStatus | "all">
          label="Filter by status"
          size="md"
          value={filter}
          onChange={setFilter}
          options={FILTERS.map((f) => ({ value: f.key, label: f.label, count: counts[f.key] }))}
        />
      </div>

      <div className="card overflow-hidden">
        {sessions.isPending ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <SkeletonRows rows={6} cols={9} />
            </table>
          </div>
        ) : rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-ground">
                <tr>
                  <th className="th w-8">
                    <span className="sr-only">Details</span>
                  </th>
                  <th className="th">Plate</th>
                  <th className="th">Direction</th>
                  <th className="th">Opened</th>
                  <th className="th text-right">AI count</th>
                  <th className="th text-right">Manual</th>
                  <th className="th">Variance</th>
                  <th className="th text-right">Accuracy</th>
                  <th className="th">Status</th>
                  <th className="th min-w-[14rem]">Sign-off</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((s, i) => (
                  <Fragment key={s.id}>
                  <MotionRow index={i} className="transition-colors hover:bg-ground/60">
                    <td className="td pr-0">
                      <button
                        className="grid h-6 w-6 place-items-center rounded-md text-faint transition hover:bg-ground hover:text-ink"
                        aria-expanded={expanded === s.id}
                        aria-label={`Details for ${s.plate ?? "this load"}`}
                        onClick={() => setExpanded((e) => (e === s.id ? null : s.id))}
                      >
                        <motion.span animate={{ rotate: expanded === s.id ? 90 : 0 }} transition={transition.fast} className="inline-flex">
                          <ChevronRight size={14} />
                        </motion.span>
                      </button>
                    </td>
                    <td className="td num font-semibold text-ink">{s.plate ?? "—"}</td>
                    <td className="td capitalize text-muted">{s.direction}</td>
                    <td className="td text-muted">{dateTime(s.opened_at)}</td>
                    <td className="td num text-right font-semibold">{s.ai_count.toLocaleString()}</td>
                    <td className="td text-right">
                      <VerifyCell session={s} canCorrect={isAdmin} />
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
                    <td className="td min-w-[14rem] whitespace-normal">
                      <ApproveCell session={s} isAdmin={isAdmin} />
                    </td>
                  </MotionRow>
                  <AnimatePresence initial={false}>
                    {expanded === s.id && (
                      <motion.tr
                        key="detail"
                        initial={{ y: -6 }}
                        animate={{ y: 0 }}
                        exit={{ y: -6, transition: transition.fast }}
                        className="bg-ground/50"
                      >
                        <td />
                        <td colSpan={9} className="px-3 py-3">
                          <dl className="grid gap-x-8 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
                            <div>
                              <dt className="eyebrow">Opened · closed</dt>
                              <dd className="num mt-0.5 text-ink">
                                {time(s.opened_at)} · {s.closed_at ? time(s.closed_at) : "still open"}
                                {s.closed_at && (
                                  <span className="text-muted">
                                    {" "}
                                    ({Math.max(1, Math.round((Date.parse(s.closed_at) - Date.parse(s.opened_at)) / 60000))} min)
                                  </span>
                                )}
                              </dd>
                            </div>
                            <div>
                              <dt className="eyebrow">Counts</dt>
                              <dd className="num mt-0.5 text-ink">
                                AI {s.ai_count.toLocaleString()} ·{" "}
                                {s.manual_count === null ? "no manual count yet" : `sheet ${s.manual_count.toLocaleString()}`}
                              </dd>
                            </div>
                            <div>
                              <dt className="eyebrow">Sign-off</dt>
                              <dd className="mt-0.5 text-ink">
                                {s.approved_by ? `${reasonLabel(s.approval_reason)} by ${s.approved_by}` : "None"}
                              </dd>
                            </div>
                            <div>
                              <dt className="eyebrow">Session</dt>
                              <dd className="num mt-0.5 truncate text-faint" title={s.id}>
                                {s.id}
                              </dd>
                            </div>
                          </dl>
                        </td>
                      </motion.tr>
                    )}
                  </AnimatePresence>
                  </Fragment>
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
