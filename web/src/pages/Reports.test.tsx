import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
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
});
