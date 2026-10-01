import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FileText } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { PocParams, PocReport } from "../api/types";
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
    <section aria-label="A day's report" className="card mb-4 flex flex-wrap items-end gap-2 p-3">
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
    </section>
  );
}

const RESULT: Record<string, string> = {
  pass: "bg-good/10 text-good",
  fail: "bg-bad/10 text-bad",
  "not measured": "bg-warn/10 text-warn",
  incomplete: "bg-warn/10 text-warn",
};
const VERDICT: Record<PocReport["verdict"], string> = {
  pass: "Every criterion measured, and every one met.",
  fail: "At least one criterion was measured and not met.",
  incomplete:
    "At least one criterion could not be measured from the records; it is not a pass until it is.",
};
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toLocaleDateString("en-CA");

/**
 * The scope's acceptance criteria, measured from the platform's records over the POC
 * window: accuracy, speed, reliability, plate reading, and what the crates add up to.
 * A criterion the records cannot support says "not measured", never pass.
 */
function PocReportPanel() {
  const { site } = useScope();
  const [start, setStart] = useState(daysAgo(13));
  const [end, setEnd] = useState(today());
  const [baseline, setBaseline] = useState("");
  const [crateValue, setCrateValue] = useState("");
  const params = (): PocParams => ({
    start,
    end,
    ...(baseline ? { baseline_minutes: Number(baseline) } : {}),
    ...(crateValue ? { crate_value: Number(crateValue) } : {}),
  });
  const measure = useMutation({ mutationFn: () => api.pocReport(site!.id, params()) });
  const file = useMutation({
    mutationFn: async (format: "pdf" | "csv") => {
      const blob = await api.pocReportFile(site!.id, params(), format);
      save(blob, `${(site?.name ?? "site").toLowerCase().replace(/\s+/g, "-")}-poc-${start}-to-${end}.${format}`);
    },
  });
  const r = measure.data;
  return (
    <section aria-labelledby="poc-heading" className="card mt-6 p-4">
      <h2 id="poc-heading" className="text-sm font-bold text-ink">
        Proof-of-concept report
      </h2>
      <p className="mt-0.5 text-xs text-muted">
        The scope's acceptance criteria over the POC's days at {site?.name ?? "this site"}. Give the
        loading cycle time measured before the system to judge speed, and a crate value to price
        the crates not yet back; without them those stay unmeasured and unpriced.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        {(
          [
            ["poc-start", "From", start, setStart, "date"],
            ["poc-end", "To", end, setEnd, "date"],
            ["poc-baseline", "Baseline cycle (min)", baseline, setBaseline, "number"],
            ["poc-value", "Crate value", crateValue, setCrateValue, "number"],
          ] as const
        ).map(([id, label, value, set, type]) => (
          <div key={id}>
            <label htmlFor={id} className="label">
              {label}
            </label>
            <input
              id={id}
              type={type}
              min={type === "number" ? 0 : undefined}
              className="input w-40"
              value={value}
              max={type === "date" ? today() : undefined}
              onChange={(e) => set(e.target.value)}
            />
          </div>
        ))}
        <button className="btn-accent" disabled={!site || measure.isPending} onClick={() => measure.mutate()}>
          {measure.isPending ? "Measuring…" : "Measure"}
        </button>
        <button className="btn-ghost" disabled={!site || file.isPending} onClick={() => file.mutate("pdf")}>
          <Download size={15} /> PDF
        </button>
        <button className="btn-ghost" disabled={!site || file.isPending} onClick={() => file.mutate("csv")}>
          <Download size={15} /> CSV
        </button>
      </div>
      {(measure.error || file.error) && (
        <p role="alert" className="mt-2 text-xs text-bad">
          {((measure.error || file.error) as Error).message}
        </p>
      )}
      {r && (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`chip uppercase ${RESULT[r.verdict]}`}>{r.verdict}</span>
            <span className="text-sm text-ink">{VERDICT[r.verdict]}</span>
          </div>
          <ul className="mt-3 divide-y divide-line">
            {r.criteria.map((c) => (
              <li key={c.name} className="py-2">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="w-24 font-semibold text-ink">{c.name}</span>
                  <span className={`chip ${RESULT[c.result]}`}>{c.result}</span>
                  <span className="num text-sm text-ink">{c.figure}</span>
                  <span className="text-xs text-muted">target {c.target}</span>
                </div>
                <ul className="mt-1 space-y-0.5 pl-24 text-xs text-muted">
                  {c.notes.map((n) => (
                    <li key={n} className="whitespace-pre-wrap">
                      {n}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted">
            {r.loads} loads: {r.dispatched.toLocaleString()} crates out, {r.returned.toLocaleString()} back,{" "}
            {r.outstanding.toLocaleString()} not yet back
            {r.outstanding_value !== null
              ? `, worth ${r.outstanding_value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${r.currency}`
              : " (not priced: no crate value given)"}
            . People corrected {r.corrections} load(s).
          </p>
        </div>
      )}
    </section>
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
      <PocReportPanel />
    </>
  );
}
