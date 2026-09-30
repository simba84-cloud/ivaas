/**
 * The bay's alerts, and which of them someone has acknowledged.
 *
 * One hook for every place that shows them (the navigation badge, the command
 * view, the alerts page), reading the same cached queries, so they always agree.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useMediaServerUp } from "../api/media";
import { useScope } from "../api/scope";
import type { Acknowledgement } from "../api/types";
import { type FeedItem, type Severity, attention } from "./activity";

export interface Alert extends FeedItem {
  ack?: Acknowledgement;
}

/** How the four severities are named to people. */
export const SEVERITY_LABEL: Record<Severity, string> = {
  bad: "Critical",
  warn: "Warning",
  info: "Information",
  good: "Success",
};

export function useAlerts() {
  const { bay } = useScope();
  const bayId = bay?.id;
  const mediaUp = useMediaServerUp();
  const cameras = useQuery({ queryKey: ["cameras", bayId], queryFn: () => api.cameras(bayId!), enabled: !!bayId });
  const sessions = useQuery({ queryKey: ["sessions", bayId], queryFn: () => api.sessions(bayId), enabled: !!bayId });
  const overview = useQuery({
    queryKey: ["overview", bayId],
    queryFn: () => api.overview(14, bayId),
    enabled: !!bayId,
  });
  const acks = useQuery({ queryKey: ["alert-acks"], queryFn: api.acknowledgements });
  const nodes = useQuery({ queryKey: ["edge-nodes"], queryFn: api.edgeNodes, refetchInterval: 30_000 });
  const siteId = bay?.site_id;
  const exceptions = useQuery({ queryKey: ["exceptions", "open"], queryFn: () => api.exceptions("open") });

  const byKey = new Map((acks.data ?? []).map((a) => [a.key, a]));
  const all: Alert[] = attention({
    cameras: cameras.data,
    mediaUp,
    sessions: sessions.data,
    insights: overview.data?.insights,
    nodes: nodes.data?.filter((n) => n.site_id === siteId),
    openExceptions: exceptions.data?.length,
  }).map((a) => ({ ...a, ack: byKey.get(a.key) }));

  const active = all.filter((a) => !a.ack);
  const acknowledged = all.filter((a) => a.ack);
  const count = (s: Severity) => active.filter((a) => a.severity === s).length;

  return {
    all,
    active,
    acknowledged,
    counts: { bad: count("bad"), warn: count("warn"), info: count("info"), good: count("good") },
    /** faults and warnings nobody has acknowledged: what the navigation badge shows */
    urgent: active.filter((a) => a.severity === "bad" || a.severity === "warn").length,
    critical: count("bad") > 0,
    /** true until the state alerts are derived from has loaded */
    loading: !cameras.isSuccess || !sessions.isSuccess,
  };
}
