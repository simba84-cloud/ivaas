import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Boxes, Clock, Layers, Printer, Truck } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { TimelineEvent } from "../api/types";
import { PageHeader, StatCard, dateTime } from "../components/ui";
import { fmtSeconds } from "./Analysis";

const KIND: Record<TimelineEvent["kind"], string> = {
  load_started: "bg-brand",
  stack_counted: "bg-accent",
  plate_read: "bg-warn",
  load_ended: "bg-faint",
};

export default function AnalysisReport() {
  const { id = "" } = useParams();
  const job = useQuery({
    queryKey: ["analysis", id],
    queryFn: () => api.analysis(id),
    refetchInterval: (q) =>
      q.state.data && (q.state.data.status === "queued" || q.state.data.status === "running")
        ? 2000
        : false,
  });
  const j = job.data;
  if (!j) return null;

  const frames = j.timeline.filter((e) => e.frame_url);
  const lowConf = j.loads.reduce((n, ld) => n + ld.low_confidence, 0);

  return (
    <div className="print:text-black">
      <PageHeader
        title={`Report · ${j.filename}`}
        subtitle={`Submitted ${dateTime(j.created_at)} by ${j.created_by}${
          j.finished_at ? ` · analysed ${dateTime(j.finished_at)}` : ""
        }`}
        actions={
          <div className="flex gap-2 print:hidden">
            <Link to="/analysis" className="btn-ghost">
              <ArrowLeft size={16} /> All analyses
            </Link>
            {j.status === "done" && (
              <button className="btn-primary" onClick={() => window.print()}>
                <Printer size={16} /> Print / PDF
              </button>
            )}
          </div>
        }
      />

      {j.status !== "done" && (
        <div className="card mb-6 p-5">
          {j.status === "failed" ? (
            <p className="text-sm text-bad">Analysis failed: {j.error}</p>
          ) : (
            <>
              <div className="mb-1 flex justify-between text-sm">
                <span className="font-semibold capitalize text-ink">{j.status}</span>
                <span className="text-muted">{Math.round(j.progress * 100)}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-line">
                <div
                  className="h-full bg-accent transition-all"
                  style={{ width: `${j.progress * 100}%` }}
                />
              </div>
            </>
          )}
        </div>
      )}

      {j.status === "done" && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="Crates counted" value={j.total_crates.toLocaleString()} icon={Boxes} accent />
            <StatCard label="Stacks" value={String(j.loads.reduce((n, l) => n + l.stacks, 0))} icon={Layers} />
            <StatCard label="Truck loads" value={String(j.loads.length)} icon={Truck} />
            <StatCard
              label="Video length"
              value={fmtSeconds(j.duration_s)}
              hint={lowConf ? `${lowConf} stack(s) counted at low confidence` : "All stacks counted with confidence"}
              icon={Clock}
            />
          </div>

          {j.summary && (
            <section className="card mt-6 p-5">
              <h2 className="mb-2 font-semibold text-ink">Summary</h2>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">{j.summary}</p>
            </section>
          )}

          <section className="card mt-6 overflow-hidden">
            <div className="border-b border-line px-5 py-4">
              <h2 className="font-semibold text-ink">Loads</h2>
            </div>
            {j.loads.length ? (
              <table className="w-full">
                <thead className="bg-ground">
                  <tr>
                    <th className="th">#</th>
                    <th className="th">Plate</th>
                    <th className="th">From</th>
                    <th className="th">To</th>
                    <th className="th text-right">Stacks</th>
                    <th className="th text-right">Crates</th>
                    <th className="th text-right">Low confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {j.loads.map((ld, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className="td tabular-nums">{i + 1}</td>
                      <td className="td num font-semibold text-ink">{ld.plate ?? "—"}</td>
                      <td className="td tabular-nums">{fmtSeconds(ld.start_s)}</td>
                      <td className="td tabular-nums">{fmtSeconds(ld.end_s)}</td>
                      <td className="td text-right tabular-nums">{ld.stacks}</td>
                      <td className="td text-right font-semibold tabular-nums">{ld.crates}</td>
                      <td className={`td text-right tabular-nums ${ld.low_confidence ? "text-warn" : ""}`}>
                        {ld.low_confidence || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="px-5 py-8 text-sm text-muted">No stacks were counted in this video.</p>
            )}
          </section>

          {frames.length > 0 && (
            <section className="mt-6">
              <h2 className="mb-3 font-semibold text-ink">Counted stacks</h2>
              <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
                {frames.map((e, i) => (
                  <figure key={i} className="card overflow-hidden">
                    <img src={e.frame_url!} alt={e.detail} className="aspect-video w-full object-cover" />
                    <figcaption className="px-2 py-1.5 text-xs">
                      <span className="font-semibold text-ink">{fmtSeconds(e.at_s)}</span>
                      <span className="ml-1 text-muted">{e.detail}</span>
                    </figcaption>
                  </figure>
                ))}
              </div>
            </section>
          )}

          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <section className="card overflow-hidden">
              <div className="border-b border-line px-5 py-4">
                <h2 className="font-semibold text-ink">Timeline</h2>
              </div>
              <ol className="max-h-[32rem] overflow-y-auto px-5 py-3">
                {j.timeline.map((e, i) => (
                  <li key={i} className="flex items-start gap-3 py-1.5 text-sm">
                    <span className={`mt-1.5 h-2.5 w-2.5 flex-none rounded-full ${KIND[e.kind]}`} />
                    <span className="w-12 flex-none num text-xs text-muted">{fmtSeconds(e.at_s)}</span>
                    <span className="text-ink">{e.detail}</span>
                  </li>
                ))}
              </ol>
            </section>
            {j.video_url && (
              <section className="card overflow-hidden print:hidden">
                <div className="border-b border-line px-5 py-4">
                  <h2 className="font-semibold text-ink">Video</h2>
                </div>
                <video src={j.video_url} controls className="w-full bg-black" />
              </section>
            )}
          </div>
        </>
      )}
    </div>
  );
}
