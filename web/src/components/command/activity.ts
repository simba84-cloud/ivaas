/**
 * What the command view says is happening, derived only from what the platform
 * reported. Two kinds of item:
 *
 * - attention: conditions true right now (a camera without signal, a disputed load),
 *   recomputed from current state, so they clear themselves when fixed;
 * - activity: things that happened, from session records and from live events
 *   received while the page is open.
 *
 * Nothing here estimates or fills in a figure the platform did not send.
 */
import type { LiveMessage } from "../../api/live";
import type { Camera, Insight, Session } from "../../api/types";

/** The accuracy target the dashboard reports against. */
export const ACCURACY_TARGET = 0.95;

export type Severity = "bad" | "warn" | "info" | "good";

export interface FeedItem {
  key: string;
  severity: Severity;
  title: string;
  detail: string;
  /** epoch ms, or null for a condition that has no start time we know */
  at: number | null;
  cameraId?: string;
  /** a session event, so a live copy and the stored record are not both shown */
  dedupe?: string;
}

export type MarkKind = "opened" | "plate" | "crates" | "closed";

/** A point on the live lane of the timeline. */
export interface Mark {
  key: string;
  at: number;
  kind: MarkKind;
  label: string;
}

const RANK: Record<Severity, number> = { bad: 0, warn: 1, info: 2, good: 3 };
const fmt = (n: number) => n.toLocaleString();
const signed = (n: number) => (n > 0 ? `+${n}` : String(n));
const clock = (ms: number) =>
  new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function attention({
  cameras,
  mediaUp,
  sessions,
  insights,
}: {
  cameras: Camera[] | undefined;
  mediaUp: boolean | undefined;
  sessions: Session[] | undefined;
  insights: Insight[] | undefined;
}): FeedItem[] {
  const items: FeedItem[] = [];

  if (cameras && !cameras.length) {
    items.push({
      key: "no-cameras",
      severity: "warn",
      title: "No cameras registered",
      detail: "Nothing at this bay is being counted. Add cameras under Cameras.",
      at: null,
    });
  }
  if (mediaUp === false && cameras?.some((c) => c.status === "online")) {
    items.push({
      key: "media-down",
      severity: "bad",
      title: "Media server unreachable",
      detail: "Feeds cannot play in the portal until the media server answers.",
      at: null,
    });
  }
  for (const c of cameras ?? []) {
    if (c.status === "offline") {
      items.push({
        key: `cam-${c.id}`,
        severity: "bad",
        title: `${c.name} has no signal`,
        detail: c.last_seen_at
          ? `Last frame at ${clock(Date.parse(c.last_seen_at))}.`
          : "No frame received since it was registered.",
        at: c.last_seen_at ? Date.parse(c.last_seen_at) : null,
        cameraId: c.id,
      });
    } else if (c.status === "degraded") {
      items.push({
        key: `cam-${c.id}`,
        severity: "warn",
        title: `${c.name} is degraded`,
        detail: "Frames are arriving late or being dropped.",
        at: c.last_seen_at ? Date.parse(c.last_seen_at) : null,
        cameraId: c.id,
      });
    }
  }

  const open = sessions?.find((s) => s.status === "open");
  if (open) {
    items.push({
      key: `open-${open.id}`,
      severity: "info",
      title: "Truck at the bay",
      detail: `${open.plate ?? "Plate not read yet"} · ${plural(open.ai_count, "crate")} counted so far.`,
      at: Date.parse(open.opened_at),
    });
  }
  for (const s of (sessions ?? []).filter((x) => x.status === "disputed").slice(0, 3)) {
    items.push({
      key: `disputed-${s.id}`,
      severity: "warn",
      title: `Load ${s.plate ?? "without a plate"} disputed`,
      detail:
        s.manual_count !== null && s.variance !== null
          ? `AI ${fmt(s.ai_count)} against sheet ${fmt(s.manual_count)} (${signed(s.variance)}). Needs an approval or a recount.`
          : "Needs an approval or a recount.",
      at: s.closed_at ? Date.parse(s.closed_at) : null,
    });
  }
  const waiting = (sessions ?? []).filter((x) => x.status === "closed").length;
  if (waiting) {
    items.push({
      key: "awaiting-count",
      severity: "info",
      title: `${plural(waiting, "load")} waiting for a manual count`,
      detail: "Enter the sheet figures on Reconciliation to measure accuracy.",
      at: null,
    });
  }
  // the camera insight restates the per-camera items above
  for (const i of insights ?? []) {
    if (i.key.includes("camera")) continue;
    items.push({
      key: `insight-${i.key}`,
      severity: i.severity === "critical" ? "bad" : i.severity,
      title: i.title,
      detail: i.detail,
      at: null,
    });
  }

  return items.sort((a, b) => RANK[a.severity] - RANK[b.severity]);
}

