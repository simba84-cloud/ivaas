import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FileText } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { useScope } from "../api/scope";
import { EmptyState, dateTime } from "../components/ui";
import { MotionRow, SkeletonRows } from "../motion";

/** The day as a date input wants it, in the browser's own day. */
const today = () => new Date().toLocaleDateString("en-CA");

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Any day, built now: today's is as far as the day has got. */
function OnDemand() {
  const { site } = useScope();
  const [day, setDay] = useState(today());
  const fetchIt = useMutation({
    mutationFn: async (format: "pdf" | "csv") => {
      const blob = await api.dailyReport(site!.id, day, format);
      save(blob, `${(site?.name ?? "site").toLowerCase().replace(/\s+/g, "-")}-${day}.${format}`);
    },
  });
  return (
    <div className="card mb-4 flex flex-wrap items-end gap-2 p-3">
      <div>
        <label htmlFor="report-day" className="label">
          Any day, built now
        </label>
        <input id="report-day" type="date" className="input w-40" value={day} max={today()} onChange={(e) => setDay(e.target.value)} />
      </div>
      <button className="btn-ghost" disabled={!site || fetchIt.isPending} onClick={() => fetchIt.mutate("pdf")}>
        <Download size={15} /> PDF
      </button>
      <button className="btn-ghost" disabled={!site || fetchIt.isPending} onClick={() => fetchIt.mutate("csv")}>
        <Download size={15} /> CSV
      </button>
      <span className="pb-2 text-xs text-muted">for {site?.name ?? "this site"}</span>
      {fetchIt.error && (
        <span role="alert" className="w-full text-xs text-bad">
          {(fetchIt.error as Error).message}
        </span>
      )}
    </div>
  );
}

/**
 * The daily reports the POC runs on: each morning after 06:00 site time, yesterday's
 * day at each site is filed as a PDF for people and a CSV for spreadsheets.
 */
export default function Reports() {
  const reports = useQuery({ queryKey: ["reports"], queryFn: api.reports });
  const rows = reports.data ?? [];
  return (
    <>
      <div className="mb-4">
        <h1 className="text-2xl font-extrabold tracking-tight text-ink">Daily reports</h1>
        <p className="mt-0.5 text-sm text-muted">
          Filed each morning for the day before: crates out and back, accuracy against the tally
          sheets, manifest exceptions, corrections, and every load.
        </p>
      </div>
      <OnDemand />
      <div className="card overflow-hidden">
        {reports.isPending ? (
          <table className="w-full">
            <SkeletonRows rows={3} cols={4} />
          </table>
        ) : rows.length ? (
          <table className="w-full">
            <thead>
              <tr className="border-b border-line">
                <th className="th">Day</th>
                <th className="th">Site</th>
                <th className="th text-right">Loads</th>
                <th className="th">Filed</th>
                <th className="th" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <MotionRow key={`${r.site_id}-${r.day}`} index={i} className="border-b border-line last:border-0">
                  <td className="td num font-semibold text-ink">
                    <span className="inline-flex items-center gap-2">
                      <FileText size={14} className="text-muted" />
                      {r.day}
                    </span>
                  </td>
                  <td className="td text-muted">{r.site}</td>
                  <td className="td num text-right">{r.loads}</td>
                  <td className="td text-xs text-muted">{dateTime(r.generated_at)}</td>
                  <td className="td text-right">
                    <a className="btn-ghost btn-sm" href={r.pdf_url}>
                      PDF
                    </a>{" "}
                    <a className="btn-ghost btn-sm" href={r.csv_url}>
                      CSV
                    </a>
                  </td>
                </MotionRow>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState
            title="No reports filed yet"
            body="The first is filed after 06:00 site time for the day before. Any day can be built above in the meantime."
          />
        )}
      </div>
    </>
  );
}
