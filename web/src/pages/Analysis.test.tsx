import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
      http.get("/api/v1/config", () => HttpResponse.json({ max_upload_mb: 5120 })),
    );
    renderPage(<Analysis me={me(["operator"])} />, { path: "/analysis", route: "/analysis" });
    expect(await screen.findByRole("button", { name: /Analyse video/ })).toBeDisabled(); // no file yet
    expect(screen.getByText(/No analyses yet/)).toBeInTheDocument();
  });
});

describe("upload limits", () => {
  const setup = (maxMb: number) => {
    server.use(
      http.get("/api/v1/bays", () => HttpResponse.json([bay])),
      http.get("/api/v1/analysis", () => HttpResponse.json([])),
      http.get("/api/v1/config", () => HttpResponse.json({ max_upload_mb: maxMb })),
    );
    renderPage(<Analysis me={me(["operator"])} />, { path: "/analysis", route: "/analysis" });
  };

  it("states the limit the server actually enforces", async () => {
    setup(5120);
    expect(await screen.findByText(/up to 5 GB/)).toBeInTheDocument();
  });

  it("rejects an oversized file before the upload starts", async () => {
    setup(5120);
    await screen.findByText(/up to 5 GB/);

    const file = new File(["x"], "huge.mp4", { type: "video/mp4" });
    Object.defineProperty(file, "size", { value: 6 * 1024 * 1024 * 1024 });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, file);

    expect(await screen.findByRole("alert")).toHaveTextContent(/6\.0 GB\. The limit is 5 GB/);
    expect(screen.getByRole("button", { name: /Analyse video/ })).toBeDisabled();
  });

  it("accepts a file within the limit", async () => {
    setup(5120);
    await screen.findByText(/up to 5 GB/);

    const file = new File(["x"], "ok.mp4", { type: "video/mp4" });
    Object.defineProperty(file, "size", { value: 3 * 1024 * 1024 * 1024 });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, file);

    expect(await screen.findByText("ok.mp4")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
