import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "../test/server";
import { session } from "../test/fixtures";
import { api } from "./client";

describe("api client", () => {
  it("sends the bearer token and parses JSON", async () => {
    sessionStorage.setItem("ivaas.token", "tok");
    let auth = "";
    server.use(
      http.get("/api/v1/sessions", ({ request }) => {
        auth = request.headers.get("authorization") ?? "";
        return HttpResponse.json([session()]);
      }),
    );
    const rows = await api.sessions();
    expect(auth).toBe("Bearer tok");
    expect(rows[0].plate).toBe("ABC 1234");
  });

  it("turns an error body into a readable Error", async () => {
    server.use(
      http.post("/api/v1/sessions/s1/reconcile", () =>
        HttpResponse.json({ detail: "session s1 must be closed before reconciliation" }, { status: 409 }),
      ),
    );
    await expect(api.reconcile("s1", 10)).rejects.toThrow("must be closed before reconciliation");
  });

  it("announces a 401 so the app can sign the user out", async () => {
    const onUnauthorized = vi.fn();
    window.addEventListener("ivaas:unauthorized", onUnauthorized);
    server.use(http.get("/api/v1/summary", () => HttpResponse.json({ detail: "expired" }, { status: 401 })));
    await expect(api.summary()).rejects.toThrow();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    window.removeEventListener("ivaas:unauthorized", onUnauthorized);
  });

  it("treats 204 as success with no body", async () => {
    server.use(http.delete("/api/v1/cameras/c1", () => new HttpResponse(null, { status: 204 })));
    await expect(api.removeCamera("c1")).resolves.toBeUndefined();
  });
});
