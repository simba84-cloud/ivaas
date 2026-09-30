/**
 * Alerts: what is wrong at this bay right now, who has seen it, and what has just
 * happened.
 *
 * An alert is a condition worked out from current state, so it disappears when the
 * condition clears. Acknowledging records that a person has seen it (who, when, an
 * optional note, in the audit log); it resolves nothing and changes no count.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, LayoutGroup, motion } from "framer-motion";
import { BellRing, Check, CheckCheck, ChevronDown } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useScope } from "../api/scope";
import type { Acknowledgement } from "../api/types";
import { type Me, can } from "../auth/session";
import { useToast } from "../components/toast";
import { EmptyState, dateTime, time } from "../components/ui";
import { type Severity, history, mergeActivity } from "../live/activity";
import { type Alert, SEVERITY_LABEL, useAlerts } from "../live/alerts";
import { useLiveFeed } from "../live/provider";
import { Segmented, SkeletonList, transition } from "../motion";

type Filter = Severity | "all";

const TONE: Record<Severity, { dot: string; chip: string }> = {
  bad: { dot: "bg-bad text-bad", chip: "bg-bad/10 text-bad" },
  warn: { dot: "bg-warn text-warn", chip: "bg-warn/10 text-warn" },
  info: { dot: "bg-brand text-brand", chip: "bg-brand-tint text-brand" },
  good: { dot: "bg-good text-good", chip: "bg-good/10 text-good" },
};

function AlertCard({
  alert,
  canAck,
  onAck,
}: {
  alert: Alert;
  canAck: boolean;
  onAck: (alert: Alert, note: string) => void;
}) {
  const [noting, setNoting] = useState(false);
  const [note, setNote] = useState("");
  const t = TONE[alert.severity];
  const acked = !!alert.ack;

  return (
    <motion.li
      layout
      layoutId={`alert-${alert.key}`}
      initial={{ x: 24 }}
      animate={{ x: 0 }}
      exit={{ x: -24, transition: transition.fast }}
      transition={transition.spring}
      className={`card p-4 ${acked ? "bg-ground/60" : ""}`}
    >
      <div className="flex items-start gap-3">
        <span className={`dot relative mt-1.5 ${t.dot} ${alert.severity === "bad" && !acked ? "ping" : ""}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`text-sm font-bold ${acked ? "text-muted" : "text-ink"}`}>{alert.title}</span>
            <span className={`chip py-0 text-[10.5px] ${t.chip}`}>{SEVERITY_LABEL[alert.severity]}</span>
          </div>
          <p className="mt-0.5 text-sm text-muted">{alert.detail}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-faint">
            {alert.at !== null && <span className="num">since {dateTime(new Date(alert.at).toISOString())}</span>}
            {alert.cameraId && (
              <Link to="/live" className="font-semibold text-brand hover:underline">
                Open camera wall
              </Link>
            )}
            {alert.ack && (
              <span className="inline-flex items-center gap-1 text-good">
                <CheckCheck size={13} /> {alert.ack.acknowledged_by} · {time(alert.ack.acknowledged_at)}
                {alert.ack.note && <span className="text-muted">· “{alert.ack.note}”</span>}
              </span>
            )}
          </div>

          <AnimatePresence initial={false}>
            {noting && !acked && (
              <motion.form
                key="note"
                initial={{ y: -6 }}
                animate={{ y: 0 }}
                exit={{ y: -6, transition: transition.fast }}
                className="mt-3 flex flex-wrap gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  onAck(alert, note);
                }}
              >
                <input
                  id={`ack-note-${alert.key}`}
                  autoFocus
                  value={note}
                  maxLength={500}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Optional note, e.g. who is looking into it"
                  aria-label="Note"
                  className="input h-8 min-w-0 flex-1 text-xs"
                />
                <button className="btn-primary btn-sm">
                  <Check size={13} /> Confirm
                </button>
                <button type="button" className="btn-ghost btn-sm" onClick={() => setNoting(false)}>
                  Cancel
                </button>
              </motion.form>
            )}
          </AnimatePresence>
        </div>

        {!acked && canAck && !noting && (
          <div className="flex flex-none items-center gap-1">
            <button className="btn-ghost btn-sm" onClick={() => onAck(alert, "")}>
              <Check size={13} /> Acknowledge
            </button>
            <button
              className="btn-ghost btn-sm px-1.5"
              aria-label="Acknowledge with a note"
              title="Acknowledge with a note"
              onClick={() => setNoting(true)}
            >
              <ChevronDown size={13} />
            </button>
          </div>
        )}
      </div>
    </motion.li>
  );
}

export default function Alerts({ me }: { me: Me | undefined }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { bay } = useScope();
  const alerts = useAlerts();
  const live = useLiveFeed();
  const sessions = useQuery({
    queryKey: ["sessions", bay?.id],
    queryFn: () => api.sessions(bay?.id),
    enabled: !!bay,
  });
  const [filter, setFilter] = useState<Filter>("all");
  const [showAcked, setShowAcked] = useState(false);
  const canAck = can(me, "session.operate");

  const ack = useMutation({
    mutationFn: ({ alert, note }: { alert: Alert; note: string }) =>
      api.acknowledge(alert.key, alert.title, note),
    // show it acknowledged at once; put it back if the server says no
    onMutate: async ({ alert, note }) => {
      await qc.cancelQueries({ queryKey: ["alert-acks"] });
      const previous = qc.getQueryData<Acknowledgement[]>(["alert-acks"]);
      qc.setQueryData<Acknowledgement[]>(["alert-acks"], (old = []) => [
        {
          key: alert.key,
          acknowledged_by: me?.subject ?? "you",
          acknowledged_at: new Date().toISOString(),
          note: note.trim() || null,
        },
        ...old,
      ]);
      return { previous };
    },
    onError: (err, _vars, ctx) => {
      qc.setQueryData(["alert-acks"], ctx?.previous);
      toast({ severity: "critical", title: "Not acknowledged", detail: (err as Error).message });
    },
    onSuccess: (_d, { alert }) => toast({ severity: "success", title: "Acknowledged", detail: alert.title }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["alert-acks"] }),
  });

  const matches = (a: Alert) => filter === "all" || a.severity === filter;
  const active = alerts.active.filter(matches);
  const acked = alerts.acknowledged.filter(matches);
  const activity = mergeActivity(live.items, history(sessions.data), 30);

  return (
    <>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Alerts</h1>
          <p className="mt-0.5 text-sm text-muted">
            {bay ? bay.name : "Loading bay"} · conditions that need a person, and what just happened
          </p>
        </div>
        <Segmented<Filter>
          label="Severity"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "All", count: alerts.active.length },
            { value: "bad", label: "Critical", count: alerts.counts.bad },
            { value: "warn", label: "Warning", count: alerts.counts.warn },
            { value: "info", label: "Information", count: alerts.counts.info },
            { value: "good", label: "Success", count: alerts.counts.good },
          ]}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section aria-label="Active alerts" className="min-w-0">
          <LayoutGroup>
            {alerts.loading ? (
              <div className="card">
                <SkeletonList rows={4} />
              </div>
            ) : active.length ? (
              <ul className="grid gap-2">
                <AnimatePresence initial={false} mode="popLayout">
                  {active.map((a) => (
                    <AlertCard key={a.key} alert={a} canAck={canAck} onAck={(alert, note) => ack.mutate({ alert, note })} />
                  ))}
                </AnimatePresence>
              </ul>
            ) : (
              <div className="card">
                <EmptyState
                  title={filter === "all" ? "Nothing needs attention" : `No ${SEVERITY_LABEL[filter as Severity].toLowerCase()} alerts`}
                  body={
                    alerts.acknowledged.length
                      ? "Everything current has been acknowledged."
                      : "Alerts appear here when a camera, a load or the platform needs a person."
                  }
                />
              </div>
            )}

            {!canAck && active.length > 0 && (
              <p className="mt-2 text-xs text-faint">Operators and admins can acknowledge alerts.</p>
            )}

            {acked.length > 0 && (
              <div className="mt-5">
                <button
                  className="flex items-center gap-2 text-sm font-bold text-muted hover:text-ink"
                  aria-expanded={showAcked}
                  onClick={() => setShowAcked((v) => !v)}
                >
                  <motion.span animate={{ rotate: showAcked ? 180 : 0 }} className="inline-flex">
                    <ChevronDown size={15} />
                  </motion.span>
                  Acknowledged <span className="num text-faint">{acked.length}</span>
                </button>
                <AnimatePresence initial={false}>
                  {showAcked && (
                    <motion.ul key="acked" initial={{ y: -8 }} animate={{ y: 0 }} exit={{ y: -8 }} className="mt-2 grid gap-2">
                      <AnimatePresence initial={false} mode="popLayout">
                        {acked.map((a) => (
                          <AlertCard key={a.key} alert={a} canAck={false} onAck={() => {}} />
                        ))}
                      </AnimatePresence>
                    </motion.ul>
                  )}
                </AnimatePresence>
              </div>
            )}
          </LayoutGroup>
        </section>

        <aside className="card min-w-0 self-start">
          <div className="panel-head">
            <h2 className="panel-title flex items-center gap-2">
              <BellRing size={14} className="text-muted" /> Recent events
            </h2>
            <span className="text-xs text-faint">live, and the last loads</span>
          </div>
          {sessions.isPending ? (
            <SkeletonList rows={5} />
          ) : activity.length ? (
            <ul className="max-h-[70vh] divide-y divide-line overflow-y-auto">
              <AnimatePresence initial={false}>
                {activity.map((e) => (
                  <motion.li
                    key={e.key}
                    layout="position"
                    initial={{ x: 20 }}
                    animate={{ x: 0 }}
                    transition={transition.spring}
                    className="flex gap-3 px-4 py-2.5"
                  >
                    <span className={`dot mt-1.5 ${TONE[e.severity].dot}`} />
                    <span className="min-w-0">
                      <span className="block text-[13px] font-bold text-ink">{e.title}</span>
                      <span className="block text-xs text-muted">{e.detail}</span>
                      {e.at !== null && <span className="num block text-[11px] text-faint">{time(new Date(e.at).toISOString())}</span>}
                    </span>
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>
          ) : (
            <EmptyState title="No events yet" body="Loads and live events at this bay appear here." />
          )}
        </aside>
      </div>
    </>
  );
}
