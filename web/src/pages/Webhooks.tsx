import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, RotateCcw, Send, Trash2, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { Webhook, WebhookCreated, WebhookDelivery, WebhookEvent } from "../api/types";
import { EmptyState, dateTime } from "../components/ui";

const EVENTS: { id: WebhookEvent; label: string; why: string }[] = [
  { id: "session.closed", label: "A load closes", why: "Each truck's counts, as soon as it leaves the bay." },
  {
    id: "exception.raised",
    label: "A manifest exception is raised",
    why: "Where the cameras and a dispatch manifest disagree.",
  },
];
const LABEL: Record<string, string> = {
  "session.closed": "Load closed",
  "exception.raised": "Manifest exception",
  "webhook.test": "Test",
};

/** The signing secret, shown once: the platform keeps it only to sign with. */
function OneTimeSecret({ hook, onClose }: { hook: WebhookCreated; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="card-lift mb-4 border-l-4 border-l-accent p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <KeyRound size={15} className="text-accent" />
            <h2 className="text-sm font-bold text-ink">Signing secret for {hook.url}</h2>
          </div>
          <p className="mt-1 text-xs text-muted">
            Give this to the receiving system now; it is not shown again. It checks each
            request's <code>webhook-signature</code> with it (Standard Webhooks), and can ignore
            a <code>webhook-id</code> it has already seen: the same event may arrive twice.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <code className="num max-w-full break-all rounded-md bg-line/40 px-2 py-1 text-xs text-ink">
              {hook.secret}
            </code>
            <button
              className="btn-ghost btn-sm"
              onClick={() =>
                navigator.clipboard?.writeText(hook.secret).then(
                  () => setCopied(true),
                  () => setCopied(false),
                )
              }
            >
              <Copy size={13} /> {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
        <button aria-label="Dismiss" className="btn-ghost btn-sm" onClick={onClose}>
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

function NewWebhook({ onDone }: { onDone: (h: WebhookCreated) => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [events, setEvents] = useState<WebhookEvent[]>(["session.closed"]);
  const create = useMutation({
    mutationFn: () => api.createWebhook(url.trim(), events, description.trim()),
    onSuccess: (h) => {
      qc.invalidateQueries({ queryKey: ["webhooks"] });
      setOpen(false);
      setUrl("");
      setDescription("");
      onDone(h);
    },
  });
  if (!open) {
    return (
      <button className="btn-accent" onClick={() => setOpen(true)}>
        <Plus size={16} /> Add webhook
      </button>
    );
  }
  return (
    <form
      className="card mb-4 space-y-3 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <div className="flex flex-wrap gap-3">
        <div className="min-w-0 flex-1">
          <label htmlFor="hook-url" className="label">
            Receiver URL
          </label>
          <input
            id="hook-url"
            className="input w-full"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://erp.example.com/ivaas"
            required
          />
        </div>
        <div className="w-full sm:w-64">
          <label htmlFor="hook-description" className="label">
            What it is (optional)
          </label>
          <input
            id="hook-description"
            className="input w-full"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Dispatch ERP"
          />
        </div>
      </div>
      <fieldset>
        <legend className="label">Send when</legend>
        <div className="flex flex-wrap gap-4">
          {EVENTS.map((ev) => (
            <label key={ev.id} className="flex items-start gap-2 text-sm text-ink" title={ev.why}>
              <input
                type="checkbox"
                className="mt-1"
                checked={events.includes(ev.id)}
                onChange={(e) =>
                  setEvents((was) =>
                    e.target.checked ? [...was, ev.id] : was.filter((x) => x !== ev.id),
                  )
                }
              />
              <span>
                {ev.label}
                <span className="block text-xs text-muted">{ev.why}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-accent" disabled={create.isPending || !url.trim() || !events.length}>
          Create
        </button>
        <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <span className="text-xs text-muted">Public HTTPS only. Requests are signed.</span>
      </div>
      {create.error && (
        <div role="alert" className="text-xs text-bad">
          {(create.error as Error).message}
        </div>
      )}
    </form>
  );
}

function Status({ d }: { d: WebhookDelivery }) {
  if (d.status === "delivered") return <span className="chip bg-good/10 text-good">Delivered</span>;
  if (d.status === "failed") return <span className="chip bg-bad/10 text-bad">Gave up</span>;
  return (
    <span className="chip bg-warn/10 text-warn" title={d.next_attempt_at ? `Next try ${dateTime(d.next_attempt_at)}` : ""}>
      {d.attempts ? "Retrying" : "Queued"}
    </span>
  );
}

function Deliveries({ hook }: { hook: Webhook }) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["webhook-deliveries", hook.id],
    queryFn: () => api.webhookDeliveries(hook.id),
    refetchInterval: 10_000,
  });
  const replay = useMutation({
    mutationFn: api.replayDelivery,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["webhook-deliveries", hook.id] }),
  });
  const rows = list.data ?? [];
  if (list.isPending) return <div className="px-4 py-3 text-sm text-muted">Loading…</div>;
  if (!rows.length) {
    return (
      <div className="px-4 py-3 text-sm text-muted">
        Nothing sent yet. Send a test, or wait for the next event it subscribes to.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs text-muted">
          <tr>
            <th className="px-4 py-2 font-semibold">Event</th>
            <th className="px-2 py-2 font-semibold">Status</th>
            <th className="px-2 py-2 font-semibold">Tries</th>
            <th className="px-2 py-2 font-semibold">Last answer</th>
            <th className="px-2 py-2 font-semibold">Queued</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((d) => (
            <tr key={d.id}>
              <td className="px-4 py-2 text-ink">
                {LABEL[d.event] ?? d.event}
                {d.replay_of && <span className="ml-1 text-xs text-muted">(replay)</span>}
              </td>
              <td className="px-2 py-2">
                <Status d={d} />
              </td>
              <td className="num px-2 py-2 text-muted">{d.attempts}</td>
              <td className="max-w-xs truncate px-2 py-2 text-xs text-muted" title={d.last_error ?? ""}>
                {d.last_status_code ?? (d.last_error ? "no answer" : "")}
                {d.last_error ? ` · ${d.last_error}` : ""}
              </td>
              <td className="num whitespace-nowrap px-2 py-2 text-xs text-muted">{dateTime(d.created_at)}</td>
              <td className="px-4 py-2 text-right">
                {d.status !== "pending" && (
                  <button
                    className="btn-ghost btn-sm"
                    disabled={replay.isPending}
                    onClick={() => replay.mutate(d.id)}
                    title="Send the same event again; the receiver sees the same webhook-id"
                  >
                    <RotateCcw size={13} /> Replay
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Endpoint({ hook }: { hook: Webhook }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const test = useMutation({
    mutationFn: () => api.testWebhook(hook.id),
    onSuccess: () => {
      setOpen(true);
      qc.invalidateQueries({ queryKey: ["webhook-deliveries", hook.id] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteWebhook(hook.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["webhooks"] }),
  });
  return (
    <li className="card overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
        <div className="min-w-0">
          <div className="num break-all text-sm font-semibold text-ink">{hook.url}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
            {hook.description && <span className="text-ink">{hook.description} ·</span>}
            {hook.events.map((e) => (
              <span key={e} className="chip bg-brand/10 text-brand">
                {LABEL[e] ?? e}
              </span>
            ))}
            <span>
              added by {hook.created_by}, {dateTime(hook.created_at)}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost btn-sm" disabled={test.isPending} onClick={() => test.mutate()}>
            <Send size={13} /> Send test
          </button>
          <button className="btn-ghost btn-sm" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? "Hide deliveries" : "Deliveries"}
          </button>
          <button
            className={`btn-ghost btn-sm ${confirming ? "text-bad" : ""}`}
            disabled={remove.isPending}
            onClick={() => (confirming ? remove.mutate() : setConfirming(true))}
            onBlur={() => setConfirming(false)}
          >
            <Trash2 size={13} /> {confirming ? "Remove it and its history?" : "Remove"}
          </button>
        </div>
      </div>
      {(test.error || remove.error) && (
        <div role="alert" className="px-4 pb-2 text-xs text-bad">
          {((test.error || remove.error) as Error).message}
        </div>
      )}
      {open && (
        <div className="border-t border-line">
          <Deliveries hook={hook} />
        </div>
      )}
    </li>
  );
}

/**
 * Where the platform tells a tenant's own systems what happened: a load closed, a
 * manifest disagreed. Each request is signed; each is retried until it lands or the
 * schedule runs out, and can be replayed from here.
 */
export default function Webhooks() {
  const [created, setCreated] = useState<WebhookCreated | null>(null);
  const list = useQuery({ queryKey: ["webhooks"], queryFn: api.webhooks });
  const hooks = list.data ?? [];
  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Webhooks</h1>
          <p className="mt-0.5 text-sm text-muted">
            Send loads and manifest exceptions to another system as they happen: an ERP, a
            dispatch board. Failed requests are tried again for about 19 hours.
          </p>
        </div>
        {!created && <NewWebhook onDone={setCreated} />}
      </div>
      {created && <OneTimeSecret hook={created} onClose={() => setCreated(null)} />}
      {list.isPending ? (
        <div className="card px-4 py-6 text-sm text-muted">Loading…</div>
      ) : hooks.length ? (
        <ul className="space-y-3">
          {hooks.map((h) => (
            <Endpoint key={h.id} hook={h} />
          ))}
        </ul>
      ) : (
        <div className="card">
          <EmptyState
            title="No webhooks"
            body="Nothing is sent anywhere. The daily reports carry the same figures; a webhook is for a system that wants each load as it closes."
          />
        </div>
      )}
    </>
  );
}
