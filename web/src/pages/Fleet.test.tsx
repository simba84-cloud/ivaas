import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { bay, meAs, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Fleet from "./Fleet";

const scope = () =>
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
  );

describe("fleet register", () => {
  it("lists the trucks", async () => {
    scope();
    server.use(
      http.get("/api/v1/fleet", () =>
        HttpResponse.json([
          { id: "v1", plate: "ABC 1234", fleet_number: "SL-01", operator: "Superlink", notes: "", active: true, created_at: null },
        ]),
      ),
    );
    renderPage(<Fleet me={meAs(["admin"])} />, { path: "/fleet", route: "/fleet" });
    expect(await screen.findByText("ABC 1234")).toBeInTheDocument();
    expect(screen.getByText("Superlink")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add truck/ })).toBeInTheDocument();
  });

  it("says what an empty register means, and offers no editing without the right", async () => {
    scope();
    server.use(http.get("/api/v1/fleet", () => HttpResponse.json([])));
    renderPage(<Fleet me={meAs(["operator"])} />, { path: "/fleet", route: "/fleet" });
    expect(await screen.findByText(/not checked against a fleet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add truck/ })).not.toBeInTheDocument();
  });
});
