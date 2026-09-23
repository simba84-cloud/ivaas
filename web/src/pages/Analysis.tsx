import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileVideo, Loader2, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AnalysisJob } from "../api/types";
import { type Me, hasRole } from "../auth/session";
import { EmptyState, PageHeader, dateTime } from "../components/ui";

const STATUS: Record<AnalysisJob["status"], string> = {
  queued: "bg-slate-100 text-slate-600",
  running: "bg-brand-magenta-tint text-brand-magenta",
  done: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-700",
};

export function fmtSeconds(s: number | null): string {
  if (s === null) return "—";
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.round(s % 60)).padStart(2, "0")}`;
}

function UploadCard({ bayId }: { bayId: string }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sent, setSent] = useState(0);
  const [dragging, setDragging] = useState(false);

  const upload = useMutation({
    mutationFn: (f: File) => api.uploadVideo(bayId, f, setSent),
    onSuccess: () => {
      setFile(null);
      setSent(0);
      qc.invalidateQueries({ queryKey: ["analyses"] });
    },
  });

  const pick = (f: File | undefined) => {
    if (f && f.type.startsWith("video/")) setFile(f);
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
          dragging ? "border-brand-magenta bg-brand-magenta-tint" : "border-slate-300 hover:border-brand-navy"
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
            <FileVideo size={32} className="text-brand-navy" />
            <div className="mt-3 font-semibold text-brand-navy">{file.name}</div>
            <div className="text-xs text-slate-500">{(file.size / 1e6).toFixed(1)} MB</div>
          </>
        ) : (
          <>
            <UploadCloud size={32} className="text-slate-400" />
            <div className="mt-3 font-semibold text-brand-navy">Drop a video here, or click to choose</div>
            <div className="text-xs text-slate-500">MP4, MOV, MKV or WebM · up to 2 GB</div>
          </>
        )}
      </div>

      {upload.isPending && (
        <div className="mt-4">
          <div className="mb-1 flex justify-between text-xs text-slate-500">
            <span>Uploading</span>
            <span>{Math.round(sent * 100)}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-slate-200">
            <div className="h-full bg-brand-magenta transition-all" style={{ width: `${sent * 100}%` }} />
          </div>
        </div>
      )}
      {upload.isError && (
        <p className="mt-3 text-sm text-red-600">{(upload.error as Error).message}</p>
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
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const jobs = useQuery({
    queryKey: ["analyses"],
    queryFn: api.analyses,
    refetchInterval: (q) =>
      q.state.data?.some((j) => j.status === "queued" || j.status === "running") ? 2000 : 15000,
  });
  const bayId = bays.data?.[0]?.id;

  return (
    <>
      <PageHeader
        title="Video Analysis"
        subtitle="Upload recorded footage; the platform counts the crates and writes a report"
      />
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="xl:col-span-1">
          {hasRole(me, "operator") ? (
            bayId && <UploadCard bayId={bayId} />
          ) : (
            <div className="card p-5 text-sm text-slate-500">
              Operators and admins can upload videos. You can view completed reports.
            </div>
          )}
        </div>

        <section className="card overflow-hidden xl:col-span-2">
          <div className="border-b border-slate-100 px-5 py-4">
            <h2 className="font-semibold text-brand-navy">Analyses</h2>
          </div>
          {jobs.data?.length ? (
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-100">
                  <th className="th">Video</th>
                  <th className="th">Status</th>
                  <th className="th text-right">Loads</th>
                  <th className="th text-right">Crates</th>
                  <th className="th">Submitted</th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((j) => (
                  <tr key={j.id} className="border-b border-slate-50 last:border-0">
                    <td className="td">
                      <Link to={`/analysis/${j.id}`} className="font-semibold text-brand-navy hover:underline">
                        {j.filename}
                      </Link>
                      {j.duration_s !== null && (
                        <span className="ml-2 text-xs text-slate-400">{fmtSeconds(j.duration_s)}</span>
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
                      {j.error && <div className="mt-1 max-w-xs truncate text-xs text-red-600">{j.error}</div>}
                    </td>
                    <td className="td text-right tabular-nums">{j.status === "done" ? j.loads.length : "—"}</td>
                    <td className="td text-right font-semibold tabular-nums">
                      {j.status === "done" ? j.total_crates.toLocaleString() : "—"}
                    </td>
                    <td className="td text-slate-500">
                      {dateTime(j.created_at)}
                      <div className="text-xs text-slate-400">{j.created_by}</div>
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
