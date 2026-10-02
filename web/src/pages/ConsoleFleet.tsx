import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { FleetNode, TenantFleet } from "../api/types";
import { EmptyState, PageHeader, dateTime } from "../components/ui";

const TONE: Record<FleetNode["health"], string> = {
  online: "bg-good/10 text-good",
  stale: "bg-warn/10 text-warn",
  offline: "bg-bad/10 text-bad",
  never_seen: "bg-warn/10 text-warn",
  revoked: "bg-ground text-muted",
};

function cameras(n: FleetNode): string {
  if (n.cameras_reported === null) return "cameras not reported";
  if (n.cameras_reported === 0) return "no cameras";
  return `${n.cameras_connected} of ${n.cameras_reported} cameras streaming`;
}

function Tenant({ f }: { f: TenantFleet }) {
  // revoked nodes are retired: counted, not listed
  const live = f.nodes.filter((n) => n.health !== "revoked");
  const retired = f.nodes.length - live.length;
  return (
    <section aria-label={f.tenant.name} className="card p-4">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <Link to={`/console/tenants/${f.tenant.id}`} className="text-sm font-bold text-ink hover:underline">
          {f.tenant.name}
        </Link>
        {live.length === 0 ? (
          <span className="text-xs text-muted">Nothing installed</span>
        ) : f.needs_attention ? (
          <span className="chip bg-warn/10 text-warn">needs attention</span>
        ) : (
          <span className="chip bg-good/10 text-good">all online</span>
        )}
      </div>
      {live.length > 0 && (
        <ul className="divide-y divide-line text-sm">
          {live.map((n) => (
            <li key={n.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
              <span className="font-semibold text-ink">{n.name}</span>
              <span className="text-xs text-muted">
                {cameras(n)}
                {n.spool_pending ? ` · ${n.spool_pending} queued` : ""}
                {n.version && ` · ${n.version}`} ·{" "}
                {n.last_seen_at ? `last heard ${dateTime(n.last_seen_at)}` : "never heard from"}
              </span>
              <span className={`chip ${TONE[n.health]}`}>{n.health.replace("_", " ")}</span>
            </li>
          ))}
        </ul>
      )}
      {retired > 0 && (
        <p className="mt-1 text-xs text-faint">
          {retired} revoked node{retired === 1 ? "" : "s"} not shown
        </p>
      )}
    </section>
  );
}

/**
 * Every edge node of every tenant you look after: alive or not, which cameras stream,
 * what is queued. Tenants needing attention come first.
 */
export default function ConsoleFleet() {
  const fleet = useQuery({ queryKey: ["console", "fleet"], queryFn: api.fleetView, refetchInterval: 30_000 });
  const sorted = [...(fleet.data ?? [])].sort(
    (a, b) =>
      Number(b.needs_attention) - Number(a.needs_attention) ||
      b.nodes.filter((n) => n.health !== "revoked").length - a.nodes.filter((n) => n.health !== "revoked").length,
  );
  const nodes = sorted.flatMap((f) => f.nodes).filter((n) => n.health !== "revoked");
  const online = nodes.filter((n) => n.health === "online").length;
  return (
    <>
      <PageHeader
        title="Fleet"
        subtitle={
          fleet.data
            ? nodes.length
              ? `${online} of ${nodes.length} edge nodes online, across ${sorted.filter((f) => f.nodes.some((n) => n.health !== "revoked")).length} tenants.`
              : "No edge nodes enrolled anywhere yet."
            : "Edge nodes across tenants."
        }
      />
      {fleet.isPending ? (
        <div className="card px-4 py-6 text-sm text-muted">Loading…</div>
      ) : fleet.error ? (
        <div className="card px-4 py-6 text-sm text-bad">{(fleet.error as Error).message}</div>
      ) : !sorted.length ? (
        <div className="card">
          <EmptyState title="No tenants" body="Onboard a tenant to see its edge nodes here." />
        </div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {sorted.map((f) => (
            <Tenant key={f.tenant.id} f={f} />
          ))}
        </div>
      )}
    </>
  );
}
