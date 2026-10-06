import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Download, Scale, Sigma, Target } from "lucide-react";
import { api, saveFile } from "../api/client";
import { useScope } from "../api/scope";
import { EmptyState, Metric, PageHeader, VarianceBar, pct } from "../components/ui";
import { TallyBadge } from "./TallySheets";

/**
 * The POC's success figure: the AI count against the paper tally sheet, per truck.
 * Only sheets that reconciled a load are scored; anything else is listed with its
 * reason and never counted as 0%.
 */
export default function TallyReport() {
  const { bay } = useScope();
  const report = useQuery({ queryKey: ["tally", "report"], queryFn: api.tallyReport });
  const r = report.data;
  const rows = (r?.rows ?? []).filter((x) => !bay || x.sheet.bay_id === bay.id);
  const scored = rows.filter((x) => x.accuracy !== null);
  // the headline figures follow the bay in view, like every other page
  const mean = scored.length ? scored.reduce((a, x) => a + (x.accuracy ?? 0), 0) / scored.length : null;
  const passing = scored.filter((x) => x.passed).length;
  const aiSum = scored.reduce((a, x) => a + (x.ai_count ?? 0), 0);
  const truthSum = scored.reduce((a, x) => a + (x.sheet.truth ?? 0), 0);
  const aggregate = truthSum ? Math.abs(aiSum - truthSum) / truthSum : null;
  const target = r?.target ?? 0.95;
  // the file covers what the page shows: the bay in view
  const file = useMutation({
    mutationFn: async (format: "pdf" | "xlsx") => saveFile(await api.tallyReportFile(format, bay?.id)),
  });

  return (
    <>
      <PageHeader
        title="Accuracy"
        subtitle="AI count against the paper tally sheet, per truck. This is the figure the POC is judged on."
        actions={
          <div className="flex gap-2">
            <button className="btn-ghost" disabled={file.isPending} onClick={() => file.mutate("pdf")}>
              <Download size={15} /> PDF
            </button>
            <button className="btn-ghost" disabled={file.isPending} onClick={() => file.mutate("xlsx")}>
              <Download size={15} /> Excel
            </button>
          </div>
        }
      />
      {file.error && (
        <p role="alert" className="mb-3 text-xs text-bad">
          {(file.error as Error).message}
        </p>
      )}

      <div className="card mb-6 grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Mean accuracy"
          icon={Target}
          value={pct(mean)}
          hint={mean === null ? "No sheet has reconciled a load yet" : `Target ${pct(target)}`}
          tone={mean === null ? "neutral" : mean >= target ? "good" : "warn"}
        />
        <Metric
          label="Trucks passing"
          icon={CheckCircle2}
          value={scored.length ? `${passing} / ${scored.length}` : "—"}
          hint={`at or above ${pct(target)}`}
        />
        <Metric
          label="Aggregate error"
          icon={Sigma}
          value={pct(aggregate)}
          hint="|Σ AI − Σ sheets| ÷ Σ sheets"
        />
        <Metric
          label="Sheets scored"
          icon={Scale}
          value={`${scored.length} / ${rows.length}`}
          hint="the rest are unmatched, conflicting or still loading"
        />
      </div>

      <div className="card overflow-hidden">
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-ground">
                <tr>
                  <th className="th">Sheet</th>
                  <th className="th">Plate</th>
                  <th className="th">Direction</th>
                  <th className="th">Date</th>
                  <th className="th text-right">Sheet</th>
                  <th className="th text-right">AI</th>
                  <th className="th">Variance</th>
                  <th className="th text-right">Accuracy</th>
                  <th className="th">Sheet status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((x) => (
                  <tr key={x.sheet.id} className="transition-colors hover:bg-ground/60">
                    <td className="td num font-semibold text-ink">{x.sheet.sheet_id}</td>
                    <td className="td num">{x.sheet.plate}</td>
                    <td className="td text-muted">{x.sheet.direction === "LOAD" ? "Load" : "Return"}</td>
                    <td className="td num text-muted">{x.sheet.date}</td>
                    <td className="td num text-right">{x.sheet.truth?.toLocaleString() ?? "—"}</td>
                    <td className="td num text-right font-semibold">{x.ai_count?.toLocaleString() ?? "—"}</td>
                    <td className="td">
                      <VarianceBar variance={x.variance} manual={x.accuracy === null ? null : x.sheet.truth} />
                    </td>
                    <td
                      className={`td num text-right ${x.passed === false ? "font-semibold text-warn" : x.passed ? "text-good" : ""}`}
                    >
                      {pct(x.accuracy)}
                    </td>
                    <td className="td">
                      <TallyBadge status={x.sheet.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title={report.isPending ? "Loading…" : "No tally sheets yet"}
            body="Accuracy appears here once the paper sheets are entered and matched to their trucks."
          />
        )}
      </div>
    </>
  );
}
