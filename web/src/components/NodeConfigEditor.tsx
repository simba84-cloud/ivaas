import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Crosshair, RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Camera, EdgeNode, NodeCameraConfig, NodeConfig, Point } from "../api/types";
import { roleLabel } from "./ui";

type Mode = "line" | "zone";
type Zone = [number, number, number, number];
interface RowState {
  on: boolean;
  mode: Mode;
  line?: [Point, Point];
  zone?: Zone;
  stride: number;
  /** what the stored configuration had for this camera, kept for keys the editor does not show */
  kept?: NodeCameraConfig;
}

const DEFAULT_MODEL = { path: "/models/stacks-v2.onnx", arch: "rtdetr" as const };
const DEFAULT_LAYERS = "/models/layers-v3.onnx";
const round = (n: number) => Math.round(n);
/** A choice, not a number box: a box cleared to retype snapped back to 1 and gave 13. */
const STRIDES = [1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30];

/** A camera's starting state: what the node runs now, else what its position suggests. */
function initial(cam: Camera, cfg: Partial<NodeConfig>, configured: boolean): RowState {
  const lpr = cam.role === "lpr";
  const had = (lpr ? cfg.lpr_cameras : cfg.cameras)?.find((c) => c.api_camera_id === cam.id);
  if (had) {
    return {
      on: true,
      mode: had.line ? "line" : "zone",
      line: had.line,
      zone: had.zone,
      stride: had.stride ?? 1,
      kept: had,
    };
  }
  return {
    // a node with nothing configured yet starts with every camera on
    on: !configured,
    mode: cam.role === "chokepoint" ? "line" : "zone",
    stride: lpr ? 5 : cam.role === "chokepoint" ? 1 : 2,
  };
}

/** What still stops this row from being saved, in words; null when it can be. */
function problem(cam: Camera, r: RowState): string | null {
  if (!r.on || cam.role === "lpr") return null;
  if (r.mode === "line" && !r.line) return `Draw the counting line for ${cam.name}`;
  if (r.mode === "zone" && !r.zone) return `Draw the counting zone for ${cam.name}`;
  return null;
}