/** Session records as a history: when each load opened and how it ended. */
export function history(sessions: Session[] | undefined, limit = 20): FeedItem[] {
  const items: FeedItem[] = [];
  for (const s of sessions ?? []) {
    const plate = s.plate ?? "No plate";
    items.push({
      key: `hist-open-${s.id}`,
      severity: "info",
      title: `${s.direction === "loading" ? "Loading" : "Offloading"} started · ${plate}`,
      detail: s.plate ? "Session opened at the bay." : "Session opened; no plate was read.",
      at: Date.parse(s.opened_at),
      dedupe: `${s.id}:opened`,
    });
    if (!s.closed_at) continue;
    const at = Date.parse(s.closed_at);
    const base = { key: `hist-close-${s.id}`, at, dedupe: `${s.id}:closed` };
    if (s.status === "reconciled" && s.manual_count !== null) {
      items.push({
        ...base,
        severity: (s.accuracy ?? 0) >= ACCURACY_TARGET ? "good" : "warn",
        title: `Verified · ${plate}`,
        detail: `AI ${fmt(s.ai_count)}, sheet ${fmt(s.manual_count)}${s.variance ? ` (${signed(s.variance)})` : ", an exact match"}.`,
      });
    } else if (s.status === "disputed") {
      items.push({
        ...base,
        severity: "warn",
        title: `Disputed · ${plate}`,
        detail: `AI ${fmt(s.ai_count)}${s.manual_count !== null ? `, sheet ${fmt(s.manual_count)}` : ""}.`,
      });
    } else if (s.status === "approved") {
      items.push({
        ...base,
        severity: "info",
        title: `Approved · ${plate}`,
        detail: `Discrepancy accepted by ${s.approved_by ?? "a supervisor"}. The counts are unchanged.`,
      });
    } else {
      items.push({
        ...base,
        severity: "good",
        title: `Load closed · ${plate}`,
        detail: `${plural(s.ai_count, "crate")} counted. Waiting for the manual count.`,
      });
    }
  }
  return items.sort((a, b) => (b.at ?? 0) - (a.at ?? 0)).slice(0, limit);
}

export interface Known {
  plate: string | null;
  ai_count: number;
}

/**
 * Turn one live event into feed items and timeline marks. `known` holds the last
 * state seen per session, which is how a count update becomes "+N crates": the
 * event carries the running total, not the increment.
 */
