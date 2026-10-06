import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { bay, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Reports from "./Reports";

const scope = () =>
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
  );

describe("daily reports", () => {
  it("lists filed reports with their PDF, CSV and Excel, saved under the Liquid name", async () => {
    scope();
    server.use(
      http.get("/api/v1/reports", () =>
        HttpResponse.json([
          {
            site_id: site.id,
            site: "Bakery Industrial Site",
            day: "2026-10-01",
            loads: 5,
            generated_at: "2026-10-02T04:30:00Z",
            pdf_url: "/api/v1/objects/r.pdf?sig=x",
            csv_url: "/api/v1/objects/r.csv?sig=x",
            xlsx_url: "/api/v1/objects/r.xlsx?sig=x",
            file_stem: "liquid-ivaas-bakery-industrial-site-2026-10-01",
          },
          {
            site_id: site.id,
            site: "Bakery Industrial Site",
            day: "2026-09-30",
            loads: 3,
            generated_at: "2026-10-01T04:30:00Z",
            pdf_url: "/api/v1/objects/o.pdf?sig=x",
            csv_url: "/api/v1/objects/o.csv?sig=x",
            xlsx_url: null, // filed before workbooks were
            file_stem: "liquid-ivaas-bakery-industrial-site-2026-09-30",
          },
        ]),
      ),
    );
    renderPage(<Reports />, { path: "/reports", route: "/reports" });
    expect(await screen.findByText("2026-10-01")).toBeInTheDocument();
    const [pdf] = screen.getAllByRole("link", { name: "PDF" });
    expect(pdf).toHaveAttribute("href", "/api/v1/objects/r.pdf?sig=x");
    expect(pdf).toHaveAttribute("download", "liquid-ivaas-bakery-industrial-site-2026-10-01.pdf");
    expect(screen.getAllByRole("link", { name: "CSV" })[0]).toHaveAttribute("href", "/api/v1/objects/r.csv?sig=x");
    // only the report that has a workbook offers one
    const excel = screen.getAllByRole("link", { name: "Excel" });
    expect(excel).toHaveLength(1);
    expect(excel[0]).toHaveAttribute("href", "/api/v1/objects/r.xlsx?sig=x");
    expect(excel[0]).toHaveAttribute("download", "liquid-ivaas-bakery-industrial-site-2026-10-01.xlsx");
  });

  it("builds any day as Excel and saves it under the name the API gives", async () => {
    scope();
    let asked = "";
    server.use(
      http.get("/api/v1/reports", () => HttpResponse.json([])),
      http.get("/api/v1/reports/daily", ({ request }) => {
        asked = new URL(request.url).searchParams.get("format") ?? "";
        return new HttpResponse(new Blob(["PK"]), {
          headers: {
            "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "content-disposition": 'attachment; filename="liquid-ivaas-bakery-industrial-site-2026-10-01.xlsx"',
          },
        });
      }),
    );
    const saved: string[] = [];
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    renderPage(<Reports />, { path: "/reports", route: "/reports" });
    const day = await screen.findByRole("region", { name: "A day's report" });
    const button = within(day).getByRole("button", { name: "Excel" });
    await vi.waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
    await vi.waitFor(() => expect(saved).toEqual(["liquid-ivaas-bakery-industrial-site-2026-10-01.xlsx"]));
    expect(asked).toBe("xlsx");
    click.mockRestore();
  });

  it("says when the first report will come, and offers any day now", async () => {
    scope();
    server.use(http.get("/api/v1/reports", () => HttpResponse.json([])));
    renderPage(<Reports />, { path: "/reports", route: "/reports" });
    expect(await screen.findByText(/filed after 06:00 site time/)).toBeInTheDocument();
    expect(screen.getByLabelText("Any day, built now")).toBeInTheDocument();
  });

  it("measures the POC criteria and says incomplete, not pass, when one cannot be measured", async () => {
    scope();
    const asked: URLSearchParams[] = [];
    server.use(
      http.get("/api/v1/reports", () => HttpResponse.json([])),
      http.get("/api/v1/reports/poc", ({ request }) => {
        asked.push(new URL(request.url).searchParams);
        return HttpResponse.json({
          site: "Bakery Industrial Site",
          start: "2026-10-01",
          end: "2026-10-14",
          timezone: "Africa/Harare",
          generated_at: "2026-10-15T06:00:00Z",
          verdict: "incomplete",
          criteria: [
            { name: "Accuracy", result: "pass", figure: "97.2%", target: "> 95.0%", how: "x", notes: ["120 loads verified"] },
            { name: "Speed", result: "not measured", figure: "median 21.0 min", target: "no delay", how: "x",
              notes: ["No baseline cycle time was given, so there is nothing to compare with."] },
          ],
          loads: 140, dispatched: 16000, returned: 15100, outstanding: 900, still_at_the_bay: 0,
          corrections: 3, correction_crates: -4, outstanding_value: null, currency: "USD",
          exceptions: [], nodes: [],
        });
      }),
    );
    renderPage(<Reports />, { path: "/reports", route: "/reports" });
    await userEvent.click(await screen.findByRole("button", { name: "Measure" }));
    expect(await screen.findByText("incomplete")).toBeInTheDocument();
    expect(screen.getByText(/not a pass until it is/)).toBeInTheDocument();
    expect(screen.getByText("97.2%")).toBeInTheDocument();
    expect(screen.getByText(/nothing to compare with/)).toBeInTheDocument();
    expect(screen.getByText(/not priced: no crate value given/)).toBeInTheDocument();
    // nothing typed, nothing sent: no figure defaults to one somebody made up
    expect(asked[0].has("baseline_minutes")).toBe(false);
    expect(asked[0].has("crate_value")).toBe(false);

    await userEvent.type(screen.getByLabelText("Baseline cycle (min)"), "25");
    await userEvent.type(screen.getByLabelText("Crate value"), "4.5");
    await userEvent.click(screen.getByRole("button", { name: "Measure" }));
    await vi.waitFor(() => expect(asked).toHaveLength(2));
    expect(asked[1].get("baseline_minutes")).toBe("25");
    expect(asked[1].get("crate_value")).toBe("4.5");
  });
});
