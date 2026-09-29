import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "../test/server";
import { can, getToken, loginLocal, logout, onAuthChange } from "./session";

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

describe("permissions", () => {
  const me = (permissions: string[]) => ({ subject: "u", name: "u", roles: [], permissions });
  it("offers only what the account's roles grant", () => {
    expect(can(me(["count.read", "session.operate"]), "session.operate")).toBe(true);
    expect(can(me(["count.read"]), "session.operate")).toBe(false);
  });
  it("offers nothing when signed out or when the API sent no permissions", () => {
    expect(can(undefined, "count.read")).toBe(false);
    expect(can({ subject: "u", name: "u", roles: ["tenant_admin"] }, "count.read")).toBe(false);
  });
});
