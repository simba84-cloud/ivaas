import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { InvoiceView, TenantRecord } from "../api/types";
import { type Me, can } from "../auth/session";
import { OnboardingProgress } from "../components/OnboardingProgress";
import { PageHeader, dateTime } from "../components/ui";
import { Draft, InvoiceStatus } from "./Billing";
import { TenantStatus } from "./Console";

export const isPlatform = (me: Me | undefined) => !!me?.roles.some((r) => r.startsWith("platform_"));

/** The last finished month: the one that can be invoiced. */
export function lastMonth(now = new Date()): string {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
  return d.toISOString().slice(0, 7);
}

function Plan({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const sub = useQuery({ queryKey: ["console", tenantId, "plan"], queryFn: () => api.tenantPlan(tenantId) });
  const book = useQuery({ queryKey: ["price-book"], queryFn: api.priceBook });
  const [plan, setPlan] = useState("");
  const current = sub.data?.entitlements?.plan ?? "";
  const chosen = plan || current || book.data?.plans[0]?.id || "";
  const save = useMutation({
    mutationFn: () => api.setTenantPlan(tenantId, chosen, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["console", tenantId] }),
  });
  return (
    <section aria-label="Plan" className="card p-4">
      <h2 className="mb-2 text-sm font-bold text-ink">Plan</h2>
      {sub.isPending ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : !sub.data?.subscribed ? (
        <p className="text-sm text-muted">No plan: nothing is limited and nothing is billed.</p>
      ) : (
        <p className="text-sm text-ink">
          {book.data?.plans.find((p) => p.id === current)?.name ?? current}: {sub.data.channels_in_use.od} of{" "}
          {sub.data.entitlements!.limits.od_channels ?? 0} counting channels, {sub.data.channels_in_use.lpr} of{" "}
          {sub.data.entitlements!.limits.lpr_channels ?? 0} plate-reading
          {sub.data.entitlements!.valid_until && <>; trial ends {sub.data.entitlements!.valid_until.slice(0, 10)}</>}
        </p>
      )}
      <form
        className="mt-3 flex flex-wrap items-end gap-2 border-t border-line pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div>
          <label htmlFor="console-plan" className="label">
            {sub.data?.subscribed ? "Change to" : "Plan"}
          </label>
          <select id="console-plan" className="input h-9 w-48" value={chosen} onChange={(e) => setPlan(e.target.value)}>
            {book.data?.plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <button className="btn-accent" disabled={!chosen || save.isPending}>
          {sub.data?.subscribed ? "Change from now" : "Set plan"}
        </button>
        {save.error && (
          <span role="alert" className="w-full text-xs text-bad">
            {(save.error as Error).message}
          </span>
        )}
      </form>
    </section>
  );
}

function Pay({ tenantId, inv }: { tenantId: string; inv: InvoiceView }) {
  const qc = useQueryClient();
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const pay = useMutation({
    mutationFn: () => api.payTenantInvoice(tenantId, inv.number!, amount, reference),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["console"] }),
  });
  return (
    <form
      className="flex flex-wrap items-center justify-end gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        pay.mutate();
      }}
    >
      <input
        aria-label={`Amount paid on ${inv.number}`}
        className="input h-8 w-24"
        inputMode="decimal"
        value={amount}
        onChange={(e) => setAmount(e.target.value)}
        required
      />
      <input
        aria-label={`Bank reference for ${inv.number}`}
        placeholder="Bank reference"
        className="input h-8 w-32"
        value={reference}
        onChange={(e) => setReference(e.target.value)}
        required
      />
      <button className="btn-ghost btn-sm" disabled={pay.isPending}>
        Record payment
      </button>
      {pay.error && (
        <span role="alert" className="w-full text-right text-xs text-bad">
          {(pay.error as Error).message}
        </span>
      )}
    </form>
  );
}

