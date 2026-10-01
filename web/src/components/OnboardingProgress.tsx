import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle, Clock } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { EnrollmentToken, Onboarding, OnboardingStep } from "../api/types";
import { OneTimeToken } from "../pages/EdgeNodes";
import { dateTime } from "./ui";

const LABELS: Record<OnboardingStep["name"], string> = {
  tenant_created: "Tenant created",
  owner_signed_in: "Owner has signed in",
  plan_set: "Plan set",
  site_and_bay: "Site and first bay",
  enrollment_token: "Enrollment token made",
  node_enrolled: "Edge node enrolled",
  node_reporting: "Node reporting",
};

const minutes = (s: number) => `${Math.round(s / 60)} min`;

/**
 * Creation to first enrolled node, against T8.1's half hour. Until a node enrols there
 * is no such time to show: it says how long the install has been going instead.
 */
function TimeToFirstNode({ o }: { o: Onboarding }) {
  if (o.seconds_to_first_node === null) {
    const since = o.tenant.created_at ? (Date.now() - Date.parse(o.tenant.created_at)) / 1000 : null;
    return (
      <p className="flex items-center gap-1.5 text-sm text-muted">
        <Clock size={14} /> No node enrolled yet
        {since !== null && since >= 0 && <>; created {minutes(since)} ago</>}
      </p>
    );
  }
  return (
    <p className={`flex items-center gap-1.5 text-sm font-semibold ${o.within_target ? "text-good" : "text-warn"}`}>
      <Clock size={14} /> First node enrolled {minutes(o.seconds_to_first_node)} after creation,{" "}
      {o.within_target ? "inside" : "over"} the {minutes(o.target_seconds)} target
    </p>
  );
}

