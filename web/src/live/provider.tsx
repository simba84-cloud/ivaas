/**
 * What has happened at this bay since the portal was opened, shared by every page.
 *
 * Kept above the routes so navigating away and back does not lose it: the command
 * view, the alerts page and the toasts all read the same feed. Notable events
 * (a load closing, a count being verified, footage finishing) also raise a toast,
 * so they are seen from whichever page is open.
 */
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, createContext, useContext, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { onLive } from "../api/live";
import { useScope } from "../api/scope";
import { type ToastSeverity, useToast } from "../components/toast";
import { type FeedItem, type Known, type Mark, type Severity, interpret } from "./activity";

interface LiveFeed {
  items: FeedItem[];
  marks: Mark[];
}

const LiveFeedContext = createContext<LiveFeed>({ items: [], marks: [] });

const TOAST: Record<Severity, ToastSeverity> = {
  bad: "critical",
  warn: "warning",
  info: "info",
  good: "success",
};

/** Worth interrupting for: outcomes, not every crate that crosses the line. */
const NOTABLE = /-(closed|reconciled|approved)-|^live-job-|^live-incident-/;

export function LiveActivityProvider({ children }: { children: ReactNode }) {
  const { bay } = useScope();
  const bayId = bay?.id;
  const toast = useToast();
  const navigate = useNavigate();
  const sessions = useQuery({
    queryKey: ["sessions", bayId],
    queryFn: () => api.sessions(bayId),
    enabled: !!bayId,
  });
  const known = useRef(new Map<string, Known>());
  const [feed, setFeed] = useState<LiveFeed>({ items: [], marks: [] });

  useEffect(() => {
    for (const s of sessions.data ?? []) {
      if (!known.current.has(s.id)) known.current.set(s.id, { plate: s.plate, ai_count: s.ai_count });
    }
  }, [sessions.data]);

  useEffect(() => {
    setFeed({ items: [], marks: [] }); // a different bay starts a different story
    return onLive((m) => {
      const r = interpret(m, known.current, bayId);
      if (!r.items.length && !r.marks.length) return;
      setFeed((p) => ({
        items: [...r.items, ...p.items].slice(0, 80),
        marks: [...p.marks, ...r.marks].slice(-400),
      }));
      for (const item of r.items.filter((i) => NOTABLE.test(i.key))) {
        const job = item.key.startsWith("live-job-");
        const incident = item.key.startsWith("live-incident-");
        const [label, to] = incident
          ? ["Open security", "/security"]
          : job
            ? ["Open analysis", "/analysis"]
            : ["Open reconciliation", "/sessions"];
        toast({
          severity: TOAST[item.severity],
          title: item.title,
          detail: item.detail,
          action: { label, onClick: () => navigate(to) },
        });
      }
    });
  }, [bayId, toast, navigate]);

  return <LiveFeedContext.Provider value={feed}>{children}</LiveFeedContext.Provider>;
}

export const useLiveFeed = () => useContext(LiveFeedContext);
