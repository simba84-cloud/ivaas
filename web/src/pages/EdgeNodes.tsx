import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Cpu, KeyRound, Plus, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { NodeConfigEditor } from "../components/NodeConfigEditor";
import { useScope } from "../api/scope";
import type { EdgeNode, EnrollmentToken, NodeHealth } from "../api/types";
import { type Me, can } from "../auth/session";
import { EmptyState, dateTime } from "../components/ui";
import { MotionRow, SkeletonRows } from "../motion";

/** Health is what the heartbeats say. A node that never reported is not "online". */
const HEALTH: Record<NodeHealth, { label: string; tone: string; what: string }> = {
  online: { label: "Online", tone: "text-good bg-good/10", what: "Reported in the last 90 s" },
  stale: { label: "Late", tone: "text-warn bg-warn/10", what: "No report for over 90 s" },
  offline: { label: "Offline", tone: "text-bad bg-bad/10", what: "No report for over 5 min" },
  never_seen: {
    label: "Never reported",
    tone: "text-warn bg-warn/10",
    what: "Enrolled, but no heartbeat has arrived",
  },
  revoked: { label: "Revoked", tone: "text-muted bg-line/40", what: "Its credential no longer works" },
};

const since = (iso: string | null) => (iso ? dateTime(iso) : "Never");

function uptime(s: number | null) {
  if (s === null) return "—";
  const h = Math.floor(s / 3600);
  return h >= 1 ? `${h} h ${Math.floor((s % 3600) / 60)} min` : `${Math.floor(s / 60)} min`;
}

/**
 * An enrollment token, shown once. The platform stores a digest of it and cannot
 * show it again; the command beside it is what the installer runs on the node.
 */
function OneTimeToken({ token, onClose }: { token: EnrollmentToken; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const command = `docker compose run --rm pipeline python -m ivaas_pipeline enroll --api ${window.location.origin} --token ${token.token}`;
  return (
    <div className="card-lift mb-4 border-l-4 border-l-accent p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <KeyRound size={15} className="text-accent" />
            <h2 className="text-sm font-bold text-ink">Enrollment token for {token.name}</h2>
          </div>
          <p className="mt-1 text-xs text-muted">
            Run this on the edge node now. The token works once, until{" "}
            {dateTime(token.expires_at)}, and cannot be shown again: if it is lost, create
            another.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <code className="num max-w-full break-all rounded-md bg-line/40 px-2 py-1 text-xs text-ink">
              {command}
            </code>
            <button
              className="btn-ghost btn-sm"
              onClick={() => {
                navigator.clipboard?.writeText(command).then(
                  () => setCopied(true),
                  () => setCopied(false),
                );
              }}
            >
              <Copy size={13} /> {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
        <button aria-label="Dismiss" className="btn-ghost btn-sm" onClick={onClose}>
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

function NewNode({ onDone }: { onDone: (t: EnrollmentToken) => void }) {
  const { site, bay } = useScope();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api.createEnrollmentToken(site!.id, name.trim(), bay?.id),
    onSuccess: (t) => {
      setOpen(false);
      setName("");
      onDone(t);
    },
  });

  if (!open) {
    return (
      <button className="btn-accent" onClick={() => setOpen(true)} disabled={!site}>
        <Plus size={16} /> Add node
      </button>
    );
  }
  return (
    <form
      className="card flex flex-wrap items-end gap-2 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <div>
        <label htmlFor="node-name" className="label">
          Node name
        </label>
        <input
          id="node-name"
          className="input w-48"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Loading bay edge"
          required
        />
      </div>
      <div className="pb-2 text-xs text-muted">
        For {site?.name ?? "this site"}
        {bay ? ` / ${bay.name}` : ""}
      </div>
      <button className="btn-accent" disabled={create.isPending || !name.trim()}>
        Create token
      </button>
      <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {create.error && (
        <div role="alert" className="w-full text-xs text-bad">
          {(create.error as Error).message}
        </div>
      )}
    </form>
  );
}

/** "stacks v2" for a registered model, the file name for one on the node's disk. */
function modelName(m: NonNullable<EdgeNode["models"]>[string] | undefined) {
  if (!m) return null;
  return m.name ? `${m.name} ${m.version}` : (m.path ?? "").split("/").pop() || null;
}

function Row({
  node,
  index,
  canManage,
  canCalibrate,
}: {
  node: EdgeNode;
  index: number;
  canManage: boolean;
  canCalibrate: boolean;
}) {
  const qc = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => api.revokeNode(node.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["edge-nodes"] }),
  });
  const rollBack = useMutation({
    mutationFn: () => api.rollBackNode(node.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["edge-nodes"] }),
  });
  const [configuring, setConfiguring] = useState(false);
  const running = modelName(node.models?.detector);
  const h = HEALTH[node.health];
  const down = node.cameras.filter((c) => !c.connected);
  return (
    <>
    <MotionRow index={index} className="border-b border-line last:border-0 align-top">
      <td className="td">
        <div className="flex items-center gap-2 font-semibold text-ink">
          <Cpu size={14} className="text-muted" />
          {node.name}
        </div>
        <div className="text-xs text-muted">
          {node.hostname || "hostname not reported"}
          {node.version ? ` · v${node.version}` : ""}
        </div>
      </td>
      <td className="td">
        <span title={h.what} className={`rounded-full px-2 py-0.5 text-xs font-semibold ${h.tone}`}>
          {h.label}
        </span>
        <div className="mt-1 text-xs text-muted">Last report: {since(node.last_seen_at)}</div>
      </td>
      <td className="td text-sm">
        {node.cameras.length === 0 ? (
          <span className="text-muted">None reported</span>
        ) : (
          <>
            <div className={down.length ? "text-bad" : "text-ink"}>
              {node.cameras.length - down.length} of {node.cameras.length} streaming
            </div>
            {down.slice(0, 3).map((c) => (
              <div key={c.api_camera_id} className="text-xs text-bad">
                {c.name ?? "Unknown camera"}: no stream
              </div>
            ))}
          </>
        )}
      </td>
      <td className="td text-sm">
        <div className={node.spool_pending ? "text-warn" : "text-ink"}>
          {node.spool_pending === null ? "—" : `${node.spool_pending} queued`}
        </div>
        <div className="text-xs text-muted">Up {uptime(node.uptime_s)}</div>
      </td>
      <td className="td text-xs">
        {Object.keys(node.config).length === 0 ? (
          <span className="text-warn">Not configured</span>
        ) : node.config_drift === null ? (
          <span className="text-muted">Not yet reported</span>
        ) : node.config_drift ? (
          <span className="text-warn">Updating to {node.config_version}</span>
        ) : (
          <span className="text-good">Current ({node.config_version})</span>
        )}
        {running && <div className="mt-1 text-muted">Model: {running}</div>}
        {node.model_error && (
          <div role="alert" className="mt-1 max-w-xs text-bad">
            Refused a new model: {node.model_error}
          </div>
        )}
      </td>
      <td className="td text-right">
        {canCalibrate && node.status === "active" && (
          <button
            className="btn-ghost btn-sm"
            aria-expanded={configuring}
            onClick={() => setConfiguring((o) => !o)}
            title="Which cameras it counts with, where, and at what stride"
          >
            Configure
          </button>
        )}
        {canCalibrate && node.status === "active" && node.can_roll_back && (
          <button
            className="btn-ghost btn-sm"
            disabled={rollBack.isPending}
            onClick={() => rollBack.mutate()}
            title="Return to the configuration and model before the last change"
          >
            Roll back
          </button>
        )}
        {canManage && node.status === "active" && (
          <button
            className="btn-ghost btn-sm text-bad"
            disabled={revoke.isPending}
            onClick={() => revoke.mutate()}
          >
            Revoke
          </button>
        )}
      </td>
    </MotionRow>
    {configuring && (
      <tr className="border-b border-line">
        <td colSpan={6} className="p-3">
          <NodeConfigEditor node={node} onClose={() => setConfiguring(false)} />
        </td>
      </tr>
    )}
    </>
  );
}

