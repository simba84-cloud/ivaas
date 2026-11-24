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

const emailStatus = {
  configured: true,
  relay: "relay.example:25",
  sender: "alerts@example.com",
  starttls: "opportunistic",
  enabled: false,
  group: "owners, admins and site managers",
  recipients: ["admin@bakers-inn.example"],
  missing_email: ["manager1"],
  recent: [
    {
      id: "e1",
      kind: "camera_offline",
      subject: "Camera offline: LPR at Bay 1",
      recipients: ["admin@bakers-inn.example"],
      status: "failed",
      attempts: 7,
      last_error: "451 try later",
      created_at: "2026-10-01T08:00:00Z",
      sent_at: null,
    },
  ],
};

const api = (email: object = emailStatus) =>
  server.use(
    http.get("/api/v1/settings", () => HttpResponse.json(payload)),
    http.get("/api/v1/alerts/email", () => HttpResponse.json(email)),
  );

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

  it("shows where alert emails go and who cannot be reached", async () => {
    api();
    renderPage(<Settings />, { path: "/settings", route: "/settings" });

    expect(await screen.findByText("relay.example:25")).toBeInTheDocument();
    expect(screen.getByText("admin@bakers-inn.example")).toBeInTheDocument();
    expect(screen.getByText(/no email\s+address: manager1/)).toBeInTheDocument();
    expect(screen.getByText("Camera offline: LPR at Bay 1")).toBeInTheDocument();
    expect(screen.getByText("451 try later")).toBeInTheDocument();
  });

  it("says plainly when there is no relay", async () => {
    api({ ...emailStatus, configured: false, relay: null, recent: [] });
    renderPage(<Settings />, { path: "/settings", route: "/settings" });

    expect(await screen.findByText(/IVAAS_SMTP_HOST is empty/)).toBeInTheDocument();
    expect(screen.getByText("No alert emails yet.")).toBeInTheDocument();
  });

  it("sends a test and shows the relay's answer", async () => {
    api();
    server.use(
      http.post("/api/v1/alerts/email/test", () =>
        HttpResponse.json({
          ...emailStatus.recent[0],
          id: "t1",
          kind: "test",
          subject: "IVaaS test email",
          status: "failed",
          last_error: "relay.example answered 554: relay access denied",
        }),
      ),
    );
    renderPage(<Settings />, { path: "/settings", route: "/settings" });

    await userEvent.click(await screen.findByRole("button", { name: /Send a test to me/ }));
    expect(await screen.findByText(/Not sent: relay.example answered 554/)).toBeInTheDocument();
  });
});
