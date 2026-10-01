import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { BillingSubscription, InvoiceView, StatementView } from "../api/types";
import { type Me, can } from "../auth/session";
import { EmptyState, dateTime } from "../components/ui";

const thisMonth = () => new Date().toISOString().slice(0, 7);
const today = () => new Date().toISOString().slice(0, 10);

function Meter({ label, used, limit, unit = "" }: { label: string; used: number; limit: number; unit?: string }) {
  const share = limit > 0 ? Math.min(1, used / limit) : 0;
  const over = used > limit;
  return (
    <div>
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-ink">{label}</span>
        <span className={`num ${over ? "text-warn" : "text-muted"}`}>
          {used.toLocaleString()} of {limit.toLocaleString()}
          {unit}
          {over && " (overage)"}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line">
        <div className={`h-full rounded-full ${over ? "bg-warn" : "bg-brand"}`} style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

function Plan({ sub, canChange }: { sub: BillingSubscription; canChange: boolean }) {
  const e = sub.entitlements!;
  const used = sub.usage_this_month;
  return (
    <section className="card p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-bold text-ink">Plan: {e.plan}</h2>
        {e.valid_until && <span className="text-xs text-muted">Trial until {dateTime(e.valid_until)}</span>}
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Meter label="Counting channels" used={sub.channels_in_use.od} limit={e.limits.od_channels ?? 0} />
        <Meter label="Plate-reading channels" used={sub.channels_in_use.lpr} limit={e.limits.lpr_channels ?? 0} />
        <Meter
          label="Storage this month"
          used={Number(used.storage_gb_month ?? 0)}
          limit={e.allowances.storage_gb_month ?? 0}
          unit=" GB-month"
        />
        <Meter label="Assistant tokens this month" used={Number(used.assistant_tokens ?? 0)} limit={e.allowances.assistant_tokens ?? 0} />
      </div>
      <p className="mt-3 text-xs text-muted">
        A channel past the plan is refused when it is added; storage and assistant use past the allowance are allowed and
        billed as overage.
      </p>
      {canChange && <ChangePlan sub={sub} />}
    </section>
  );
}

function ChangePlan({ sub }: { sub: BillingSubscription }) {
  const qc = useQueryClient();
  const book = useQuery({ queryKey: ["price-book"], queryFn: api.priceBook });
  const e = sub.entitlements!;
  const [plan, setPlan] = useState(e.plan);
  const [od, setOd] = useState(String(e.limits.od_channels ?? 0));
  const [lpr, setLpr] = useState(String(e.limits.lpr_channels ?? 0));
  const save = useMutation({
    mutationFn: () => api.changePlan(plan, { "ivaas-od-count": Number(od), "ivaas-lpr": Number(lpr) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["billing"] }),
  });
  return (
    <form
      className="mt-4 flex flex-wrap items-end gap-2 border-t border-line pt-3"
      onSubmit={(ev) => {
        ev.preventDefault();
        save.mutate();
      }}
    >
      <div>
        <label htmlFor="plan" className="label">
          Plan
        </label>
        <select id="plan" className="input h-9 w-40" value={plan} onChange={(ev) => setPlan(ev.target.value)}>
          {(book.data?.plans ?? [{ id: e.plan, name: e.plan }]).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="od" className="label">
          Counting channels
        </label>
        <input id="od" type="number" min={0} className="input h-9 w-28" value={od} onChange={(ev) => setOd(ev.target.value)} />
      </div>
      <div>
        <label htmlFor="lpr" className="label">
          Plate-reading
        </label>
        <input id="lpr" type="number" min={0} className="input h-9 w-28" value={lpr} onChange={(ev) => setLpr(ev.target.value)} />
      </div>
      <button className="btn-accent" disabled={save.isPending}>
        Change from now
      </button>
      <span className="text-xs text-muted">Charged by the day for the rest of the month.</span>
      {save.error && (
        <span role="alert" className="w-full text-xs text-bad">
          {(save.error as Error).message}
        </span>
      )}
    </form>
  );
}

export function Lines({ lines }: { lines: { description: string; quantity: string; amount?: string }[] }) {
  return (
    <ul className="divide-y divide-line text-sm">
      {lines.map((l, i) => (
        <li key={i} className="flex justify-between gap-3 py-1.5">
          <span className="text-ink">{l.description}</span>
          <span className="num shrink-0 text-muted">{l.amount ?? `x ${l.quantity}`}</span>
        </li>
      ))}
    </ul>
  );
}

export function Draft({ inv }: { inv: Pick<InvoiceView, "stamp" | "lines" | "subtotal" | "tax_name" | "tax" | "total" | "currency"> }) {
  return (
    <>
      {inv.stamp && <p className="mb-2 text-xs font-semibold text-warn">{inv.stamp}</p>}
      <Lines lines={inv.lines} />
      <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-4 text-sm">
        <dt className="text-muted">Subtotal</dt>
        <dd className="num text-right">{inv.subtotal}</dd>
        <dt className="text-muted">{inv.tax_name}</dt>
        <dd className="num text-right">{inv.tax}</dd>
        <dt className="font-semibold text-ink">Total</dt>
        <dd className="num text-right font-semibold text-ink">
          {inv.total} {inv.currency}
        </dd>
      </dl>
    </>
  );
}

function Statement({ st }: { st: StatementView }) {
  return (
    <>
      <p className="mb-2 text-xs text-muted">
        {st.billed_by} invoices you for these on its own paper; this is what you used, without prices.
      </p>
      <Lines lines={st.lines} />
    </>
  );
}

function ThisMonth() {
  const period = thisMonth();
  const draft = useQuery({ queryKey: ["billing", "draft", period], queryFn: () => api.billingDraft(period), retry: false });
  const partnerBilled = draft.isError && /partner invoices you/.test((draft.error as Error).message);
  const statement = useQuery({
    queryKey: ["billing", "statement", period],
    queryFn: () => api.billingStatement(period),
    enabled: partnerBilled,
  });
  return (
    <section className="card p-4">
      <h2 className="mb-2 text-sm font-bold text-ink">This month so far</h2>
      {draft.isPending ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : draft.data ? (
        <Draft inv={draft.data} />
      ) : statement.data ? (
        <Statement st={statement.data} />
      ) : (
        <p className="text-sm text-muted">{partnerBilled ? "Loading…" : (draft.error as Error).message}</p>
      )}
    </section>
  );
}

export function InvoiceStatus({ inv }: { inv: Pick<InvoiceView, "settled" | "due_date"> }) {
  if (inv.settled) return <span className="chip bg-good/10 text-good">Paid</span>;
  const late = inv.due_date !== null && inv.due_date < today();
  return (
    <span className={`chip ${late ? "bg-bad/10 text-bad" : "bg-warn/10 text-warn"}`}>
      {late ? "Overdue" : "Due"} {inv.due_date}
    </span>
  );
}

/**
 * The tenant's plan and its limits, what it has used this month, and its invoices. A
 * tenant its partner bills sees its usage, without Cassava's prices, instead.
 */
export default function Billing({ me }: { me: Me | undefined }) {
  const sub = useQuery({ queryKey: ["billing"], queryFn: api.billing });
  const book = useQuery({ queryKey: ["price-book"], queryFn: api.priceBook });
  const invoices = useQuery({ queryKey: ["billing", "invoices"], queryFn: api.invoices });
  return (
    <>
      <div className="mb-4">
        <h1 className="text-2xl font-extrabold tracking-tight text-ink">Billing</h1>
        <p className="mt-0.5 text-sm text-muted">Your plan and what it allows, this month's use, and your invoices.</p>
      </div>
      {book.data?.placeholder && (
        <p role="note" className="mb-4 flex items-center gap-2 rounded-lg bg-warn/10 px-3 py-2 text-sm text-warn">
          <AlertTriangle size={15} /> These prices are placeholders, not agreed ones: nothing here is for issue.
        </p>
      )}
      {sub.isPending ? (
        <div className="card px-4 py-6 text-sm text-muted">Loading…</div>
      ) : !sub.data?.subscribed ? (
        <div className="card">
          <EmptyState title="No plan" body="Nothing is limited and nothing is billed until a plan is set." />
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Plan sub={sub.data} canChange={can(me, "subscription.manage")} />
          <ThisMonth />
        </div>
      )}
      {(invoices.data?.length ?? 0) > 0 && (
        <section className="card mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line">
                <th className="th">Invoice</th>
                <th className="th">Period</th>
                <th className="th text-right">Total</th>
                <th className="th text-right">Paid</th>
                <th className="th" />
              </tr>
            </thead>
            <tbody>
              {invoices.data!.map((inv) => (
                <tr key={inv.number} className="border-b border-line last:border-0">
                  <td className="td num font-semibold text-ink">{inv.number}</td>
                  <td className="td text-muted">
                    {inv.period_start} to {inv.period_end}
                  </td>
                  <td className="td num text-right">
                    {inv.total} {inv.currency}
                  </td>
                  <td className="td num text-right">{inv.paid}</td>
                  <td className="td text-right">
                    <InvoiceStatus inv={inv} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
