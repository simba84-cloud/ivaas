import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileSpreadsheet, Plus, RefreshCw, Trash2, Upload } from "lucide-react";
import { type InputHTMLAttributes, useState } from "react";
import { api, saveFile } from "../api/client";
import { useScope } from "../api/scope";
import type { TallyDirection, TallySheet, TallyStatus } from "../api/types";
import { useToast } from "../components/toast";
import { EmptyState, PageHeader, dateTime } from "../components/ui";

/**
 * Entering the paper tally sheets. Deliberately blind: nothing on this page fetches a
 * session or shows the AI's count, so what is typed is what was written at the bay,
 * not a figure nudged towards the machine's.
 */

const STATUS: Record<TallyStatus, { label: string; cls: string; dot: string; hint: string }> = {
  pending: { label: "Pending", cls: "bg-ground text-muted", dot: "bg-faint", hint: "Saved, not matched yet" },
  matched: {
    label: "Truck still loading",
    cls: "bg-accent-tint text-accent",
    dot: "bg-accent",
    hint: "Matched to a session that is still open; it reconciles when the truck leaves",
  },
  reconciled: {
    label: "Reconciled",
    cls: "bg-good/10 text-good",
    dot: "bg-good",
    hint: "This sheet is now the load's manual count",
  },
  conflict: {
    label: "Conflict",
    cls: "bg-warn/10 text-warn",
    dot: "bg-warn",
    hint: "The load already has a different manual count; an admin needs to settle it",
  },
  unmatched: {
    label: "No matching truck",
    cls: "bg-bad/10 text-bad",
    dot: "bg-bad",
    hint: "No session fits this plate, bay and time. Check them, or rematch later",
  },
};

export function TallyBadge({ status }: { status: TallyStatus }) {
  const s = STATUS[status];
  return (
    <span className={`chip ${s.cls}`} title={s.hint}>
      <span className={`dot ${s.dot}`} />
      {s.label}
    </span>
  );
}

const hhmm = (t: string | null) => (t ? t.slice(0, 5) : "—");

function useRefresh() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["tally"] });
    // a reconciled sheet changes the load's status and the dashboard's accuracy
    qc.invalidateQueries({ queryKey: ["sessions"] });
    qc.invalidateQueries({ queryKey: ["summary"] });
  };
}

function outcome(saved: TallySheet[]): string {
  const by = (s: TallyStatus) => saved.filter((x) => x.status === s).length;
  const parts = [
    by("reconciled") && `${by("reconciled")} reconciled`,
    by("matched") && `${by("matched")} waiting for the truck to leave`,
    by("unmatched") && `${by("unmatched")} with no matching truck`,
    by("conflict") && `${by("conflict")} in conflict`,
  ].filter(Boolean);
  return parts.join(" · ");
}

function ImportCsv({ bayId }: { bayId: string }) {
  const toast = useToast();
  const refresh = useRefresh();
  const [sheets, setSheets] = useState<File | null>(null);
  const [stacks, setStacks] = useState<File | null>(null);
  const [skipped, setSkipped] = useState<string[]>([]);

  const upload = useMutation({
    mutationFn: () => api.importTally(bayId, sheets!, stacks),
    onSuccess: (r) => {
      setSkipped(r.skipped);
      toast({
        severity: r.saved.some((s) => s.status === "conflict" || s.status === "unmatched") ? "warning" : "success",
        title: `${r.saved.length} sheet${r.saved.length === 1 ? "" : "s"} imported`,
        detail: outcome(r.saved),
      });
      refresh();
    },
  });

  return (
    <form
      className="card p-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (sheets) upload.mutate();
      }}
    >
      <div className="mb-3 flex items-center gap-2">
        <FileSpreadsheet size={16} className="text-brand" />
        <h2 className="panel-title">Import from the tally workbook</h2>
      </div>
      <p className="mb-3 text-xs text-muted">
        Add the tally workbook itself (.xlsx): its <b>Entry - Sheets</b> and <b>Entry - Stacks</b> tabs are read
        together. Or save those two tabs as CSV and add them here. Example rows are skipped. No workbook yet? Take
        the <b>Tally sheet template</b> above: it has the paper form to print and your bays in its drop-down. If any row has a
        problem, nothing is imported and the row is named.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="tally-sheets" className="label">
            Workbook, or Sheets CSV
          </label>
          <input
            id="tally-sheets"
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="input h-auto py-1.5"
            onChange={(e) => setSheets(e.target.files?.[0] ?? null)}
          />
        </div>
        <div>
          <label htmlFor="tally-stacks" className="label">
            Stacks CSV (not needed with the workbook)
          </label>
          <input
            id="tally-stacks"
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="input h-auto py-1.5"
            onChange={(e) => setStacks(e.target.files?.[0] ?? null)}
          />
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button className="btn-primary btn-sm" disabled={!sheets || upload.isPending}>
          <Upload size={13} /> {upload.isPending ? "Importing…" : "Import"}
        </button>
        {upload.isError && (
          <span role="alert" className="text-xs text-bad">
            {(upload.error as Error).message}
          </span>
        )}
      </div>
      {skipped.length > 0 && (
        <ul className="mt-2 text-xs text-faint">
          {skipped.map((s) => (
            <li key={s}>Skipped: {s}</li>
          ))}
        </ul>
      )}
    </form>
  );
}