function Invoices({ tenant }: { tenant: TenantRecord }) {
  const qc = useQueryClient();
  const [period, setPeriod] = useState(lastMonth());
  const list = useQuery({ queryKey: ["console", tenant.id, "invoices"], queryFn: () => api.tenantInvoices(tenant.id) });
  const draft = useQuery({
    queryKey: ["console", tenant.id, "draft", period],
    queryFn: () => api.tenantDraft(tenant.id, period),
    retry: false,
  });
  const issue = useMutation({
    mutationFn: () => api.issueTenantInvoice(tenant.id, period),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["console", tenant.id] }),
  });
  return (
    <section aria-label="Invoices" className="card p-4">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <h2 className="text-sm font-bold text-ink">Invoices</h2>
        <div className="flex items-end gap-2">
          <div>
            <label htmlFor="console-period" className="label">
              Month
            </label>
            <input
              id="console-period"
              type="month"
              className="input h-9"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
            />
          </div>
          <button className="btn-accent" disabled={!draft.data || issue.isPending} onClick={() => issue.mutate()}>
            Issue
          </button>
        </div>
      </div>
      {issue.error && (
        <p role="alert" className="mb-2 text-xs text-bad">
          {(issue.error as Error).message}
        </p>
      )}
      {draft.data ? (
        <Draft inv={draft.data} />
      ) : (
        <p className="text-sm text-muted">{draft.isPending ? "Loading…" : (draft.error as Error).message}</p>
      )}
      {(list.data?.length ?? 0) > 0 && (
        <table className="mt-4 w-full border-t border-line text-sm">
          <tbody>
            {list.data!.map((inv) => (
              <tr key={inv.number} className="border-b border-line last:border-0">
                <td className="td num font-semibold text-ink">{inv.number}</td>
                <td className="td num text-right">
                  {inv.total} {inv.currency}
                </td>
                <td className="td text-right">
                  <InvoiceStatus inv={inv} />
                </td>
                <td className="td">{!inv.settled && <Pay tenantId={tenant.id} inv={inv} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Hold({ tenant }: { tenant: TenantRecord }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const hold = useMutation({
    mutationFn: () => api.holdTenant(tenant.id, !tenant.on_hold, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["console"] }),
  });
  return (
    <section aria-label="Hold" className="card p-4">
      <h2 className="mb-1 text-sm font-bold text-ink">Hold</h2>
      <p className="text-xs text-muted">
        {tenant.on_hold
          ? "Held: its people can look but not change anything. Counting carries on."
          : "Holding a customer suspends it until the hold is lifted, whatever it has paid. Counting carries on."}
      </p>
      <form
        className="mt-2 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          hold.mutate();
        }}
      >
        {!tenant.on_hold && (
          <input
            aria-label="Reason"
            placeholder="Reason, for the audit log"
            className="input h-9 w-64"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        )}
        <button className={tenant.on_hold ? "btn-accent" : "btn-ghost"} disabled={hold.isPending}>
          {tenant.on_hold ? "Lift the hold" : "Hold this customer"}
        </button>
      </form>
    </section>
  );
}

/**
 * The end of a tenant: cancel (the owner can too), reinstate within the window, and,
 * once it has passed, purge. Each asks for the tenant's short name: none is a click.
 */
function Lifecycle({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const lc = useQuery({ queryKey: ["console", tenantId, "lifecycle"], queryFn: () => api.tenantLifecycle(tenantId) });
  const [confirm, setConfirm] = useState("");
  const [reason, setReason] = useState("");
  const done = () => {
    setConfirm("");
    qc.invalidateQueries({ queryKey: ["console"] });
  };
  const change = useMutation({
    mutationFn: (action: "cancel" | "reinstate") => api.changeLifecycle(tenantId, action, confirm, reason),
    onSuccess: done,
  });
  const purge = useMutation({ mutationFn: () => api.purgeTenant(tenantId, confirm), onSuccess: done });
  if (!lc.data) return null;
  const t = lc.data;
  const cancelled = t.status === "cancelled";
  const due = t.purge_after !== null && Date.parse(t.purge_after) <= Date.now();
  const error = (change.error ?? purge.error) as Error | null;
  if (purge.data) {
    return (
      <section aria-label="Lifecycle" className="card p-4">
        <h2 className="text-sm font-bold text-ink">Purged</h2>
        <p className="mt-1 text-sm text-muted">
          Everything of {purge.data.tenant_name} is gone: {purge.data.body.scan.rows_remaining} rows and{" "}
          {purge.data.body.scan.objects_remaining} objects left after. Its certificate is under{" "}
          <Link to="/console/certificates" className="font-semibold text-ink hover:underline">
            Deletion certificates
          </Link>
          .
        </p>
      </section>
    );
  }
  return (
    <section aria-label="Lifecycle" className="card p-4">
      <h2 className="mb-1 text-sm font-bold text-ink">Cancellation</h2>
      <p className="text-xs text-muted">
        {cancelled
          ? `Cancelled ${t.cancelled_at ? dateTime(t.cancelled_at) : ""}${t.cancelled_by ? ` by ${t.cancelled_by}` : ""}. Its owner can still export; its data may be purged from ${t.purge_after ? dateTime(t.purge_after) : "the end of the window"}.`
          : `Cancelling stops counting and closes the portal except the owner's export. The data is kept ${t.retention_days} days, then may be purged.`}
      </p>
      <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(e) => e.preventDefault()}>
        {!cancelled && (
          <input
            aria-label="Reason"
            placeholder="Reason, for the audit log"
            className="input h-9 w-56"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        )}
        {(!cancelled || due) && (
          <input
            aria-label={`Type ${t.slug} to confirm`}
            placeholder={`Type ${t.slug} to confirm`}
            className="input h-9 w-48"
            autoComplete="off"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        )}
        {!cancelled && (
          <button
            className="btn-ghost text-bad"
            disabled={confirm !== t.slug || change.isPending}
            onClick={() => change.mutate("cancel")}
          >
            Cancel tenant
          </button>
        )}
        {cancelled && (
          <button className="btn-ghost" disabled={change.isPending} onClick={() => change.mutate("reinstate")}>
            Reinstate
          </button>
        )}
        {cancelled && due && (
          <button
            className="btn-accent"
            disabled={confirm !== t.slug || purge.isPending}
            onClick={() => purge.mutate()}
          >
            Purge for good
          </button>
        )}
      </form>
      {error && (
        <p role="alert" className="mt-2 text-xs text-bad">
          {error.message}
        </p>
      )}
    </section>
  );
}

/** One tenant, as its provisioner sees it: the install, the plan, its bills, a hold. */
export default function ConsoleTenant({ me }: { me: Me | undefined }) {
  const { id = "" } = useParams();
  const tenant = useQuery({ queryKey: ["console", id, "tenant"], queryFn: () => api.tenant(id) });
  const partners = useQuery({ queryKey: ["console", "partners"], queryFn: api.partners });
  if (tenant.isPending) return <div className="card px-4 py-6 text-sm text-muted">Loading…</div>;
  if (tenant.error) return <div className="card px-4 py-6 text-sm text-bad">{(tenant.error as Error).message}</div>;
  const t = tenant.data!;
  const partner = t.partner_id ? partners.data?.find((p) => p.id === t.partner_id) : undefined;
  const billing = can(me, "subscription.manage");
  return (
    <>
      <PageHeader
        title={t.name}
        subtitle={`${t.slug} · ${t.partner_id ? `billed through ${partner?.name ?? "its partner"}` : "billed by Cassava directly"}`}
        actions={
          <div className="flex items-center gap-2">
            <TenantStatus t={t} />
            <Link to="/console" className="btn-ghost">
              All tenants
            </Link>
          </div>
        }
      />
      <div className="grid gap-4 xl:grid-cols-2">
        <OnboardingProgress tenantId={t.id} />
        <div className="space-y-4">
          {billing && <Plan tenantId={t.id} />}
          {billing && !t.partner_id && <Invoices tenant={t} />}
          {billing && t.partner_id && isPlatform(me) && (
            <p className="card px-4 py-3 text-sm text-muted">
              {partner?.name ?? "Its partner"} invoices this customer. Cassava bills the partner wholesale: see{" "}
              <Link to="/console/partners" className="font-semibold text-ink hover:underline">
                Partner invoices
              </Link>
              .
            </p>
          )}
          {billing && <Hold tenant={t} />}
          {can(me, "tenant.suspend") && <Lifecycle tenantId={t.id} />}
        </div>
      </div>
    </>
  );
}
