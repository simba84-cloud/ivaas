import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileVideo, Loader2, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useScope } from "../api/scope";
import type { AnalysisJob } from "../api/types";
import { type Me, hasRole } from "../auth/session";
import { EmptyState, PageHeader, dateTime } from "../components/ui";

const STATUS: Record<AnalysisJob["status"], string> = {
  queued: "bg-ground text-muted",
  running: "bg-accent-tint text-accent",
  done: "bg-good/10 text-good",
  failed: "bg-bad/10 text-bad",
};

export function fmtSeconds(s: number | null): string {
  if (s === null) return "—";
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.round(s % 60)).padStart(2, "0")}`;
}

const GB = 1024 * 1024 * 1024;

function UploadCard({ bayId, maxMb }: { bayId: string; maxMb: number }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sent, setSent] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);
  const maxBytes = maxMb * 1024 * 1024;
  const maxLabel = maxMb >= 1024 ? `${Math.round(maxMb / 1024)} GB` : `${maxMb} MB`;

  const upload = useMutation({
    mutationFn: (f: File) => api.uploadVideo(bayId, f, setSent),
    onSuccess: () => {
      setFile(null);
      setSent(0);
      qc.invalidateQueries({ queryKey: ["analyses"] });
    },
  });

  /** Check before the upload starts: finding out at the end of 5 GB is no use. */
  const pick = (f: File | undefined) => {
    if (!f) return;
    if (!f.type.startsWith("video/")) {
      setRejected(`${f.name} is not a video file.`);
      return;
    }
    if (f.size > maxBytes) {
      setRejected(
        `${f.name} is ${(f.size / GB).toFixed(1)} GB. The limit is ${maxLabel} — trim the clip or ask an administrator to raise it.`,
      );
      return;
    }
    setRejected(null);
    setFile(f);
  };

  return (
    <div className="card p-5">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          pick(e.dataTransfer.files[0]);
        }}
        onClick={() => input.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition ${
          dragging ? "border-accent bg-accent-tint" : "border-line hover:border-brand"
        }`}
      >
        <input
          ref={input}
          type="file"
          accept="video/*"
          className="hidden"
          onChange={(e) => pick(e.target.files?.[0])}
        />
        {file ? (
          <>
            <FileVideo size={32} className="text-ink" />
            <div className="mt-3 font-semibold text-ink">{file.name}</div>
            <div className="text-xs text-muted">{(file.size / 1e6).toFixed(1)} MB</div>
          </>
        ) : (
          <>
            <UploadCloud size={32} className="text-faint" />
            <div className="mt-3 font-semibold text-ink">Drop a video here, or click to choose</div>
            <div className="text-xs text-muted">MP4, MOV, MKV or WebM · up to {maxLabel}</div>
          </>
        )}
      </div>

      {upload.isPending && (
        <div className="mt-4">
          <div className="mb-1 flex justify-between text-xs text-muted">
            <span>Uploading</span>
            <span>{Math.round(sent * 100)}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-line">
            <div className="h-full bg-accent transition-all" style={{ width: `${sent * 100}%` }} />
          </div>
        </div>
      )}
      {rejected && (
        <p role="alert" className="mt-3 rounded-lg bg-warn/10 px-3 py-2 text-sm text-warn">
          {rejected}
        </p>
      )}
      {upload.isError && (
        <p className="mt-3 text-sm text-bad">{(upload.error as Error).message}</p>
      )}
      <div className="mt-4 flex gap-3">
        <button
          className="btn-accent"
          disabled={!file || upload.isPending}
          onClick={() => file && upload.mutate(file)}
        >
          {upload.isPending ? "Uploading…" : "Analyse video"}
        </button>
        {file && !upload.isPending && (
          <button className="btn-ghost" onClick={() => setFile(null)}>
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

export default function Analysis({ me }: { me: Me | undefined }) {
  const { bay } = useScope();
  const config = useQuery({ queryKey: ["platform-config"], queryFn: api.platformConfig });
  const jobs = useQuery({
    queryKey: ["analyses"],
    queryFn: api.analyses,
    refetchInterval: (q) =>
      q.state.data?.some((j) => j.status === "queued" || j.status === "running") ? 2000 : 15000,
  });
  const bayId = bay?.id;

  return (
    <>
      <PageHeader
        title="Video Analysis"
        subtitle="Upload recorded footage; the platform counts the crates and writes a report"
      />
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="xl:col-span-1">
          {hasRole(me, "operator") ? (
            bayId && <UploadCard bayId={bayId} maxMb={config.data?.max_upload_mb ?? 5120} />
          ) : (
            <div className="card p-5 text-sm text-muted">
              Operators and admins can upload videos. You can view completed reports.
            </div>
          )}
        </div>

        <section className="card overflow-hidden xl:col-span-2">
          <div className="border-b border-line px-5 py-4">
            <h2 className="font-semibold text-ink">Analyses</h2>
          </div>
          {jobs.data?.length ? (
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Video</th>
                  <th className="th">Status</th>
                  <th className="th text-right">Loads</th>
                  <th className="th text-right">Crates</th>
                  <th className="th">Submitted</th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((j) => (
                  <tr key={j.id} className="border-b border-line last:border-0">
                    <td className="td">
                      <Link to={`/analysis/${j.id}`} className="font-semibold text-ink hover:underline">
                        {j.filename}
                      </Link>
                      {j.duration_s !== null && (
                        <span className="ml-2 text-xs text-faint">{fmtSeconds(j.duration_s)}</span>
                      )}
                    </td>
                    <td className="td">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ${STATUS[j.status]}`}
                      >
                        {j.status === "running" && <Loader2 size={12} className="animate-spin" />}
                        {j.status}
                        {j.status === "running" && ` ${Math.round(j.progress * 100)}%`}
                      </span>
                      {j.error && <div className="mt-1 max-w-xs truncate text-xs text-bad">{j.error}</div>}
                    </td>
                    <td className="td text-right tabular-nums">{j.status === "done" ? j.loads.length : "—"}</td>
                    <td className="td text-right font-semibold tabular-nums">
                      {j.status === "done" ? j.total_crates.toLocaleString() : "—"}
                    </td>
                    <td className="td text-muted">
                      {dateTime(j.created_at)}
                      <div className="text-xs text-faint">{j.created_by}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState title="No analyses yet" body="Upload a video to get a crate-count report." />
          )}
        </section>
      </div>
    </>
  );
}