export function interpret(
  m: LiveMessage,
  known: Map<string, Known>,
  bayId: string | undefined,
): { items: FeedItem[]; marks: Mark[] } {
  const items: FeedItem[] = [];
  const marks: Mark[] = [];
  const d = m.data;
  const str = (k: string) => (typeof d[k] === "string" ? (d[k] as string) : null);
  const num = (k: string) => (typeof d[k] === "number" ? (d[k] as number) : null);

  if (m.subject.startsWith("ivaas.session.")) {
    const id = str("id");
    if (!id || (bayId && str("bay_id") && str("bay_id") !== bayId)) return { items, marks };
    const prev = known.get(id);
    const plate = str("plate");
    const ai = num("ai_count") ?? prev?.ai_count ?? 0;
    const name = plate ?? "the truck";
    const key = (what: string) => `live-${id}-${what}-${m.at}`;

    if (m.subject === "ivaas.session.opened") {
      items.push({
        key: key("opened"),
        severity: "info",
        title: `${str("direction") === "offloading" ? "Offloading" : "Loading"} started`,
        detail: plate ? `Truck ${plate} at the bay.` : "Waiting for the plate reader.",
        at: m.at,
        dedupe: `${id}:opened`,
      });
      marks.push({ key: key("opened"), at: m.at, kind: "opened", label: "open" });
    } else if (m.subject === "ivaas.session.updated") {
      if (plate && plate !== prev?.plate) {
        items.push({
          key: key("plate"),
          severity: "info",
          title: `Plate ${plate} read`,
          detail: "Attached to the load at the bay.",
          at: m.at,
        });
        marks.push({ key: key("plate"), at: m.at, kind: "plate", label: plate });
      }
      const delta = prev ? ai - prev.ai_count : 0;
      if (delta > 0) {
        items.push({
          key: key("crates"),
          severity: "info",
          title: `+${plural(delta, "crate")} counted`,
          detail: `${fmt(ai)} on ${name} so far.`,
          at: m.at,
        });
        marks.push({ key: key("crates"), at: m.at, kind: "crates", label: `+${delta}` });
      }
    } else if (m.subject === "ivaas.session.closed") {
      items.push({
        key: key("closed"),
        severity: "good",
        title: `Load closed · ${plate ?? "no plate"}`,
        detail: `${plural(ai, "crate")} counted. Waiting for the manual count.`,
        at: m.at,
        dedupe: `${id}:closed`,
      });
      marks.push({ key: key("closed"), at: m.at, kind: "closed", label: fmt(ai) });
    } else if (m.subject === "ivaas.session.reconciled") {
      const manual = num("manual_count");
      const variance = num("variance");
      const accuracy = num("accuracy");
      items.push({
        key: key("reconciled"),
        severity: accuracy !== null && accuracy >= ACCURACY_TARGET ? "good" : "warn",
        title: `Manual count entered · ${plate ?? "no plate"}`,
        detail:
          manual === null
            ? "Manual count recorded."
            : variance
              ? `Sheet says ${fmt(manual)}, AI counted ${fmt(ai)} (${signed(variance)}).`
              : `Sheet says ${fmt(manual)}. Matches the AI count.`,
        at: m.at,
        dedupe: `${id}:closed`,
      });
    } else if (m.subject === "ivaas.session.approved") {
      items.push({
        key: key("approved"),
        severity: "info",
        title: `Discrepancy approved · ${plate ?? "no plate"}`,
        detail: "A supervisor accepted the difference. The counts are unchanged.",
        at: m.at,
        dedupe: `${id}:closed`,
      });
    }
    known.set(id, { plate: plate ?? prev?.plate ?? null, ai_count: ai });
  } else if (m.subject === "ivaas.camera.registered") {
    items.push({
      key: `live-cam-${m.at}`,
      severity: "info",
      title: "Camera registered",
      detail: `${str("name") ?? "A camera"} was added.`,
      at: m.at,
      cameraId: str("id") ?? undefined,
    });
  } else if (m.subject === "ivaas.camera.removed") {
    items.push({ key: `live-cam-${m.at}`, severity: "info", title: "Camera removed", detail: "A camera was taken off this bay.", at: m.at });
  } else if (m.subject === "ivaas.analysis.updated") {
    const status = str("status");
    const file = str("filename") ?? "Uploaded footage";
    if (status === "done") {
      const crates = num("total_crates");
      items.push({
        key: `live-job-${str("id")}-done`,
        severity: "good",
        title: "Footage analysed",
        detail: crates === null ? `${file} is ready to review.` : `${file}: ${plural(crates, "crate")} counted.`,
        at: m.at,
      });
    } else if (status === "failed") {
      items.push({
        key: `live-job-${str("id")}-failed`,
        severity: "bad",
        title: "Footage analysis failed",
        detail: `${file}: ${str("error") ?? "the worker reported an error"}.`,
        at: m.at,
      });
    }
  }
  return { items, marks };
}

/** Live items first, then stored history the live stream has not already told. */
export function mergeActivity(live: FeedItem[], stored: FeedItem[], limit = 40): FeedItem[] {
  const told = new Set(live.map((i) => i.dedupe).filter(Boolean));
  return [...live, ...stored.filter((i) => !i.dedupe || !told.has(i.dedupe))]
    .sort((a, b) => (b.at ?? 0) - (a.at ?? 0))
    .slice(0, limit);
}