function PlanStep({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const book = useQuery({ queryKey: ["price-book"], queryFn: api.priceBook });
  const [plan, setPlan] = useState("");
  const chosen = plan || book.data?.plans[0]?.id || "";
  const save = useMutation({
    mutationFn: () => api.setTenantPlan(tenantId, chosen, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["console", tenantId] }),
  });
  return (
    <form
      className="mt-2 flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div>
        <label htmlFor="onboard-plan" className="label">
          Plan
        </label>
        <select id="onboard-plan" className="input h-9 w-48" value={chosen} onChange={(e) => setPlan(e.target.value)}>
          {book.data?.plans.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <button className="btn-accent" disabled={!chosen || save.isPending}>
        Set plan
      </button>
      {save.error && (
        <span role="alert" className="w-full text-xs text-bad">
          {(save.error as Error).message}
        </span>
      )}
    </form>
  );
}

function SiteStep({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const [site, setSite] = useState("");
  const [timezone, setTimezone] = useState("Africa/Harare");
  const [bay, setBay] = useState("Dock 1");
  const save = useMutation({
    mutationFn: () => api.onboardingSite(tenantId, { site_name: site, timezone, bay_name: bay }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["console", tenantId] }),
  });
  return (
    <form
      className="mt-2 flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div>
        <label htmlFor="onboard-site" className="label">
          Site
        </label>
        <input id="onboard-site" className="input h-9 w-48" value={site} onChange={(e) => setSite(e.target.value)} required />
      </div>
      <div>
        <label htmlFor="onboard-tz" className="label">
          Time zone
        </label>
        <input id="onboard-tz" className="input h-9 w-40" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
      </div>
      <div>
        <label htmlFor="onboard-bay" className="label">
          First bay
        </label>
        <input id="onboard-bay" className="input h-9 w-32" value={bay} onChange={(e) => setBay(e.target.value)} required />
      </div>
      <button className="btn-accent" disabled={save.isPending}>
        Create site
      </button>
      {save.error && (
        <span role="alert" className="w-full text-xs text-bad">
          {(save.error as Error).message}
        </span>
      )}
    </form>
  );
}

function TokenStep({ tenantId, o, onToken }: { tenantId: string; o: Onboarding; onToken: (t: EnrollmentToken) => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("Edge 1");
  const [bay, setBay] = useState(o.bays[0]?.id ?? "");
  const site = o.bays.find((b) => b.id === bay)?.site_id ?? o.sites[0]?.id;
  const save = useMutation({
    mutationFn: () => api.onboardingToken(tenantId, { site_id: site!, bay_id: bay || null, name, ttl_hours: 24 }),
    onSuccess: (t) => {
      onToken(t);
      qc.invalidateQueries({ queryKey: ["console", tenantId] });
    },
  });
  return (
    <form
      className="mt-2 flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div>
        <label htmlFor="onboard-node" className="label">
          Node name
        </label>
        <input id="onboard-node" className="input h-9 w-40" value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <div>
        <label htmlFor="onboard-node-bay" className="label">
          Bay
        </label>
        <select id="onboard-node-bay" className="input h-9 w-48" value={bay} onChange={(e) => setBay(e.target.value)}>
          {o.bays.map((b) => (
            <option key={b.id} value={b.id}>
              {o.sites.find((s) => s.id === b.site_id)?.name ?? "Site"} / {b.name}
            </option>
          ))}
        </select>
      </div>
      <button className="btn-accent" disabled={!site || save.isPending}>
        Make enrollment token
      </button>
      <span className="text-xs text-muted">Works once, for 24 hours.</span>
      {save.error && (
        <span role="alert" className="w-full text-xs text-bad">
          {(save.error as Error).message}
        </span>
      )}
    </form>
  );
}

const HEALTH: Record<string, string> = {
  online: "bg-good/10 text-good",
  stale: "bg-warn/10 text-warn",
  offline: "bg-bad/10 text-bad",
  revoked: "bg-ground text-muted",
  never_seen: "bg-warn/10 text-warn",
};

/**
 * How far a tenant's install has got, from creation to a node that reports in, with
 * the next step's form beside it. Read from what exists in the tenant, every few
 * seconds until it is all done, so the installer sees the node arrive.
 */
export function OnboardingProgress({ tenantId }: { tenantId: string }) {
  const [token, setToken] = useState<EnrollmentToken | null>(null);
  const q = useQuery({
    queryKey: ["console", tenantId, "onboarding"],
    queryFn: () => api.onboarding(tenantId),
    refetchInterval: (query) => (query.state.data?.steps.every((s) => s.done) ? false : 5000),
  });
  if (q.isPending) return <div className="card px-4 py-6 text-sm text-muted">Loading…</div>;
  if (q.error) return <div className="card px-4 py-6 text-sm text-bad">{(q.error as Error).message}</div>;
  const o = q.data!;
  const done = (name: OnboardingStep["name"]) => o.steps.find((s) => s.name === name)?.done ?? false;
  return (
    <section aria-label="Onboarding" className="card p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-bold text-ink">Onboarding</h2>
        <TimeToFirstNode o={o} />
      </div>
      {token && <OneTimeToken token={token} onClose={() => setToken(null)} />}
      <ol className="space-y-3">
        {o.steps.map((s) => (
          <li key={s.name} className="flex gap-2.5">
            {s.done ? (
              <CheckCircle2 size={17} className="mt-0.5 flex-none text-good" aria-label="done" />
            ) : (
              <Circle size={17} className="mt-0.5 flex-none text-faint" aria-label="not yet" />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className={s.done ? "font-semibold text-ink" : "text-muted"}>{LABELS[s.name]}</span>
                {s.detail && <span className="text-xs text-muted">{s.detail}</span>}
                {s.at && <span className="text-xs text-faint">{dateTime(s.at)}</span>}
              </div>
              {s.name === "plan_set" && !s.done && <PlanStep tenantId={tenantId} />}
              {s.name === "site_and_bay" && !s.done && <SiteStep tenantId={tenantId} />}
              {s.name === "enrollment_token" && done("site_and_bay") && (
                <TokenStep tenantId={tenantId} o={o} onToken={setToken} />
              )}
            </div>
          </li>
        ))}
      </ol>
      {o.nodes.length > 0 && (
        <ul className="mt-4 divide-y divide-line border-t border-line text-sm">
          {o.nodes.map((n) => (
            <li key={n.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span className="font-semibold text-ink">{n.name}</span>
              <span className="text-xs text-muted">
                enrolled {dateTime(n.enrolled_at)} · {n.last_seen_at ? `last heard ${dateTime(n.last_seen_at)}` : "never heard from"}
                {n.version && ` · ${n.version}`}
              </span>
              <span className={`chip ${HEALTH[n.health] ?? "bg-ground text-muted"}`}>{n.health.replace("_", " ")}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-muted">
        You set up the first site and enrol its nodes. Anything after that, and everything the tenant counts, is its own
        admins' to see and change.
      </p>
    </section>
  );
}
