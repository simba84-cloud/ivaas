import { screen } from "@testing-library/react";
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
  it("lists filed reports with their PDF and CSV", async () => {
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
          },
        ]),
      ),
    );
    renderPage(<Reports />, { path: "/reports", route: "/reports" });
    expect(await screen.findByText("2026-10-01")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "PDF" })).toHaveAttribute("href", "/api/v1/objects/r.pdf?sig=x");
    expect(screen.getByRole("link", { name: "CSV" })).toHaveAttribute("href", "/api/v1/objects/r.csv?sig=x");
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
