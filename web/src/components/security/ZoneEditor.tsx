/**
 * Drawing security zones on a camera's view.
 *
 * Points are stored as fractions of the frame, so a zone survives a change of stream
 * resolution. The editor draws over a live still from the camera; a camera that is not
 * streaming gets a plain frame, labelled as such, so nobody mistakes it for the scene.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Pencil, Plus, Trash2, Undo2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { useScope } from "../../api/scope";
import type { SecurityStatus, Zone, ZoneInput, ZoneRule } from "../../api/types";
import { transition } from "../../motion";
import { useToast } from "../toast";
import { EmptyState } from "../ui";
import { edgeHas } from "./capabilities";

const W = 1600;
const H = 900;
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const RULES: { rule: ZoneRule; label: string; needs: (s: SecurityStatus | undefined) => string | null }[] = [
  {
    rule: "intrusion",
    label: "Anyone here while armed",
    needs: (s) => (edgeHas(s, "people") === "off" ? "needs the person detector on the edge node" : null),
  },
  {
    rule: "badge",
    label: "Anyone here must have badged in",
    needs: (s) => (s && !s.badge_events_24h ? "no badge swipes received yet" : null),
  },
  {
    rule: "ppe",
    label: "People here must be in uniform / PPE",
    needs: (s) => (edgeHas(s, "uniform") !== "on" ? "needs a uniform model trained on this site" : null),
  },
  {
    rule: "face",
    label: "Only enrolled people may be here",
    needs: (s) => (!s?.face_recognition ? "face recognition is switched off" : null),
  },
  {
    rule: "fire",
    label: "Watch for fire and smoke",
    needs: (s) => (edgeHas(s, "fire") !== "on" ? "needs a fire model trained on this site" : null),
  },
];

const blank = (): ZoneInput => ({
  name: "",
  polygon: [],
  rules: ["intrusion"],
  schedule: [],
  min_dwell_s: 3,
  exclude: false,
  badge_door: null,
});

const pts = (p: [number, number][]) => p.map(([x, y]) => `${x * W},${y * H}`).join(" ");

export function ZonesTab({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { bay } = useScope();
  const cameras = useQuery({ queryKey: ["cameras", bay?.id], queryFn: () => api.cameras(bay!.id), enabled: !!bay });
  const zones = useQuery({ queryKey: ["zones", bay?.id], queryFn: () => api.zones(bay!.id), enabled: !!bay });
  const status = useQuery({ queryKey: ["security-status"], queryFn: api.securityStatus });

  const [cameraId, setCameraId] = useState<string>("");
  const camera = cameras.data?.find((c) => c.id === cameraId) ?? cameras.data?.[0];
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [draft, setDraft] = useState<ZoneInput>(blank);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const drawing = editing !== null && isAdmin;

  const snap = useQuery({
    queryKey: ["snapshot", camera?.id],
    queryFn: () => api.cameraSnapshot(camera!.id),
    enabled: !!camera,
    staleTime: 60_000,
    refetchInterval: false,
  });
  const imageUrl = useMemo(() => (snap.data ? URL.createObjectURL(snap.data) : null), [snap.data]);
  useEffect(() => () => void (imageUrl && URL.revokeObjectURL(imageUrl)), [imageUrl]);

  const onCamera = (zones.data ?? []).filter((z) => z.camera_id === camera?.id);

  const startNew = () => {
    setDraft(blank());
    setEditing("new");
  };
  const edit = (z: Zone) => {
    setDraft({
      name: z.name,
      polygon: z.polygon,
      rules: z.rules,
      schedule: z.schedule,
      min_dwell_s: z.min_dwell_s,
      exclude: z.exclude,
      badge_door: z.badge_door,
    });
    setEditing(z.id);
  };
  const stop = () => {
    setEditing(null);
    setConfirmDelete(false);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setEditing(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["zones"] });
    qc.invalidateQueries({ queryKey: ["audit"] });
  };
  const save = useMutation({
    mutationFn: () =>
      editing === "new" ? api.createZone(camera!.id, draft) : api.updateZone(editing!, draft),
    onSuccess: (z) => {
      toast({ severity: "success", title: `Zone saved · ${z.name}`, detail: z.armed ? "Armed now" : "Not armed at this hour" });
      stop();
      refresh();
    },
    onError: (e) => toast({ severity: "critical", title: "Zone not saved", detail: (e as Error).message }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteZone(id),
    onSuccess: () => {
      toast({ severity: "success", title: "Zone deleted" });
      stop();
      refresh();
    },
  });

  if (cameras.isSuccess && !cameras.data.length) {
    return (
      <div className="card">
        <EmptyState title="No cameras registered" body="Add a camera before drawing zones on it." />
      </div>
    );
  }

  const set = <K extends keyof ZoneInput>(k: K, v: ZoneInput[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const toggleRule = (r: ZoneRule) =>
    set("rules", draft.rules.includes(r) ? draft.rules.filter((x) => x !== r) : [...draft.rules, r]);
  const valid =
    draft.name.trim() &&
    draft.polygon.length >= 3 &&
    (draft.exclude || draft.rules.length > 0) &&
    (!draft.rules.includes("badge") || (draft.badge_door ?? "").trim());

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <section className="min-w-0">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <select
            id="zone-camera"
            aria-label="Camera"
            className="input h-8 w-auto py-0 text-sm"
            value={camera?.id ?? ""}
            onChange={(e) => {
              setCameraId(e.target.value);
              stop();
            }}
          >
            {(cameras.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          {isAdmin && !editing && (
            <button className="btn-primary btn-sm" onClick={startNew} disabled={!camera}>
              <Plus size={14} /> New zone
            </button>
          )}
          {drawing && (
            <span className="text-xs text-muted">
              Click on the view to add points ({draft.polygon.length}); Esc stops.
            </span>
          )}
        </div>

        <div className="video-well aspect-video w-full rounded-xl ring-1 ring-line">
          {imageUrl ? (
            <img src={imageUrl} alt={`Still from ${camera?.name}`} className="absolute inset-0 h-full w-full object-cover" />
          ) : (
            <div className="absolute inset-0 grid place-items-center text-center text-xs text-white/55">
              {snap.isPending ? "Fetching a still from the camera…" : "Not streaming: drawing on a blank frame"}
            </div>
          )}
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className={`absolute inset-0 h-full w-full ${drawing ? "cursor-crosshair" : ""}`}
            aria-label={`Zones on ${camera?.name ?? "the camera"}`}
            onClick={(e) => {
              if (!drawing) return;
              const r = e.currentTarget.getBoundingClientRect();
              const x = Math.round(((e.clientX - r.left) / r.width) * 1000) / 1000;
              const y = Math.round(((e.clientY - r.top) / r.height) * 1000) / 1000;
              set("polygon", [...draft.polygon, [x, y]]);
            }}
          >
            {onCamera
              .filter((z) => z.id !== editing)
              .map((z) => (
                <g
                  key={z.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`Edit ${z.name}`}
                  className="cursor-pointer outline-none"
                  onClick={(e) => {
                    if (drawing) return;
                    e.stopPropagation();
                    edit(z);
                  }}
                  onKeyDown={(e) => e.key === "Enter" && !drawing && edit(z)}
                >
                  <polygon
                    points={pts(z.polygon)}
                    className={
                      z.exclude
                        ? "fill-white/10 stroke-white/60"
                        : z.armed
                          ? "fill-accent/20 stroke-accent"
                          : "fill-white/5 stroke-white/50"
                    }
                    strokeWidth={3}
                    strokeDasharray={z.exclude || !z.armed ? "12 8" : undefined}
                    vectorEffect="non-scaling-stroke"
                  />
                  <text
                    x={z.polygon[0][0] * W + 10}
                    y={z.polygon[0][1] * H + 34}
                    className="fill-white text-[30px] font-bold"
                    style={{ paintOrder: "stroke", stroke: "rgb(0 0 0 / .6)", strokeWidth: 6 }}
                  >
                    {z.name}
                    {z.exclude ? " (ignored)" : z.armed ? "" : " (not armed now)"}
                  </text>
                </g>
              ))}
            {editing && draft.polygon.length > 0 && (
              <g>
                <polygon
                  points={pts(draft.polygon)}
                  className="fill-warn/25 stroke-warn"
                  strokeWidth={3}
                  vectorEffect="non-scaling-stroke"
                />
                {draft.polygon.map(([x, y], i) => (
                  <circle key={i} cx={x * W} cy={y * H} r={9} className="fill-warn stroke-black/60" strokeWidth={2} />
                ))}
              </g>
            )}
          </svg>
        </div>

        {!editing && onCamera.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {onCamera.map((z) => (
              <li key={z.id}>
                <button className="chip h-7 bg-surface text-muted ring-1 ring-line hover:text-ink" onClick={() => edit(z)}>
                  <span className={`dot ${z.exclude ? "bg-faint" : z.armed ? "bg-accent" : "bg-faint"}`} />
                  {z.name}
                  {isAdmin && <Pencil size={11} />}
                </button>
              </li>
            ))}
          </ul>
        )}
        {!editing && !onCamera.length && (
          <p className="mt-3 text-sm text-muted">
            No zones on this camera. {isAdmin ? "Draw one to start watching it." : "An admin draws them."}
          </p>
        )}
      </section>

      {editing && (
        <motion.aside initial={{ x: 24 }} animate={{ x: 0 }} transition={transition.spring} className="card min-w-0 self-start">
          <div className="panel-head">
            <h2 className="panel-title">{editing === "new" ? "New zone" : draft.name || "Zone"}</h2>
            <button className="rounded-md p-1 text-faint hover:bg-ground hover:text-ink" aria-label="Close" onClick={stop}>
              <X size={15} />
            </button>
          </div>
          <form
            className="space-y-4 p-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (valid && isAdmin) save.mutate();
            }}
          >
            <fieldset disabled={!isAdmin} className="space-y-4">
              <div>
                <label className="label" htmlFor="zone-name">
                  Name
                </label>
                <input
                  id="zone-name"
                  className="input"
                  value={draft.name}
                  maxLength={120}
                  onChange={(e) => set("name", e.target.value)}
                  placeholder="e.g. Loading apron"
                />
              </div>

              <div className="flex items-center justify-between gap-2 text-xs text-muted">
                <span className="num">{draft.polygon.length} points</span>
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  disabled={!draft.polygon.length}
                  onClick={() => set("polygon", draft.polygon.slice(0, -1))}
                >
                  <Undo2 size={12} /> Undo point
                </button>
              </div>

              <label className="flex items-start gap-2 text-sm">
                <input
                  id="zone-exclude"
                  type="checkbox"
                  className="mt-0.5"
                  checked={draft.exclude}
                  onChange={(e) => setDraft((d) => ({ ...d, exclude: e.target.checked, rules: e.target.checked ? [] : d.rules }))}
                />
                <span>
                  <span className="font-semibold text-ink">Ignore this area</span>
                  <span className="block text-xs text-muted">For the ovens and anything else that looks like fire, or where people are expected.</span>
                </span>
              </label>

              {!draft.exclude && (
                <div>
                  <span className="label">Raise an incident when</span>
                  <div className="space-y-1.5">
                    {RULES.map(({ rule, label, needs }) => {
                      const missing = needs(status.data);
                      return (
                        <label key={rule} className="flex items-start gap-2 text-sm">
                          <input
                            id={`rule-${rule}`}
                            type="checkbox"
                            className="mt-0.5"
                            checked={draft.rules.includes(rule)}
                            onChange={() => toggleRule(rule)}
                          />
                          <span>
                            <span className="text-ink">{label}</span>
                            {missing && <span className="block text-xs text-warn">Inactive: {missing}</span>}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}

              {draft.rules.includes("badge") && (
                <div>
                  <label className="label" htmlFor="zone-door">
                    Door whose readers admit people here
                  </label>
                  <input
                    id="zone-door"
                    className="input"
                    value={draft.badge_door ?? ""}
                    onChange={(e) => set("badge_door", e.target.value)}
                    placeholder="As the access-control system names it"
                  />
                </div>
              )}

              {!draft.exclude && (
                <>
                  <div>
                    <label className="label" htmlFor="zone-dwell">
                      Seconds before it counts
                    </label>
                    <input
                      id="zone-dwell"
                      type="number"
                      min={0}
                      max={600}
                      className="input num w-28"
                      value={draft.min_dwell_s}
                      onChange={(e) => set("min_dwell_s", Number(e.target.value))}
                    />
                    <p className="mt-1 text-xs text-muted">Someone walking straight through is not an intrusion.</p>
                  </div>

                  <div>
                    <span className="label">Armed</span>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        id="zone-always"
                        type="checkbox"
                        checked={draft.schedule.length === 0}
                        onChange={(e) =>
                          set("schedule", e.target.checked ? [] : [{ days: [0, 1, 2, 3, 4], start: "18:00", end: "06:00" }])
                        }
                      />
                      Always
                    </label>
                    {draft.schedule.map((w, i) => (
                      <div key={i} className="mt-2 space-y-1.5 rounded-lg border border-line p-2">
                        <div className="flex flex-wrap gap-1">
                          {DAYS.map((d, di) => {
                            const on = w.days.includes(di);
                            return (
                              <button
                                type="button"
                                key={d}
                                aria-pressed={on}
                                className={`chip h-6 px-2 text-[11px] ${on ? "bg-brand text-white" : "bg-ground text-muted"}`}
                                onClick={() => {
                                  const days = on ? w.days.filter((x) => x !== di) : [...w.days, di].sort();
                                  set("schedule", draft.schedule.map((x, j) => (j === i ? { ...x, days } : x)));
                                }}
                              >
                                {d}
                              </button>
                            );
                          })}
                        </div>
                        <div className="flex items-center gap-1.5 text-xs text-muted">
                          <input
                            aria-label="From"
                            type="time"
                            className="input h-7 w-28 text-xs"
                            value={w.start}
                            onChange={(e) => set("schedule", draft.schedule.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)))}
                          />
                          to
                          <input
                            aria-label="Until"
                            type="time"
                            className="input h-7 w-28 text-xs"
                            value={w.end}
                            onChange={(e) => set("schedule", draft.schedule.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)))}
                          />
                          {w.end < w.start && <span>(overnight)</span>}
                        </div>
                      </div>
                    ))}
                    {draft.schedule.length > 0 && (
                      <button
                        type="button"
                        className="mt-2 text-xs font-semibold text-brand hover:underline"
                        onClick={() => set("schedule", [...draft.schedule, { days: [5, 6], start: "00:00", end: "23:59" }])}
                      >
                        Add another window
                      </button>
                    )}
                  </div>
                </>
              )}
            </fieldset>

            {isAdmin && (
              <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                <button className="btn-primary btn-sm" disabled={!valid || save.isPending}>
                  {save.isPending ? "Saving…" : "Save zone"}
                </button>
                {editing !== "new" &&
                  (confirmDelete ? (
                    <span className="inline-flex items-center gap-2 text-xs">
                      <button
                        type="button"
                        className="font-semibold text-bad hover:underline"
                        onClick={() => remove.mutate(editing)}
                        disabled={remove.isPending}
                      >
                        Delete this zone
                      </button>
                      <button type="button" className="text-muted" onClick={() => setConfirmDelete(false)}>
                        Keep
                      </button>
                    </span>
                  ) : (
                    <button type="button" className="btn-ghost btn-sm text-bad" onClick={() => setConfirmDelete(true)}>
                      <Trash2 size={13} /> Delete
                    </button>
                  ))}
                {!valid && (
                  <span className="text-xs text-faint">
                    {draft.polygon.length < 3 ? "Add at least three points" : "Name it and choose what to watch for"}
                  </span>
                )}
              </div>
            )}
          </form>
        </motion.aside>
      )}
    </div>
  );
}
