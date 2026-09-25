import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Settings from "./Settings";

const payload = {
  editable: [
    {
      key: "reconcile_tolerance",
      label: "Counting accuracy target",
      help: "A verified load at or above this accuracy is reconciled.",
      kind: "percent",
      choices: [],
      minimum: 0.5,
      maximum: 1,
      value: 0.95,
      overridden: false,
    },
    {
      key: "auto_open_direction",
      label: "A plate at an idle bay opens",
      help: "What to assume a truck is doing.",
      kind: "choice",
      choices: ["loading", "offloading", ""],
      minimum: null,
      maximum: null,
      value: "loading",
      overridden: true,
    },
  ],
  security: [
    { label: "Sign-in", value: "Local accounts", detail: "Accounts are defined in configuration." },
    { label: "Camera credentials at rest", value: "Configured", detail: null },
  ],
  platform: [{ label: "Upload limit", value: "5120 MB", detail: "IVAAS_MAX_UPLOAD_MB" }],
};

const api = () => server.use(http.get("/api/v1/settings", () => HttpResponse.json(payload)));

describe("system settings", () => {
  it("shows a ratio in the units an admin thinks in", async () => {
    api();
    renderPage(<Settings />, { path: "/settings", route: "/settings" });

    // stored as 0.95, shown as 95%
    expect(await screen.findByLabelText("Counting accuracy target")).toHaveValue("95");
    expect(screen.getByText("%")).toBeInTheDocument();
  });

  it("marks which rules have been changed from the deployed default", async () => {
    api();
    renderPage(<Settings />, { path: "/settings", route: "/settings" });

    await screen.findByLabelText("Counting accuracy target");
    expect(screen.getByText("Changed here")).toBeInTheDocument();
  });

  it("sends the value back as a ratio, not a percentage", async () => {
    api();
    const saved = vi.fn();
    server.use(
      http.put("/api/v1/settings/reconcile_tolerance", async ({ request }) => {
        saved(await request.json());
        return HttpResponse.json(payload);
      }),
    );
    renderPage(<Settings />, { path: "/settings", route: "/settings" });

    const field = await screen.findByLabelText("Counting accuracy target");
    await userEvent.clear(field);
    await userEvent.type(field, "90");
    await userEvent.click(screen.getAllByRole("button", { name: /Save/ })[0]);

    expect(saved).toHaveBeenCalledWith({ value: 0.9 });
  });

  it("separates what can be changed from what needs a redeploy", async () => {
    api();
    renderPage(<Settings />, { path: "/settings", route: "/settings" });

    expect(await screen.findByText("Operating rules")).toBeInTheDocument();
    expect(screen.getByText("Account security")).toBeInTheDocument();
    expect(screen.getByText("Deployment")).toBeInTheDocument();
    expect(screen.getByText(/set by environment, needs a restart/)).toBeInTheDocument();
    // deployment facts are stated, never made editable
    expect(screen.getByText("5120 MB")).toBeInTheDocument();
    expect(screen.queryByLabelText("Upload limit")).not.toBeInTheDocument();
  });
});