export default function EdgeNodes({ me }: { me: Me | undefined }) {
  const [shown, setShown] = useState<EnrollmentToken | null>(null);
  const canManage = can(me, "device.register");
  // a heartbeat every 30 s; refreshing at the same pace keeps health honest
  const nodes = useQuery({ queryKey: ["edge-nodes"], queryFn: api.edgeNodes, refetchInterval: 30_000 });
  const rows = nodes.data ?? [];

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Edge nodes</h1>
          <p className="mt-0.5 text-sm text-muted">
            The machines at each site that decode the cameras and count. Health comes from
            their heartbeats.
          </p>
        </div>
        {canManage && <NewNode onDone={setShown} />}
      </div>

      {shown && <OneTimeToken token={shown} onClose={() => setShown(null)} />}

      <div className="card overflow-hidden">
        {nodes.isPending ? (
          <table className="w-full">
            <SkeletonRows rows={2} cols={6} />
          </table>
        ) : nodes.isError ? (
          <EmptyState title="Could not load edge nodes" body={(nodes.error as Error).message} />
        ) : rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Node</th>
                  <th className="th">Health</th>
                  <th className="th">Cameras</th>
                  <th className="th">Backlog</th>
                  <th className="th">Configuration</th>
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {rows.map((n, i) => (
                  <Row
                    key={n.id}
                    node={n}
                    index={i}
                    canManage={canManage}
                    canCalibrate={can(me, "device.calibrate")}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No edge nodes enrolled"
            body="Nothing at this site is being counted by an enrolled node yet. Add one to get an enrollment token."
          />
        )}
      </div>

      <p className="mt-3 flex items-start gap-2 text-xs text-muted">
        <ShieldCheck size={13} className="mt-0.5 flex-none" />
        Each node can report only for the site it was enrolled at. Tokens work once and
        expire; credentials are stored as digests and never shown again. Revoking a node
        ends its access on its next report.
      </p>
    </>
  );
}
