import { useMutation, useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { type Me, can } from "../auth/session";
import { PageHeader, dateTime } from "../components/ui";

/**
 * The owner's account: take everything away, and end the account. Once cancelled this
 * is the only page left, until the data is purged.
 */
export default function Account({ me }: { me: Me | undefined }) {
  const owner = can(me, "data.export");
  const lc = useQuery({ queryKey: ["account", "lifecycle"], queryFn: api.myLifecycle, enabled: owner });
  const exporting = useMutation({ mutationFn: api.exportMine });
  const [confirm, setConfirm] = useState("");
  const [reason, setReason] = useState("");
  const cancel = useMutation({
    mutationFn: () => api.cancelMine(confirm, reason),
    // the whole portal changes: everything but this page closes
    onSuccess: () => window.location.assign("/account"),
  });
  if (!owner) {
    return (
      <div className="card px-4 py-6 text-sm text-muted">
        {me?.tenant?.status === "cancelled"
          ? `${me.tenant.name} has been cancelled. Its owner can still export its data until it is deleted.`
          : "Only the account's owner can export it or cancel it."}
      </div>
    );
  }
  if (lc.isPending) return <div className="card px-4 py-6 text-sm text-muted">Loading…</div>;
  if (lc.error) return <div className="card px-4 py-6 text-sm text-bad">{(lc.error as Error).message}</div>;
  const t = lc.data!;
  const cancelled = t.status === "cancelled";
  return (
    <>
      <PageHeader title="Account" subtitle={`${t.name} · ${t.slug}`} />
      {cancelled && (
        <p role="alert" className="mb-4 rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad">
          This account was cancelled {t.cancelled_at && dateTime(t.cancelled_at)}
          {t.cancelled_by && ` by ${t.cancelled_by}`}. Its data is kept until{" "}
          {t.purge_after ? dateTime(t.purge_after) : "the retention window ends"}, then deleted for good. Export it
          before then.
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <section aria-label="Export" className="card p-4">
          <h2 className="text-sm font-bold text-ink">Export everything</h2>
          <p className="mt-1 text-sm text-muted">
            One file with every record as JSON and CSV, every evidence clip, upload and report, and a manifest of
            counts and checksums. Passwords, keys, camera addresses and face data are never included; the manifest
            lists what was held back.
          </p>
          {t.export_available ? (
            <button className="btn-accent mt-3" disabled={exporting.isPending} onClick={() => exporting.mutate()}>
              <Download size={15} /> {exporting.isPending ? "Preparing…" : "Download the export"}
            </button>
          ) : (
            <p className="mt-3 text-sm text-warn">This installation cannot export: it is not running on its database.</p>
          )}
          {exporting.error && (
            <p role="alert" className="mt-2 text-xs text-bad">
              {(exporting.error as Error).message}
            </p>
          )}
        </section>
        {!cancelled && (
          <section aria-label="Cancel" className="card p-4">
            <h2 className="text-sm font-bold text-ink">Cancel the account</h2>
            <p className="mt-1 text-sm text-muted">
              Counting stops, and everything but this page closes. The data is kept for {t.retention_days} days so you
              can export it, then deleted for good, with a certificate. Cassava can reinstate the account until then.
            </p>
            <form
              className="mt-3 space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                cancel.mutate();
              }}
            >
              <div>
                <label htmlFor="cancel-reason" className="label">
                  Why (optional)
                </label>
                <input id="cancel-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
              </div>
              <div>
                <label htmlFor="cancel-confirm" className="label">
                  Type {t.slug} to confirm
                </label>
                <input
                  id="cancel-confirm"
                  className="input"
                  value={confirm}
                  autoComplete="off"
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </div>
              <button className="btn-ghost text-bad" disabled={confirm !== t.slug || cancel.isPending}>
                Cancel the account
              </button>
              {cancel.error && (
                <p role="alert" className="text-xs text-bad">
                  {(cancel.error as Error).message}
                </p>
              )}
            </form>
          </section>
        )}
      </div>
    </>
  );
}
