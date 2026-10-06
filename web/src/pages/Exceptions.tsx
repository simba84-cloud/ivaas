import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api/client";
import type { ManifestException, ManifestImport } from "../api/types";
import { type Me, can } from "../auth/session";
import { EvidenceClips } from "../components/EvidenceClips";
import { EmptyState, dateTime } from "../components/ui";

const KIND: Record<ManifestException["kind"], { label: string; why: string }> = {
  count_mismatch: { label: "Count differs from manifest", why: "The cameras counted a different number of crates." },
  not_seen: { label: "Manifest truck never came", why: "No load at the bay matched this manifest line." },
  unexpected: { label: "Load not on any manifest", why: "A truck was loaded that no manifest line expected." },
};

function ImportManifest() {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<ManifestImport | null>(null);
  const upload = useMutation({
    mutationFn: (file: File) => api.importManifest(file),
    onSuccess: (r) => {
      setResult(r);
      qc.invalidateQueries({ queryKey: ["exceptions"] });
      qc.invalidateQueries({ queryKey: ["balances"] });
    },
  });
  return (
    <div className="text-right">
      <input
        ref={input}
        type="file"
        accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        aria-label="Manifest, CSV or Excel"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload.mutate(f);
          e.target.value = "";
        }}
      />
      <button className="btn-accent" onClick={() => input.current?.click()} disabled={upload.isPending}>
        <Upload size={15} /> Import manifest
      </button>
      {result && (
        <div className="mt-1 text-xs text-muted" role="status">
          {result.added} added, {result.updated} updated, {result.exceptions_raised} exception(s) raised
          {result.errors.length > 0 && <div className="text-bad">{result.errors.join("; ")}</div>}
        </div>
      )}
      {upload.error && (
        <div role="alert" className="mt-1 text-xs text-bad">
          {(upload.error as Error).message}
        </div>
      )}
    </div>
  );
}

function Resolve({ item }: { item: ManifestException }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const resolve = useMutation({
    mutationFn: () => api.resolveException(item.id, note.trim()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exceptions"] }),
  });
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        resolve.mutate();
      }}
    >
      <input
        aria-label="What was found"
        className="input h-8 w-80 text-xs"
        placeholder="What was found (required)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <button className="btn-ghost btn-sm" disabled={resolve.isPending || !note.trim()}>
        Resolve
      </button>
      {resolve.error && (
        <span role="alert" className="text-xs text-bad">
          {(resolve.error as Error).message}
        </span>
      )}
    </form>
  );
}

function Item({ item, canResolve }: { item: ManifestException; canResolve: boolean }) {
  const [open, setOpen] = useState(false);
  const k = KIND[item.kind];
  const diff = item.difference;
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="rounded-full bg-warn/10 px-2 py-0.5 text-xs font-semibold text-warn" title={k.why}>
          {k.label}
        </span>
        <span className="num font-semibold text-ink">{item.plate ?? "no plate"}</span>
        {item.route && <span className="text-sm text-muted">{item.route}</span>}
        <span className="num text-sm text-muted">{item.day}</span>
        <span className="num text-sm text-ink">
          {item.expected != null && `manifest ${item.expected.toLocaleString()}`}
          {item.expected != null && item.counted != null && " · "}
          {item.counted != null && `counted ${item.counted.toLocaleString()}`}
          {diff != null && (
            <span className={`ml-1 font-semibold ${diff < 0 ? "text-bad" : "text-warn"}`}>
              ({diff > 0 ? "+" : ""}
              {diff.toLocaleString()})
            </span>
          )}
        </span>
        {item.session_id && (
          <button className="btn-ghost btn-sm ml-auto" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? "Hide evidence" : "Evidence"}
          </button>
        )}
      </div>
      {item.status === "resolved" && (
        <div className="mt-1 text-xs text-muted">
          Resolved by {item.resolved_by}
          {item.resolved_at ? ` on ${dateTime(item.resolved_at)}` : ""}: {item.resolution_note}
        </div>
      )}
      {open && item.session_id && (
        <div className="mt-2">
          <EvidenceClips sessionId={item.session_id} />
        </div>
      )}
      {item.status === "open" && canResolve && (
        <div className="mt-2">
          <Resolve item={item} />
        </div>
      )}
    </li>
  );
}

/**
 * Where the manifest and the cameras disagree, a manifest truck never came, or a
 * truck came that no manifest expected. Each is looked at with the load's video,
 * and closed with what was found. Resolving never changes a count.
 */
export default function Exceptions({ me }: { me: Me | undefined }) {
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const list = useQuery({ queryKey: ["exceptions", status], queryFn: () => api.exceptions(status) });
  const rows = list.data ?? [];
  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Exceptions</h1>
          <p className="mt-0.5 text-sm text-muted">
            Where dispatch manifests and the counted loads disagree. Look at the load's video,
            then record what was found.
          </p>
        </div>
        {can(me, "groundtruth.enter") && <ImportManifest />}
      </div>
      <div className="mb-3 flex gap-2">
        {(["open", "resolved"] as const).map((s) => (
          <button
            key={s}
            className={`btn-ghost btn-sm capitalize ${status === s ? "ring-1 ring-brand" : ""}`}
            aria-pressed={status === s}
            onClick={() => setStatus(s)}
          >
            {s}
          </button>
        ))}
      </div>
      <div className="card overflow-hidden">
        {list.isPending ? (
          <div className="px-4 py-6 text-sm text-muted">Loading…</div>
        ) : rows.length ? (
          <ul className="divide-y divide-line">
            {rows.map((e) => (
              <Item key={e.id} item={e} canResolve={can(me, "reconciliation.resolve")} />
            ))}
          </ul>
        ) : (
          <EmptyState
            title={status === "open" ? "No open exceptions" : "Nothing resolved yet"}
            body={
              status === "open"
                ? "Exceptions appear once a dispatch manifest is imported and a load disagrees with it. Without manifests there is nothing to compare against."
                : "Resolved exceptions and what was found are kept here."
            }
          />
        )}
      </div>
    </>
  );
}
