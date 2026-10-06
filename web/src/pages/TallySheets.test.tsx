import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { emitLive } from "../api/live";
import { useScope } from "../api/scope";
import type { TallySheet } from "../api/types";
import { bay, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import TallySheets from "./TallySheets";

const sheet = (over: Partial<TallySheet> = {}): TallySheet => ({
  id: "t1",
  sheet_id: "BI-20261012-B1-001",
  bay_id: bay.id,
  date: "2026-10-12",
  plate: "AGA 5372",
  direction: "LOAD",
  start_time: "06:40:00",
  end_time: "07:05:00",
  lines: 3,
  line_total: 94,
  total_on_paper: 94,
  truth: 94,
  transcription_mismatch: false,
  counted_by: "R. Ncube",
  verified_by: "S. Dube",
  status: "reconciled",
  entered_by_user: "operator",
  entered_at: "2026-10-13T05:00:00Z",
  ...over,
});

function api(rows: TallySheet[] = [sheet()]) {
  server.use(
    // the shell's scope and live feed load these on every page
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get("/api/v1/sessions", () => HttpResponse.json([])),
    http.get("/api/v1/tally/sheets", () => HttpResponse.json(rows)),
  );
}

/** A truck leaving: the live stream reports the AI's count for it. */
const truckLeft = () =>
  act(() =>
    emitLive({
      subject: "ivaas.session.closed",
      data: { id: "s9", bay_id: bay.id, plate: "ABC 1234", ai_count: 42, status: "closed" },
      at: Date.now(),
    }),
  );

const render = () => renderPage(<TallySheets />, { path: "/tally", route: "/tally" });

function BayInView() {
  return <p>{useScope().bay?.name ?? "no bay yet"}</p>;
}

describe("tally sheets", () => {
  it("lists what was entered and where it stands, with no AI figure anywhere", async () => {
    api([
      sheet(),
      sheet({ id: "t2", sheet_id: "BI-2", plate: "AFY 2210", status: "unmatched" }),
      sheet({ id: "t3", sheet_id: "BI-3", status: "matched", transcription_mismatch: true, truth: 60, total_on_paper: 64 }),
    ]);
    render();

    const first = (await screen.findByText("BI-20261012-B1-001")).closest("tr") as HTMLElement;
    expect(within(first).getByText("Reconciled")).toBeInTheDocument();
    expect(within(first).getByText("06:40–07:05", { exact: false })).toBeInTheDocument();
    expect(within(screen.getByText("BI-2").closest("tr")!).getByText("No matching truck")).toBeInTheDocument();
    const third = screen.getByText("BI-3").closest("tr") as HTMLElement;
    expect(within(third).getByText("Truck still loading")).toBeInTheDocument();
    expect(within(third).getByText("paper says 64")).toBeInTheDocument();
    expect(screen.queryByText(/AI \d/)).not.toBeInTheDocument();
  });

  it("holds back live count toasts while a sheet is being entered", async () => {
    api([]);
    render();
    await screen.findByText("Import from the tally workbook"); // rendered once the bay is known

    truckLeft();
    expect(screen.queryByText(/Load closed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/42/)).not.toBeInTheDocument();
  });

  it("the same event does interrupt on any other page", async () => {
    api([]);
    renderPage(<BayInView />, { path: "/sessions", route: "/sessions" });
    // the live feed only reports the bay in view, so wait for it to be known
    await screen.findByText(bay.name);

    truckLeft();
    expect(await screen.findByText(/Load closed/)).toBeInTheDocument();
  });

  it("imports the two CSVs for the bay in view and says how they landed", async () => {
    api([]);
    const seen = vi.fn();
    server.use(
      // jsdom's FormData does not survive Node's fetch, so the multipart body itself is
      // checked against the real API (tests/test_tally_api.py), not here
      http.post("/api/v1/tally/import", ({ request }) => {
        seen(new URL(request.url).searchParams.get("bay_id"));
        return HttpResponse.json({
          saved: [sheet(), sheet({ id: "t2", sheet_id: "BI-2", status: "unmatched" })],
          skipped: ["Sheets row 2: EXAMPLE-1 is the example row"],
        });
      }),
    );
    render();

    const csv = (name: string) => new File(["sheet_id\n"], name, { type: "text/csv" });
    await userEvent.upload(await screen.findByLabelText("Workbook, or Sheets CSV"), csv("Entry - Sheets.csv"));
    await userEvent.upload(screen.getByLabelText("Stacks CSV (not needed with the workbook)"), csv("Entry - Stacks.csv"));
    await userEvent.click(screen.getByRole("button", { name: "Import" }));

    expect(await screen.findByText("2 sheets imported")).toBeInTheDocument();
    expect(screen.getByText("1 reconciled · 1 with no matching truck")).toBeInTheDocument();
    expect(screen.getByText("Skipped: Sheets row 2: EXAMPLE-1 is the example row")).toBeInTheDocument();
    expect(seen).toHaveBeenCalledWith(bay.id);
  });

  it("takes the tally workbook itself, not only its CSVs", async () => {
    api([]);
    render();
    const picker = await screen.findByLabelText("Workbook, or Sheets CSV");
    expect(picker).toHaveAttribute("accept", expect.stringContaining(".xlsx"));
    expect(screen.getByText(/Add the tally workbook itself \(\.xlsx\)/)).toBeInTheDocument();
  });

  it("hands out the tally sheet template under the name the API gives", async () => {
    api([]);
    server.use(
      http.get("/api/v1/tally/template", () =>
        new HttpResponse(new Blob(["PK"]), {
          headers: { "content-disposition": 'attachment; filename="liquid-ivaas-bakers-inn-tally-sheet.xlsx"' },
        }),
      ),
    );
    const saved: string[] = [];
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Tally sheet template" }));
    await vi.waitFor(() => expect(saved).toEqual(["liquid-ivaas-bakers-inn-tally-sheet.xlsx"]));
    click.mockRestore();
  });

  it("shows why a file was refused", async () => {
    api([]);
    server.use(
      http.post("/api/v1/tally/import", () =>
        HttpResponse.json(
          { detail: "Nothing was imported. Sheets row 3 (BI-1): direction must be LOAD or RETURN" },
          { status: 422 },
        ),
      ),
    );
    render();

    await userEvent.upload(await screen.findByLabelText("Workbook, or Sheets CSV"), new File(["x"], "s.csv"));
    await userEvent.click(screen.getByRole("button", { name: "Import" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Sheets row 3 (BI-1): direction must be LOAD or RETURN");
  });

  it("types in a sheet line by line and flags lines that disagree with the paper total", async () => {
    api([]);
    const posted = vi.fn();
    server.use(
      http.post("/api/v1/tally/sheets", async ({ request }) => {
        const body = await request.json();
        posted(body);
        return HttpResponse.json(sheet({ sheet_id: "BI-9", status: "matched" }), { status: 201 });
      }),
    );
    render();

    await userEvent.type(await screen.findByLabelText("Sheet ID"), "BI-9");
    await userEvent.type(screen.getByLabelText("Truck plate"), "aga 5372");
    await userEvent.type(screen.getByLabelText("Total on paper"), "64");
    await userEvent.type(screen.getByLabelText("Line 1 crates"), "32");
    await userEvent.click(screen.getByRole("button", { name: "Add line" }));
    await userEvent.type(screen.getByLabelText("Line 2 crates"), "30");
    await userEvent.selectOptions(screen.getByLabelText("Line 2 note"), "P");

    expect(screen.getByRole("status")).toHaveTextContent("The lines add up to 62, the paper says 64");
    await userEvent.click(screen.getByRole("button", { name: "Save sheet" }));

    expect(await screen.findByText("Saved BI-9")).toBeInTheDocument();
    expect(posted).toHaveBeenCalledWith(
      expect.objectContaining({
        sheet_id: "BI-9",
        bay_id: bay.id,
        plate: "AGA 5372",
        direction: "LOAD",
        total_on_paper: 64,
        lines: [
          { line_no: 1, crates: 32, note: null },
          { line_no: 2, crates: 30, note: "P" },
        ],
      }),
    );
  });
});
