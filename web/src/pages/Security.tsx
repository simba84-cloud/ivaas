/**
 * Site security: incidents with their evidence, the zones they are raised in, the
 * people face recognition may recognise, and the badge log.
 *
 * The strip at the top says what is actually being watched. A feature with no model,
 * no feed or no legal basis behind it says so there, and its rules show as inactive.
 */
import { useSearchParams } from "react-router-dom";
import type { Me } from "../auth/session";
import { can } from "../auth/session";
import { Capabilities } from "../components/security/capabilities";
import { IncidentsTab } from "../components/security/incidents";
import { BadgesTab, PeopleTab } from "../components/security/people";
import { ZonesTab } from "../components/security/ZoneEditor";
import { Segmented } from "../motion";

type Tab = "incidents" | "zones" | "people" | "badges";

export default function Security({ me }: { me: Me | undefined }) {
  const [params, setParams] = useSearchParams();
  const isAdmin = can(me, "device.calibrate");
  const canEnrol = can(me, "security.manage");
  const isOperator = can(me, "session.operate");
  const tabs: { value: Tab; label: string }[] = [
    { value: "incidents", label: "Incidents" },
    { value: "zones", label: "Zones" },
    ...(canEnrol ? [{ value: "people" as Tab, label: "Enrolled faces" }] : []),
    ...(isOperator ? [{ value: "badges" as Tab, label: "Badge log" }] : []),
  ];
  const wanted = params.get("tab") as Tab | null;
  const tab: Tab = tabs.some((t) => t.value === wanted) ? wanted! : "incidents";

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Security</h1>
          <p className="mt-0.5 text-sm text-muted">
            Intrusion, badge, uniform, face and fire checks in the zones drawn on each camera
          </p>
        </div>
        <Segmented<Tab>
          label="Security section"
          size="md"
          value={tab}
          onChange={(t) => setParams(t === "incidents" ? {} : { tab: t })}
          options={tabs}
        />
      </div>

      <div className="mb-4">
        <Capabilities />
      </div>

      {tab === "incidents" && <IncidentsTab canAct={isOperator} />}
      {tab === "zones" && <ZonesTab isAdmin={isAdmin} />}
      {tab === "people" && <PeopleTab />}
      {tab === "badges" && <BadgesTab />}
    </>
  );
}
