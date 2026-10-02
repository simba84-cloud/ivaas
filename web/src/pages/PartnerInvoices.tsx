import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { PartnerInvoiceView, PartnerRecord } from "../api/types";
import { type Me } from "../auth/session";
import { EmptyState, PageHeader } from "../components/ui";
import { InvoiceStatus, Lines } from "./Billing";
import { isPlatform, lastMonth } from "./ConsoleTenant";

/** The wholesale figure and, under it, one block per customer, to re-bill from. */
function Breakdown({ inv }: { inv: PartnerInvoiceView }) {
  return (
    <>
      {inv.stamp && <p className="mb-2 text-xs font-semibold text-warn">{inv.stamp}</p>}
      {inv.customers.length === 0 ? (
        <p className="text-sm text-muted">No customer of this partner had a plan in this month: nothing to bill.</p>
      ) : (
        inv.customers.map((c) => (
          <div key={c.tenant_id} className="mb-3">
            <div className="flex justify-between text-sm font-semibold text-ink">
              <span>{c.tenant_name}</span>
              <span className="num">{c.subtotal}</span>
            </div>
            <Lines lines={c.lines} />
          </div>
        ))
      )}
      <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-4 border-t border-line pt-2 text-sm">
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

function Pay({ inv }: { inv: PartnerInvoiceView }) {
  const qc = useQueryClient();
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const pay = useMutation({
    mutationFn: () => api.payPartnerInvoice(inv.number!, amount, reference),
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

/**
 * What the partner keeps on each customer if it re-bills at Cassava's list prices.
 * What it actually charges is between it and its customer, so that is all this says.
 */
function Commission({ partner, period }: { partner: PartnerRecord; period: string }) {
  const st = useQuery({
    queryKey: ["console", "partner", partner.id, "commission", period],
    queryFn: () => api.partnerCommission(partner.id, period),
    retry: false,
  });
  if (st.isPending) return null;
  if (st.error) return <p className="mt-4 text-sm text-muted">{(st.error as Error).message}</p>;
  const c = st.data!;
  const pct = `${Math.round(Number(c.discount) * 100)}%`;
  return (
    <div aria-label={`${partner.name} commission`} className="mt-4 border-t border-line pt-3">
      <h3 className="text-sm font-bold text-ink">Commission statement</h3>
      <p className="mt-0.5 text-xs text-muted">
        At Cassava's list prices, less your {pct} wholesale discount, {c.tax_note}. What you charge your customers is
        your own business: this is the margin list price would give.
        {c.wholesale_invoice ? ` Wholesale invoice ${c.wholesale_invoice}.` : " The wholesale invoice for this month is not issued yet."}
      </p>
      {c.lines.length === 0 ? (
        <p className="mt-2 text-sm text-muted">No customer had anything billable this month: nothing to report.</p>
      ) : (
        <table className="mt-2 w-full text-sm">
          <thead>
            <tr className="border-b border-line">
              <th className="th">Customer</th>
              <th className="th text-right">At list</th>
              <th className="th text-right">Wholesale</th>
              <th className="th text-right">Margin</th>
            </tr>
          </thead>
          <tbody>
            {c.lines.map((x) => (
              <tr key={x.tenant_id} className="border-b border-line">
                <td className="td text-ink">{x.tenant_name}</td>
                <td className="td num text-right">{x.at_list}</td>
                <td className="td num text-right text-muted">{x.at_wholesale}</td>
                <td className="td num text-right">{x.margin}</td>
              </tr>
            ))}
            <tr>
              <td className="td font-semibold text-ink">Total</td>
              <td className="td num text-right font-semibold">{c.at_list}</td>
              <td className="td num text-right text-muted">{c.at_wholesale}</td>
              <td className="td num text-right font-semibold text-ink">
                {c.margin} {c.currency}
              </td>
            </tr>
          </tbody>
        </table>
      )}
    </div>
  );
}

function Partner({ partner, period, cassava }: { partner: PartnerRecord; period: string; cassava: boolean }) {
  const qc = useQueryClient();
  const draft = useQuery({
    queryKey: ["console", "partner", partner.id, "draft", period],
    queryFn: () => api.partnerDraft(partner.id, period),
    retry: false,
  });
  const issued = useQuery({
    queryKey: ["console", "partner", partner.id, "invoices"],
    queryFn: () => api.partnerInvoices(partner.id),
  });
  const issue = useMutation({
    mutationFn: () => api.issuePartnerInvoice(partner.id, period),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["console", "partner", partner.id] }),
  });
  return (
    <section aria-label={partner.name} className="card p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-bold text-ink">{partner.name}</h2>
        {cassava && (
          <button className="btn-accent" disabled={!draft.data || issue.isPending} onClick={() => issue.mutate()}>
            Issue {period}
          </button>
        )}
      </div>
      {issue.error && (
        <p role="alert" className="mb-2 text-xs text-bad">
          {(issue.error as Error).message}
        </p>
      )}
      {draft.data ? (
        <Breakdown inv={draft.data} />
      ) : (
        <p className="text-sm text-muted">{draft.isPending ? "Loading…" : (draft.error as Error).message}</p>
      )}
      <Commission partner={partner} period={period} />
      {(issued.data?.length ?? 0) > 0 && (
        <table className="mt-4 w-full border-t border-line text-sm">
          <tbody>
            {issued.data!.map((inv) => (
              <tr key={inv.number} className="border-b border-line last:border-0">
                <td className="td num font-semibold text-ink">{inv.number}</td>
                <td className="td text-muted">
                  {inv.period_start} to {inv.period_end}
                </td>
                <td className="td num text-right">
                  {inv.total} {inv.currency}
                </td>
                <td className="td text-right">
                  <InvoiceStatus inv={inv} />
                </td>
                {cassava && <td className="td">{!inv.settled && <Pay inv={inv} />}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/**
 * Cassava's wholesale invoices to its partners, each broken down by customer. Cassava
 * issues them and records payment; a partner sees its own, to re-bill its customers.
 */
export default function PartnerInvoices({ me }: { me: Me | undefined }) {
  const partners = useQuery({ queryKey: ["console", "partners"], queryFn: api.partners });
  const book = useQuery({ queryKey: ["price-book"], queryFn: api.priceBook });
  const [period, setPeriod] = useState(lastMonth());
  const cassava = isPlatform(me);
  return (
    <>
      <PageHeader
        title="Partner invoices"
        subtitle={
          cassava
            ? "What each partner owes Cassava wholesale, by customer."
            : "What Cassava bills you wholesale, by customer, to re-bill from."
        }
        actions={
          <div>
            <label htmlFor="partner-period" className="label">
              Month
            </label>
            <input
              id="partner-period"
              type="month"
              className="input h-9"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
            />
          </div>
        }
      />
      {book.data?.placeholder && (
        <p role="note" className="mb-4 flex items-center gap-2 rounded-lg bg-warn/10 px-3 py-2 text-sm text-warn">
          <AlertTriangle size={15} /> These prices are placeholders, not agreed ones: nothing here is for issue.
        </p>
      )}
      {partners.isPending ? (
        <div className="card px-4 py-6 text-sm text-muted">Loading…</div>
      ) : !partners.data?.length ? (
        <div className="card">
          <EmptyState title="No partners" body="Customers are all billed by Cassava directly." />
        </div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {partners.data.map((p) => (
            <Partner key={p.id} partner={p} period={period} cassava={cassava} />
          ))}
        </div>
      )}
    </>
  );
}
