import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Users from "./Users";

const user = (over = {}) => ({
  username: "operator",
  display_name: "Bay Operator",
  roles: ["operator"],
  disabled: false,
  must_change_password: false,
  password_is_default: false,
  created_at: "2026-09-01T08:00:00Z",
  password_changed_at: "2026-09-01T08:00:00Z",
  last_login_at: "2026-09-25T09:15:00Z",
  ...over,
});

const api = (users = [user()]) =>
  server.use(http.get("/api/v1/users", () => HttpResponse.json(users)));

const row = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;

describe("user management", () => {
  it("lists accounts with their role and last sign-in", async () => {
    api();
    renderPage(<Users me="admin" />, { path: "/users", route: "/users" });

    await screen.findByText("Bay Operator");
    const r = row("Bay Operator");
    expect(within(r).getByLabelText("Role for operator")).toHaveValue("operator");
    // the exact format is the runtime locale's business; the date is ours
    expect(within(r).getByText(/2026/)).toBeInTheDocument();
  });

  it("shows a reset password once, and says it cannot be shown again", async () => {
    api();
    server.use(
      http.post("/api/v1/users/operator/reset-password", () =>
        HttpResponse.json({
          user: user({ must_change_password: true }),
          temporary_password: "Kd8mQp2rTv6xYh3n",
        }),
      ),
    );
    renderPage(<Users me="admin" />, { path: "/users", route: "/users" });

    await userEvent.click(await screen.findByRole("button", { name: /Reset password/ }));

    expect(await screen.findByText("Kd8mQp2rTv6xYh3n")).toBeInTheDocument();
    expect(screen.getByText(/cannot be shown again/)).toBeInTheDocument();
  });

  it("warns loudly about accounts still on their seeded password", async () => {
    api([user({ password_is_default: true }), user({ username: "admin", display_name: "Admin" })]);
    renderPage(<Users me="admin" />, { path: "/users", route: "/users" });

    expect(
      await screen.findByText(/1 account is still using the password it was created with/),
    ).toBeInTheDocument();
  });

  it("sends the new role when it is changed", async () => {
    api();
    const assigned = vi.fn();
    server.use(
      http.put("/api/v1/users/operator/roles", async ({ request }) => {
        assigned(await request.json());
        return HttpResponse.json(user({ roles: ["admin"] }));
      }),
    );
    renderPage(<Users me="admin" />, { path: "/users", route: "/users" });

    await userEvent.selectOptions(await screen.findByLabelText("Role for operator"), "admin");
    expect(assigned).toHaveBeenCalledWith({ roles: ["admin"] });
  });

  it("will not let you disable your own account", async () => {
    api([user({ username: "admin", display_name: "Site Admin", roles: ["admin"] })]);
    renderPage(<Users me="admin" />, { path: "/users", route: "/users" });

    await screen.findByText("Site Admin");
    const r = row("Site Admin");
    expect(within(r).getByRole("button", { name: "Disable" })).toBeDisabled();
  });

  it("surfaces the reason the API refused a change", async () => {
    api([user({ username: "admin", display_name: "Site Admin", roles: ["admin"] })]);
    server.use(
      http.put("/api/v1/users/admin/roles", () =>
        HttpResponse.json(
          { detail: "you cannot remove your own administrator role from your own account" },
          { status: 409 },
        ),
      ),
    );
    renderPage(<Users me="admin" />, { path: "/users", route: "/users" });

    await userEvent.selectOptions(await screen.findByLabelText("Role for admin"), "viewer");
    expect(await screen.findByRole("alert")).toHaveTextContent(/cannot remove your own/);
  });
});
