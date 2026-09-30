import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../test/server";
import { EvidenceClips } from "./EvidenceClips";

const clip = (over = {}) => ({
  id: "e1",
  session_id: "s1",
  camera_id: "c1",
  kind: "crossing",
  started_at: "2026-10-01T08:00:00Z",
  ended_at: "2026-10-01T08:00:08Z",
  seconds: 8,
  size_bytes: 2048,
  sha256: "ab".repeat(32),
  expires_at: "2026-12-30T08:00:00Z",
  url: "/api/v1/objects/tenants/t/evidence/e1.mp4?exp=1&sig=x",
  ...over,
});

const show = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <EvidenceClips sessionId="s1" />
    </QueryClientProvider>,
  );

describe("evidence clips", () => {
  it("lists a load's clips and plays one from its signed link", async () => {
    server.use(
      http.get("/api/v1/sessions/s1/evidence", () =>
        HttpResponse.json([clip(), clip({ id: "e2", kind: "plate", seconds: 6 })]),
      ),
    );
    show();
    expect(await screen.findByText(/Evidence · 2 clips/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Crossing .* 8 s/ }));
    const video = screen.getByLabelText(/Crossing at/) as HTMLVideoElement;
    expect(video.getAttribute("src")).toContain("sig=x");
    expect(screen.getByText(/kept until/)).toBeInTheDocument();
  });

  it("says plainly when a load has no evidence, and why", async () => {
    server.use(http.get("/api/v1/sessions/s1/evidence", () => HttpResponse.json([])));
    show();
    expect(await screen.findByText(/No evidence clips for this load/)).toBeInTheDocument();
    expect(screen.getByText(/chokepoint and plate/)).toBeInTheDocument();
  });
});
