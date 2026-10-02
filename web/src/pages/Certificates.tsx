import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { EmptyState, PageHeader, dateTime } from "../components/ui";

/**
 * What the platform has deleted for good: one certificate per purged tenant, with the
 * scan that found nothing left, and whether its signature still checks out.
 */
export default function Certificates() {
  const certs = useQuery({ queryKey: ["console", "certificates"], queryFn: api.certificates });
  return (
    <>
      <PageHeader
        title="Deletion certificates"
        subtitle="Each purged tenant, what was deleted, and the scan afterwards. Signed: an edited one shows as invalid."
      />
      <section className="card overflow-x-auto">
        {certs.isPending ? (
          <p className="px-4 py-6 text-sm text-muted">Loading…</p>
        ) : certs.error ? (
          <p className="px-4 py-6 text-sm text-bad">{(certs.error as Error).message}</p>
        ) : !certs.data!.length ? (
          <EmptyState title="Nothing purged" body="No tenant's data has been deleted yet." />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line">
                <th className="th">Tenant</th>
                <th className="th">Purged</th>
                <th className="th text-right">Rows</th>
                <th className="th text-right">Objects</th>
                <th className="th text-right">Left after</th>
                <th className="th text-right">Signature</th>
              </tr>
            </thead>
            <tbody>
              {certs.data!.map((c) => (
                <tr key={c.id} className="border-b border-line last:border-0">
                  <td className="td">
                    <div className="font-semibold text-ink">{c.tenant_name}</div>
                    <div className="num text-xs text-muted">{c.tenant_slug}</div>
                  </td>
                  <td className="td text-muted">
                    {dateTime(c.purged_at)} by {c.purged_by}
                  </td>
                  <td className="td num text-right">
                    {Object.values(c.body.rows_deleted).reduce((a, b) => a + b, 0).toLocaleString()}
                  </td>
                  <td className="td num text-right">{c.body.objects_deleted.toLocaleString()}</td>
                  <td className="td num text-right">
                    {c.body.scan.rows_remaining} rows, {c.body.scan.objects_remaining} objects
                  </td>
                  <td className="td text-right">
                    <span className={`chip ${c.valid ? "bg-good/10 text-good" : "bg-bad/10 text-bad"}`}>
                      {c.valid ? "valid" : "does not match"}
                    </span>
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
