/**
 * What the security features can actually do right now. A feature with nothing behind
 * it says so, rather than implying the site is watched when it is not.
 */
import { useQuery } from "@tanstack/react-query";
import { Flame, ScanFace, Shirt, UserRoundSearch, WalletCards } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "../../api/client";
import type { SecurityStatus } from "../../api/types";
import { time } from "../ui";

const STALE_MS = 5 * 60_000;

type State = "on" | "off" | "unknown";

function Capability({
  icon: Icon,
  name,
  state,
  children,
}: {
  icon: typeof Flame;
  name: string;
  state: State;
  children: ReactNode;
}) {
  const tone = { on: "text-good", off: "text-faint", unknown: "text-warn" }[state];
  const dot = { on: "bg-good", off: "bg-faint", unknown: "bg-warn" }[state];
  return (
    <div className="flex min-w-0 items-start gap-2.5 px-4 py-3">
      <Icon size={16} className={`mt-0.5 flex-none ${tone}`} />
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 text-[13px] font-bold text-ink">
          <span className={`dot ${dot}`} />
          {name}
        </div>
        <div className="text-xs text-muted">{children}</div>
      </div>
    </div>
  );
}

export function edgeHas(status: SecurityStatus | undefined, detector: string): State {
  const edge = status?.edge;
  if (!edge) return "unknown";
  if (Date.now() - Date.parse(edge.reported_at) > STALE_MS) return "unknown";
  return edge.detectors.includes(detector) ? "on" : "off";
}

export function Capabilities() {
  const status = useQuery({ queryKey: ["security-status"], queryFn: api.securityStatus });
  const s = status.data;
  const edge = s?.edge;
  const edgeLine = !edge
    ? "The edge node has not checked in"
    : Date.now() - Date.parse(edge.reported_at) > STALE_MS
      ? `Edge node last checked in at ${time(edge.reported_at)}`
      : null;
  const model = (d: string, on: string, off: string) =>
    edgeLine ?? (edgeHas(s, d) === "on" ? on : off);

  const faces: State = !s?.face_recognition ? "off" : edgeHas(s, "faces") === "on" ? "on" : "unknown";

  return (
    <section
      aria-label="What is being watched"
      className="card grid divide-y divide-line sm:grid-cols-2 sm:divide-y-0 xl:grid-cols-5 xl:divide-x"
    >
      <Capability icon={UserRoundSearch} name="People" state={edgeHas(s, "people")}>
        {model("people", "Detecting people for intrusion and badge zones", "No person detector installed")}
      </Capability>
      <Capability icon={Flame} name="Fire and smoke" state={edgeHas(s, "fire")}>
        {model(
          "fire",
          "Running. Supplements the fire alarm; never replaces it",
          "No fire model installed: needs training on this site",
        )}
      </Capability>
      <Capability icon={Shirt} name="Uniform / PPE" state={edgeHas(s, "uniform")}>
        {model("uniform", "Checking people in uniform zones", "No uniform model: needs this site's staff")}
      </Capability>
      <Capability icon={ScanFace} name="Face recognition" state={faces}>
        {!s
          ? "Checking…"
          : !s.face_recognition
            ? "Off. An admin records the legal basis under Settings to switch it on"
            : !s.face_models_installed
              ? "On, but the face models are not installed on the server"
              : `On · ${s.enrolled_people} enrolled${edgeHas(s, "faces") === "on" ? "" : " · edge node not matching yet"}`}
      </Capability>
      <Capability icon={WalletCards} name="Badge feed" state={s?.badge_events_24h ? "on" : s ? "off" : "unknown"}>
        {!s
          ? "Checking…"
          : s.badge_events_24h
            ? `${s.badge_events_24h} swipes in 24 h · last ${time(s.last_badge_at)}`
            : "No swipes received: connect the access-control system"}
      </Capability>
    </section>
  );
}
