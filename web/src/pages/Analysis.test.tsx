import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { bay, job } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Analysis from "./Analysis";

const me = (roles: string[]) => ({ subject: "u", name: "u", roles });

describe("analysis list", () => {
  it("lists jobs with status, loads and crates; viewers cannot upload", async () => {
    server.use(
      http.get("/api/v1/bays", () => HttpResponse.json([bay])),
      http.get("/api/v1/analysis", () =>
        HttpResponse.json([job(), job({ id: "j2", filename: "b.mp4", status: "queued", loads: [], total_crates: 0 })]),
      ),
    );
    renderPage(<Analysis me={me(["viewer"])} />, { path: "/analysis", route: "/analysis" });
    expect(await screen.findByRole("link", { name: "loading.mp4" })).toHaveAttribute("href", "/analysis/j1");
    expect(screen.getByText("queued")).toBeInTheDocument();
    expect(screen.getByText(/Operators and admins can upload/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Analyse video/ })).not.toBeInTheDocument();
  });

  it("operators get the upload card", async () => {
    server.use(
      http.get("/api/v1/bays", () => HttpResponse.json([bay])),
      http.get("/api/v1/analysis", () => HttpResponse.json([])),
    );
    renderPage(<Analysis me={me(["operator"])} />, { path: "/analysis", route: "/analysis" });
    expect(await screen.findByRole("button", { name: /Analyse video/ })).toBeDisabled(); // no file yet
    expect(screen.getByText(/No analyses yet/)).toBeInTheDocument();
  });
});