function NumberBox({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number | undefined;
  onChange: (n: number) => void;
}) {
  return (
    <label className="flex items-center gap-1 text-xs text-muted">
      {label}
      <input
        type="number"
        className="input h-7 w-20 px-2 text-xs"
        value={value ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

/**
 * One frame from the camera, to draw on. Coordinates are the camera's own pixels, which
 * is what the node counts in. Two clicks make a line, or the corners of a zone; the
 * numbers below can be typed instead, which also works when there is no frame.
 */
function FrameDrawer({
  camera,
  row,
  onChange,
  onDone,
}: {
  camera: Camera;
  row: RowState;
  onChange: (r: RowState) => void;
  onDone: () => void;
}) {
  const frame = useQuery({
    queryKey: ["snapshot", camera.id],
    queryFn: () => api.cameraSnapshot(camera.id),
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
  const [url, setUrl] = useState<string | null>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [start, setStart] = useState<Point | null>(null);
  const silent = frame.isSuccess && frame.data === null; // the camera is not streaming
  useEffect(() => {
    if (!frame.data) return;
    const u = URL.createObjectURL(frame.data);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [frame.data]);

  const click = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!size) return;
    const box = e.currentTarget.getBoundingClientRect();
    const p: Point = [
      round(((e.clientX - box.left) / box.width) * size.w),
      round(((e.clientY - box.top) / box.height) * size.h),
    ];
    if (!start) return setStart(p);
    setStart(null);
    if (row.mode === "line") {
      if (p[0] !== start[0] || p[1] !== start[1]) onChange({ ...row, line: [start, p] });
    } else {
      const zone: Zone = [
        Math.min(start[0], p[0]),
        Math.min(start[1], p[1]),
        Math.max(start[0], p[0]),
        Math.max(start[1], p[1]),
      ];
      if (zone[2] > zone[0] && zone[3] > zone[1]) onChange({ ...row, zone });
    }
  };

  const line = row.line ?? [
    [0, 0],
    [0, 0],
  ];
  const zone = row.zone ?? [0, 0, 0, 0];
  const setLine = (i: 0 | 1, j: 0 | 1, n: number) => {
    const next = line.map((pt) => [...pt]) as [Point, Point];
    next[i][j] = n;
    onChange({ ...row, line: next });
  };
  const setZone = (i: number, n: number) => {
    const next = [...zone] as Zone;
    next[i] = n;
    onChange({ ...row, zone: next });
  };

  return (
    <div className="mt-2 rounded-lg border border-line bg-ground p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold text-ink">
          {row.mode === "line" ? "Counting line" : "Counting zone"} for {camera.name}
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={() => {
              setStart(null);
              frame.refetch();
            }}
          >
            <RefreshCw size={13} /> New frame
          </button>
          <button type="button" className="btn-ghost btn-sm" onClick={onDone}>
            Done
          </button>
        </div>
      </div>
      {frame.isPending ? (
        <div className="text-xs text-muted">Taking a frame from the camera…</div>
      ) : frame.isError || silent ? (
        <div role="alert" className="text-xs text-warn">
          The camera is not streaming, so there is no frame to draw on. Type the coordinates
          instead, or draw once it streams.
        </div>
      ) : (
        url && (
          // the frame is the camera's picture: a video well, single-theme on purpose
          <div className="video-well relative w-full max-w-3xl">
            <img
              src={url}
              alt={`Still frame from ${camera.name}`}
              className="block w-full"
              onLoad={(e) =>
                setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
              }
            />
            {size && (
              <svg
                viewBox={`0 0 ${size.w} ${size.h}`}
                className="absolute inset-0 h-full w-full cursor-crosshair text-accent"
                onClick={click}
                role="img"
                aria-label={`Drawing surface for ${camera.name}`}
              >
                {row.mode === "line" && row.line && (
                  <line
                    x1={row.line[0][0]}
                    y1={row.line[0][1]}
                    x2={row.line[1][0]}
                    y2={row.line[1][1]}
                    stroke="currentColor"
                    strokeWidth={Math.max(3, size.w / 300)}
                  />
                )}
                {row.mode === "zone" && row.zone && (
                  <rect
                    x={row.zone[0]}
                    y={row.zone[1]}
                    width={row.zone[2] - row.zone[0]}
                    height={row.zone[3] - row.zone[1]}
                    fill="currentColor"
                    fillOpacity={0.15}
                    stroke="currentColor"
                    strokeWidth={Math.max(3, size.w / 300)}
                  />
                )}
                {start && (
                  <circle cx={start[0]} cy={start[1]} r={Math.max(6, size.w / 150)} fill="currentColor" />
                )}
              </svg>
            )}
          </div>
        )
      )}
      <p className="mt-2 text-xs text-muted">
        {row.mode === "line"
          ? "Click two points across the path the stacks take."
          : "Click two opposite corners of where stacks are counted."}{" "}
        {size ? `The frame is ${size.w} × ${size.h} pixels.` : ""}
      </p>
      <div className="mt-2 flex flex-wrap gap-3">
        {row.mode === "line" ? (
          <>
            <NumberBox label="from x" value={row.line?.[0][0]} onChange={(n) => setLine(0, 0, n)} />
            <NumberBox label="y" value={row.line?.[0][1]} onChange={(n) => setLine(0, 1, n)} />
            <NumberBox label="to x" value={row.line?.[1][0]} onChange={(n) => setLine(1, 0, n)} />
            <NumberBox label="y" value={row.line?.[1][1]} onChange={(n) => setLine(1, 1, n)} />
          </>
        ) : (
          (["left", "top", "right", "bottom"] as const).map((label, i) => (
            <NumberBox key={label} label={label} value={row.zone?.[i]} onChange={(n) => setZone(i, n)} />
          ))
        )}
      </div>
    </div>
  );
}

/**
 * What an edge node counts, and where: the cameras of its bay, each counting at a line
 * (chokepoints) or in a zone, at a stride, and the plate camera. Saved, it reaches the
 * node within a minute; the node keeps the previous configuration for a roll back.
 */
export function NodeConfigEditor({ node, onClose }: { node: EdgeNode; onClose: () => void }) {
  const qc = useQueryClient();
  const cfg = node.config as Partial<NodeConfig>;
  const configured = Object.keys(node.config).length > 0;
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const atSite = (bays.data ?? []).filter((b) => b.site_id === node.site_id);
  const [bayId, setBayId] = useState<string | null>(node.bay_id);
  const bay = bayId ?? atSite[0]?.id ?? null;
  const cameras = useQuery({
    queryKey: ["cameras", bay],
    queryFn: () => api.cameras(bay!),
    enabled: !!bay,
  });
  const [rows, setRows] = useState<Record<string, RowState> | null>(null);
  const [drawing, setDrawing] = useState<string | null>(null);
  const [modelPath, setModelPath] = useState(cfg.model?.path ?? DEFAULT_MODEL.path);

  useEffect(() => {
    if (!cameras.data) return;
    setRows(Object.fromEntries(cameras.data.map((c) => [c.id, initial(c, cfg, configured)])));
    // a new bay starts again from what the node runs; edits are not carried across
  }, [cameras.data]);

  const cams = cameras.data ?? [];
  const set = (id: string, r: RowState) => setRows((was) => ({ ...(was ?? {}), [id]: r }));
  const problems = rows ? cams.map((c) => problem(c, rows[c.id])).filter(Boolean) : [];
  const counting = rows ? cams.filter((c) => c.role !== "lpr" && rows[c.id]?.on) : [];

  const save = useMutation({
    mutationFn: () => {
      const entry = (c: Camera): NodeCameraConfig => {
        const r = rows![c.id];
        const { line: _l, zone: _z, ...kept } = r.kept ?? { api_camera_id: c.id, stride: 1 };
        const out: NodeCameraConfig = { ...kept, api_camera_id: c.id, stride: r.stride };
        if (c.role !== "lpr") {
          if (r.mode === "line") out.line = r.line;
          else out.zone = r.zone;
        }
        return out;
      };
      const on = cams.filter((c) => rows![c.id]?.on);
      const body: NodeConfig = {
        ...(cfg as NodeConfig),
        model: cfg.model ?? { ...DEFAULT_MODEL, path: modelPath.trim() },
        cameras: on.filter((c) => c.role !== "lpr").map(entry),
        lpr_cameras: on.filter((c) => c.role === "lpr").map(entry),
      };
      if (!cfg.layers_model && !cfg.layers_model_id) body.layers_model = DEFAULT_LAYERS;
      return api.setNodeConfig(node.id, body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["edge-nodes"] });
      onClose();
    },
  });

  return (
    <div className="card-lift border-l-4 border-l-accent p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold text-ink">What {node.name} counts</h2>
          <p className="mt-0.5 text-xs text-muted">
            Chokepoints count stacks crossing a line; other positions count within a zone. Keep
            chokepoints at stride 1: reading fewer frames misses crossings. The node picks this up
            within a minute and keeps the previous configuration for a roll back.
          </p>
        </div>
        <button type="button" aria-label="Close" className="btn-ghost btn-sm" onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      {!node.bay_id && atSite.length > 1 && (
        <label className="mb-3 flex items-center gap-2 text-sm text-ink">
          Bay
          <select className="input h-8 w-56" value={bay ?? ""} onChange={(e) => setBayId(e.target.value)}>
            {atSite.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
      )}

      {cfg.model ? (
        <p className="mb-3 text-xs text-muted">
          Model: {cfg.model.path ?? `registered version ${cfg.model.version_id}`}. Change models
          with a rollout, not here.
        </p>
      ) : (
        <label className="mb-3 flex items-center gap-2 text-xs text-muted">
          Detection model on the node
          <input className="input h-8 w-72 text-xs" value={modelPath} onChange={(e) => setModelPath(e.target.value)} />
        </label>
      )}

      {cameras.isPending || !rows ? (
        <div className="text-sm text-muted">Loading the bay's cameras…</div>
      ) : cams.length === 0 ? (
        <div className="text-sm text-muted">
          This bay has no cameras. Register them on the Cameras page first.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="py-1 pr-2 font-semibold">Use</th>
                <th className="py-1 pr-2 font-semibold">Camera</th>
                <th className="py-1 pr-2 font-semibold">Counts at</th>
                <th className="py-1 pr-2 font-semibold">Stride</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {cams.map((c) => {
                const r = rows[c.id];
                if (!r) return null;
                const lpr = c.role === "lpr";
                const shape = r.mode === "line" ? r.line : r.zone;
                return (
                  <tr key={c.id} className="align-top">
                    <td className="py-2 pr-2">
                      <input
                        type="checkbox"
                        aria-label={`Use ${c.name}`}
                        checked={r.on}
                        onChange={(e) => set(c.id, { ...r, on: e.target.checked })}
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <div className="font-semibold text-ink">{c.name}</div>
                      <div className="text-xs text-muted">{roleLabel(c.role)}</div>
                    </td>
                    <td className="py-2 pr-2">
                      {lpr ? (
                        <span className="text-xs text-muted">Reads plates</span>
                      ) : (
                        <>
                          <select
                            aria-label={`How ${c.name} counts`}
                            className="input h-8 w-28 text-xs"
                            value={r.mode}
                            disabled={!r.on}
                            onChange={(e) => set(c.id, { ...r, mode: e.target.value as Mode })}
                          >
                            <option value="line">a line</option>
                            <option value="zone">a zone</option>
                          </select>
                          <div className={`num mt-1 text-xs ${shape ? "text-muted" : "text-warn"}`}>
                            {shape ? JSON.stringify(shape) : "not drawn"}
                          </div>
                        </>
                      )}
                    </td>
                    <td className="py-2 pr-2">
                      <select
                        aria-label={`Stride for ${c.name}`}
                        className="input h-8 w-20 text-xs"
                        value={r.stride}
                        disabled={!r.on}
                        onChange={(e) => set(c.id, { ...r, stride: Number(e.target.value) })}
                        title="The node reads every n-th frame"
                      >
                        {[...new Set([...STRIDES, r.stride])]
                          .sort((a, b) => a - b)
                          .map((n) => (
                            <option key={n} value={n}>
                              {n === 1 ? "every frame" : `1 in ${n}`}
                            </option>
                          ))}
                      </select>
                      {c.role === "chokepoint" && r.on && r.stride > 1 && (
                        <div className="mt-1 max-w-[12rem] text-xs text-warn">
                          Above 1, a chokepoint misses crossings.
                        </div>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      {!lpr && r.on && (
                        <button
                          type="button"
                          className="btn-ghost btn-sm"
                          aria-expanded={drawing === c.id}
                          onClick={() => setDrawing(drawing === c.id ? null : c.id)}
                        >
                          <Crosshair size={13} /> Draw
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {drawing && rows?.[drawing] && (
        <FrameDrawer
          camera={cams.find((c) => c.id === drawing)!}
          row={rows[drawing]}
          onChange={(r) => set(drawing, r)}
          onDone={() => setDrawing(null)}
        />
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-accent"
          disabled={save.isPending || !rows || problems.length > 0 || counting.length === 0}
          onClick={() => save.mutate()}
        >
          Save configuration
        </button>
        <button type="button" className="btn-ghost" onClick={onClose}>
          Cancel
        </button>
        {rows && counting.length === 0 && (
          <span className="text-xs text-warn">Choose at least one camera that counts.</span>
        )}
        {problems.length > 0 && <span className="text-xs text-warn">{problems[0]}</span>}
      </div>
      {save.error && (
        <div role="alert" className="mt-2 text-xs text-bad">
          {(save.error as Error).message}
        </div>
      )}
    </div>
  );
}