interface LineRow {
  crates: string;
  note: string;
}

// the local date: a sheet written at 00:30 belongs to today, not to UTC's yesterday
const today = () => new Date().toLocaleDateString("en-CA");

function EnterSheet({ bayId }: { bayId: string }) {
  const toast = useToast();
  const refresh = useRefresh();
  const blank = {
    sheet_id: "",
    date: today(),
    plate: "",
    direction: "LOAD" as TallyDirection,
    start_time: "",
    end_time: "",
    total: "",
    counted_by: "",
    verified_by: "",
  };
  const [f, setF] = useState(blank);
  const [lines, setLines] = useState<LineRow[]>([{ crates: "", note: "" }]);
  const set = (k: keyof typeof blank) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  const typed = lines.filter((l) => l.crates.trim() !== "");
  const lineTotal = typed.reduce((a, l) => a + Number(l.crates), 0);
  const linesValid = typed.every((l) => /^-?\d+$/.test(l.crates.trim()));
  const paper = f.total.trim() === "" ? null : Number(f.total);
  const mismatch = typed.length > 0 && paper !== null && paper !== lineTotal;
  const valid =
    f.sheet_id.trim().length >= 3 &&
    f.plate.trim().length >= 2 &&
    !!f.date &&
    linesValid &&
    (typed.length > 0 || paper !== null);

  const save = useMutation({
    mutationFn: () =>
      api.enterTallySheet({
        sheet_id: f.sheet_id.trim(),
        bay_id: bayId,
        date: f.date,
        plate: f.plate.trim().toUpperCase(),
        direction: f.direction,
        start_time: f.start_time || null,
        end_time: f.end_time || null,
        total_on_paper: paper,
        counted_by: f.counted_by.trim() || null,
        verified_by: f.verified_by.trim() || null,
        // line numbers follow the paper: the n-th typed line is line n
        lines: lines
          .map((l, i) => ({ line_no: i + 1, crates: l.crates.trim(), note: l.note.trim() || null }))
          .filter((l) => l.crates !== "")
          .map((l) => ({ ...l, crates: Number(l.crates) })),
      }),
    onSuccess: (s) => {
      toast({
        severity: s.status === "reconciled" || s.status === "matched" ? "success" : "warning",
        title: `Saved ${s.sheet_id}`,
        detail: STATUS[s.status].hint,
      });
      setF({ ...blank, date: f.date, counted_by: f.counted_by, verified_by: f.verified_by });
      setLines([{ crates: "", note: "" }]);
      refresh();
    },
  });

  return (
    <form
      className="card p-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) save.mutate();
      }}
    >
      <div className="mb-3 flex items-center gap-2">
        <Plus size={16} className="text-brand" />
        <h2 className="panel-title">Type in one sheet</h2>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id="t-sheet" label="Sheet ID" value={f.sheet_id} onChange={set("sheet_id")} placeholder="BI-20261012-B1-001" />
        <Field id="t-date" label="Date" type="date" value={f.date} onChange={set("date")} />
        <Field id="t-plate" label="Truck plate" value={f.plate} onChange={set("plate")} placeholder="AGA 5372" />
        <div>
          <label htmlFor="t-direction" className="label">
            Direction
          </label>
          <select id="t-direction" className="input" value={f.direction} onChange={set("direction")}>
            <option value="LOAD">Load</option>
            <option value="RETURN">Return</option>
          </select>
        </div>
        <Field id="t-start" label="Start time" type="time" value={f.start_time} onChange={set("start_time")} />
        <Field id="t-end" label="End time" type="time" value={f.end_time} onChange={set("end_time")} />
        <Field id="t-counted" label="Counted by" value={f.counted_by} onChange={set("counted_by")} />
        <Field id="t-verified" label="Verified by" value={f.verified_by} onChange={set("verified_by")} />
        <Field id="t-total" label="Total on paper" inputMode="numeric" value={f.total} onChange={set("total")} />
      </div>

      <div className="mt-4">
        <div className="label">Dollies / stacks, in the order on the sheet</div>
        <div className="grid gap-1.5">
          {lines.map((l, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="num w-7 text-right text-xs text-faint">{i + 1}</span>
              <input
                aria-label={`Line ${i + 1} crates`}
                inputMode="numeric"
                className="input num h-8 w-24 text-right"
                placeholder="Crates"
                value={l.crates}
                onChange={(e) =>
                  setLines(lines.map((x, j) => (j === i ? { ...x, crates: e.target.value.replace(/[^\d-]/g, "") } : x)))
                }
              />
              <select
                aria-label={`Line ${i + 1} note`}
                className="input h-8 w-40 py-0 text-xs"
                value={l.note}
                onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)))}
              >
                <option value="">No note</option>
                <option value="P">P · partial / broken</option>
                <option value="X">X · carried back (negative)</option>
                <option value="D">D · damaged</option>
                <option value="?">? · unsure</option>
              </select>
              {lines.length > 1 && (
                <button
                  type="button"
                  className="btn-ghost btn-sm px-2"
                  aria-label={`Remove line ${i + 1}`}
                  onClick={() => setLines(lines.filter((_, j) => j !== i))}
                >
                  <Trash2 size={13} />
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
          <button type="button" className="btn-ghost btn-sm" onClick={() => setLines([...lines, { crates: "", note: "" }])}>
            <Plus size={13} /> Add line
          </button>
          {typed.length > 0 && (
            <span className="num text-muted">
              {typed.length} line{typed.length === 1 ? "" : "s"} · {lineTotal.toLocaleString()} crates
            </span>
          )}
          {mismatch && (
            <span role="status" className="font-semibold text-warn">
              The lines add up to {lineTotal}, the paper says {paper}. Check the typing; the lines are what counts.
            </span>
          )}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button className="btn-primary btn-sm" disabled={!valid || save.isPending}>
          {save.isPending ? "Saving…" : "Save sheet"}
        </button>
        {save.isError && (
          <span role="alert" className="text-xs text-bad">
            {(save.error as Error).message}
          </span>
        )}
      </div>
    </form>
  );
}

