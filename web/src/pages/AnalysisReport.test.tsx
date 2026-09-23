import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { job } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import AnalysisReport from "./AnalysisReport";

const at = (j: ReturnType<typeof job>) => {
  server.use(http.get("/api/v1/analysis/j1", () => HttpResponse.json(j)));
  return renderPage(<AnalysisReport />, { path: "/analysis/j1", route: "/analysis/:id" });
};

describe("analysis report", () => {
  it("shows totals, loads, the summary and one image per counted stack", async () => {
    at(job());
    expect(await screen.findByText("Report · loading.mp4")).toBeInTheDocument();
    expect(screen.getByText("Crates counted").parentElement!.parentElement).toHaveTextContent("16");
    expect(screen.getByText("One truck load, three stacks, 16 crates.")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /1 — 0:32 1:28 3 16/ })).toBeInTheDocument();
    const imgs = screen.getAllByRole("img", { name: /Stack of/ });
    expect(imgs).toHaveLength(2);
    expect(imgs[0]).toHaveAttribute("src", expect.stringContaining("sig="));
    expect(screen.getByRole("button", { name: /Print/ })).toBeInTheDocument();
  });

  it("shows progress while running and no report sections", async () => {
    at(job({ status: "running", progress: 0.4, loads: [], timeline: [], summary: null }));
    expect(await screen.findByText("running")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
    expect(screen.queryByText("Loads")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Print/ })).not.toBeInTheDocument();
  });

  it("shows the error for a failed analysis", async () => {
    at(job({ status: "failed", error: "ValueError: cannot open video", loads: [], timeline: [] }));
    expect(await screen.findByText(/cannot open video/)).toBeInTheDocument();
  });

  it("says so when nothing was counted", async () => {
    at(job({ loads: [], timeline: [], total_crates: 0, summary: null }));
    expect(await screen.findByText(/No stacks were counted/)).toBeInTheDocument();
  });
});
