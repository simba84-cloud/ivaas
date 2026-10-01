import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { TenantRecord } from "../api/types";
import { type Me, can } from "../auth/session";
import { EmptyState, PageHeader, dateTime } from "../components/ui";

const TONE: Record<TenantRecord["status"], string> = {
  provisioning: "bg-ground text-muted",
  trial: "bg-brand/10 text-brand",
  active: "bg-good/10 text-good",
  past_due: "bg-warn/10 text-warn",
  suspended: "bg-bad/10 text-bad",
  expired: "bg-ground text-muted",
  cancelled: "bg-ground text-muted",
};

export function TenantStatus({ t }: { t: TenantRecord }) {
  return (
    <span className={`chip ${TONE[t.status]}`}>
      {t.status.replace("_", " ")}
      {t.on_hold && " (held)"}
    </span>
  );
}

/**
 * The tenants a platform admin or partner admin looks after: every one for Cassava,
 * a partner's own customers for a partner. The API decides which; this lists them.
 */
export default function Console({ me }: { me: Me | undefined }) {
  const tenants = useQuery({ queryKey: ["console", "tenants"], queryFn: api.tenants });
  const partners = useQuery({ queryKey: ["console", "partners"], queryFn: api.partners });
  const partnerName = (id: string | null) =>
    id ? (partners.data?.find((p) => p.id === id)?.name ?? "A partner") : "Cassava directly";
  return (
    <>
      <PageHeader
        title="Tenants"
        subtitle="Customers, how they are billed and where each stands."
        actions={
          can(me, "tenant.create") && (
            <Link to="/console/onboard" className="btn-accent">
              <Plus size={15} /> Onboard a tenant
            </Link>
          )
        }
      />
      <section className="card overflow-x-auto">
        {tenants.isPending ? (
          <p className="px-4 py-6 text-sm text-muted">Loading…</p>
        ) : tenants.error ? (
          <p className="px-4 py-6 text-sm text-bad">{(tenants.error as Error).message}</p>
        ) : !tenants.data!.length ? (
          <EmptyState title="No tenants yet" body="Onboard the first customer to see it here." />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line">
                <th className="th">Tenant</th>
                <th className="th">Billed through</th>
                <th className="th">Created</th>
                <th className="th text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {tenants.data!.map((t) => (
                <tr key={t.id} className="border-b border-line last:border-0">
                  <td className="td">
                    <Link to={`/console/tenants/${t.id}`} className="font-semibold text-ink hover:underline">
                      {t.name}
                    </Link>
                    <div className="num text-xs text-muted">{t.slug}</div>
                  </td>
                  <td className="td text-muted">{partnerName(t.partner_id)}</td>
                  <td className="td text-muted">{t.created_at ? dateTime(t.created_at) : "not recorded"}</td>
                  <td className="td text-right">
                    <TenantStatus t={t} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