function Field({
  id,
  label,
  ...input
}: { id: string; label: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div>
      <label htmlFor={id} className="label">
        {label}
      </label>
      <input id={id} className="input" {...input} />
    </div>
  );
}

export default function TallySheets() {
  const { bay } = useScope();
  const refresh = useRefresh();
  const toast = useToast();
  const sheets = useQuery({ queryKey: ["tally", "sheets"], queryFn: api.tallySheets });
  const rematch = useMutation({
    mutationFn: api.rematchTally,
    onSuccess: (r) => {
      toast({
        severity: "info",
        title: r.changed.length ? `${r.changed.length} sheet(s) changed` : "Nothing new to match",
        detail: r.changed.length ? outcome(r.changed) : undefined,
      });
      refresh();
    },
  });
  const rows = (sheets.data ?? []).filter((s) => !bay || s.bay_id === bay.id);
  const template = useMutation({ mutationFn: async () => saveFile(await api.tallyTemplate()) });

  return (
    <>
      <PageHeader
        title="Tally sheets"
        subtitle="Enter the paper counts from the bay. The AI's figure is not shown here, so what you type is what was written."
        actions={
          <button className="btn-ghost" onClick={() => template.mutate()} disabled={template.isPending}>
            <Download size={15} /> Tally sheet template
          </button>
        }
      />
      {template.error && (
        <p role="alert" className="mb-3 text-xs text-bad">
          {(template.error as Error).message}
        </p>
      )}
      {bay ? (
        <div className="mb-6 grid gap-4 xl:grid-cols-2">
          <ImportCsv bayId={bay.id} />
          <EnterSheet bayId={bay.id} />
        </div>
      ) : (
        <div className="card mb-6">
          <EmptyState title="No bay set up yet" body="Tally sheets belong to a loading bay. Add one under Cameras." />
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="panel-head">
          <span className="panel-title">Sheets entered</span>
          <button className="btn-ghost btn-sm" onClick={() => rematch.mutate()} disabled={rematch.isPending}>
            <RefreshCw size={13} className={rematch.isPending ? "animate-spin" : ""} /> Match again
          </button>
        </div>
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-ground">
                <tr>
                  <th className="th">Sheet</th>
                  <th className="th">Plate</th>
                  <th className="th">Direction</th>
                  <th className="th">Date · time</th>
                  <th className="th text-right">Lines</th>
                  <th className="th text-right">Crates</th>
                  <th className="th">Status</th>
                  <th className="th">Entered</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((s) => (
                  <tr key={s.id} className="transition-colors hover:bg-ground/60">
                    <td className="td num font-semibold text-ink">{s.sheet_id}</td>
                    <td className="td num">{s.plate}</td>
                    <td className="td text-muted">{s.direction === "LOAD" ? "Load" : "Return"}</td>
                    <td className="td num text-muted">
                      {s.date} · {hhmm(s.start_time)}–{hhmm(s.end_time)}
                    </td>
                    <td className="td num text-right">{s.lines || "—"}</td>
                    <td className="td num text-right font-semibold">
                      {s.truth === null ? "—" : s.truth.toLocaleString()}
                      {s.transcription_mismatch && (
                        <div className="text-[11px] font-normal text-warn" title="The typed lines disagree with the paper total">
                          paper says {s.total_on_paper}
                        </div>
                      )}
                    </td>
                    <td className="td">
                      <TallyBadge status={s.status} />
                    </td>
                    <td className="td text-xs text-muted">
                      {s.entered_by_user} · {dateTime(s.entered_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title={sheets.isPending ? "Loading…" : "No sheets entered yet"}
            body="Import yesterday's sheets from the workbook, or type one in above."
          />
        )}
      </div>
    </>
  );
}
