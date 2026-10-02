import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SsoView } from "../api/types";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Login from "./Login";
import SingleSignOn from "./SingleSignOn";

const off: SsoView = {
  configured: false,
  issuer: null,
  client_id: null,
  secret_configured: false,
  domains: [],
  default_role: null,
  required: false,
  updated_by: null,
  updated_at: null,
  redirect_uri: "http://localhost:8088/api/v1/auth/sso/callback",
  sign_in_url: "http://localhost:8088/api/v1/auth/sso/start?org=bakers-inn",
};

describe("single sign-on", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("T8.2: an admin turns on Azure AD and sees what to register there", async () => {
    let saved: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/v1/sites", () => HttpResponse.json([])),
      http.get("/api/v1/bays", () => HttpResponse.json([])),
      http.get("/api/v1/sso", () => HttpResponse.json(off)),
      http.put("/api/v1/sso", async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...off, configured: true, secret_configured: true, issuer: saved.issuer });
      }),
    );
    const user = userEvent.setup();
    renderPage(<SingleSignOn />);
    expect(await screen.findByText(/Off: everyone signs in with a password/)).toBeInTheDocument();
    expect(screen.getByText(off.redirect_uri)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Issuer"), "https://login.microsoftonline.com/abc/v2.0");
    await user.type(screen.getByLabelText("Application (client) id"), "ivaas");
    await user.type(screen.getByLabelText("Client secret"), "s3cret");
    await user.type(screen.getByLabelText("Your email domains"), "bakersinn.co.zw, bakersinn.com");
    await user.selectOptions(screen.getByLabelText(/no account here/), "bay_operator");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Turn on" }));
    expect(saved).toEqual({
      issuer: "https://login.microsoftonline.com/abc/v2.0",
      client_id: "ivaas",
      client_secret: "s3cret",
      domains: ["bakersinn.co.zw", "bakersinn.com"],
      default_role: "bay_operator",
      required: true,
    });
  });

  it("offers sign-in with the organisation, and says why the provider was refused", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, search: "?sso_error=someone%40gmail.com%20is%20not%20in%20Bakers%20Inn", assign });
    const user = userEvent.setup();
    render(<Login config={{ mode: "local", oidc_issuer: null, oidc_client_id: null }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("someone@gmail.com is not in Bakers Inn");
    await user.type(screen.getByLabelText(/sign in with your organisation/i), "Bakers-Inn");
    await user.click(screen.getByRole("button", { name: /Continue with single sign-on/ }));
    expect(assign).toHaveBeenCalledWith("/api/v1/auth/sso/start?org=bakers-inn");
  });
});
