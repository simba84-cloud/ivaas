import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "../test/server";
import { getToken, hasRole, loginLocal, logout, onAuthChange } from "./session";

describe("local login", () => {
  it("stores the token and notifies listeners", async () => {
    server.use(
      http.post("/api/v1/auth/login", async ({ request }) => {
        const body = (await request.json()) as { username: string; password: string };
        return body.password === "operator"
          ? HttpResponse.json({ access_token: "tok-123", token_type: "bearer" })
          : HttpResponse.json({ detail: "invalid username or password" }, { status: 401 });
      }),
    );
    const listener = vi.fn();
    const off = onAuthChange(listener);
    await loginLocal("operator", "operator");
    expect(getToken()).toBe("tok-123");
    expect(listener).toHaveBeenCalledTimes(1);
    off();
  });

  it("surfaces the API's message on a bad password", async () => {
    server.use(
      http.post("/api/v1/auth/login", () =>
        HttpResponse.json({ detail: "invalid username or password" }, { status: 401 }),
      ),
    );
    await expect(loginLocal("operator", "wrong")).rejects.toThrow("invalid username or password");
    expect(getToken()).toBeNull();
  });

  it("logout clears the token", async () => {
    sessionStorage.setItem("ivaas.token", "x");
    await logout({ mode: "local", oidc_issuer: null, oidc_client_id: null });
    expect(getToken()).toBeNull();
  });
});

describe("role hierarchy", () => {
  const me = (roles: string[]) => ({ subject: "u", name: "u", roles });
  it("admin implies operator and viewer", () => {
    expect(hasRole(me(["admin"]), "viewer")).toBe(true);
    expect(hasRole(me(["admin"]), "operator")).toBe(true);
  });
  it("viewer does not imply operator", () => {
    expect(hasRole(me(["viewer"]), "operator")).toBe(false);
  });
  it("service is not a portal role", () => {
    expect(hasRole(me(["service"]), "viewer")).toBe(false);
    expect(hasRole(undefined, "viewer")).toBe(false);
  });
});
