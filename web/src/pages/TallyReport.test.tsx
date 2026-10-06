import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import type { TallyReport as Report, TallyReportRow, TallySheet } from "../api/types";
import { bay, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import TallyReport from "./TallyReport";

const sheet = (over: Partial<TallySheet> = {}): TallySheet => ({
  id: "t1",
  sheet_id: "BI-1",
  bay_id: bay.id,
  date: "2026-10-12",
  plate: "AGA 5372",
  direction: "LOAD",
  start_time: "06:40:00",
  end_time: "07:05:00",
  lines: 3,
  line_total: 100,
  total_on_paper: 100,
  truth: 100,
  transcription_mismatch: false,
  counted_by: null,
  verified_by: null,
  status: "reconciled",
  entered_by_user: "operator",
  entered_at: "2026-10-13T05:00:00Z",
  ...over,
});

const row = (over: Partial<TallyReportRow> = {}, s: Partial<TallySheet> = {}): TallyReportRow => ({
  sheet: sheet(s),
  session_id: "s1",
  session_status: "reconciled",
  ai_count: 98,
  variance: -2,
  accuracy: 0.98,
  passed: true,
  ...over,
});

function api(rows: TallyReportRow[]) {
  const report: Report = {
    target: 0.95,
    sheets: rows.length,
    reconciled: rows.filter((r) => r.accuracy !== null).length,
    passing: 0,
    mean_accuracy: null,
    aggregate_error: null,
    rows,
  };
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get("/api/v1/sessions", () => HttpResponse.json([])), // the shell's live feed
    http.get("/api/v1/tally/report", () => HttpResponse.json(report)),
  );
}

const render = () => renderPage(<TallyReport />, { path: "/accuracy", route: "/accuracy" });

describe("accuracy against tally sheets", () => {
  it("scores reconciled sheets only, and lists the rest without a figure", async () => {
    api([
      row(),
      row({ ai_count: 90, variance: -10, accuracy: 0.9, passed: false }, { id: "t2", sheet_id: "BI-2" }),
      row(
        { session_id: null, session_status: null, ai_count: null, variance: null, accuracy: null, passed: null },
        { id: "t3", sheet_id: "BI-3", status: "unmatched" },
      ),
    ]);
    render();

    expect(await screen.findByText("94.0%")).toBeInTheDocument(); // mean of 98% and 90%
    expect(screen.getByText("1 / 2")).toBeInTheDocument(); // trucks passing
    expect(screen.getByText("2 / 3")).toBeInTheDocument(); // sheets scored
    expect(screen.getByText("6.0%")).toBeInTheDocument(); // |188 - 200| / 200

    const unmatched = screen.getByText("BI-3").closest("tr") as HTMLElement;
    expect(within(unmatched).getByText("No matching truck")).toBeInTheDocument();
    expect(within(unmatched).queryByText("0.0%")).not.toBeInTheDocument();
  });

  it("says there is nothing to score rather than showing 0%", async () => {
    api([]);
    render();

    expect(await screen.findByText("No tally sheets yet")).toBeInTheDocument();
    expect(screen.getByText("No sheet has reconciled a load yet")).toBeInTheDocument();
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
  });

  it("downloads the bay in view as PDF or Excel, under the name the API gives", async () => {
    api([]);
    const asked: string[] = [];
    server.use(
      http.get("/api/v1/tally/report", ({ request }) => {
        const q = new URL(request.url).searchParams;
        if (!q.get("format")) return undefined; // the page's own JSON: the handler above
        asked.push(`${q.get("format")} ${q.get("bay_id")}`);
        return new HttpResponse(new Blob(["%PDF"]), {
          headers: { "content-disposition": `attachment; filename="liquid-ivaas-bakers-inn-accuracy.${q.get("format")}"` },
        });
      }),
    );
    const saved: string[] = [];
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    render();
    await screen.findByText("No tally sheets yet");
    await userEvent.click(screen.getByRole("button", { name: "Excel" }));
    await vi.waitFor(() => expect(saved).toEqual(["liquid-ivaas-bakers-inn-accuracy.xlsx"]));
    await userEvent.click(screen.getByRole("button", { name: "PDF" }));
    await vi.waitFor(() => expect(saved).toHaveLength(2));
    expect(asked).toEqual([`xlsx ${bay.id}`, `pdf ${bay.id}`]);
    click.mockRestore();
  });
});
