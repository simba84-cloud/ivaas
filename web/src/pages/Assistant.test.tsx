import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
import { describe, expect, it } from "vitest";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Assistant from "./Assistant";

function api(reply = { reply: "Two loads were disputed today.", tools_used: [{ name: "list_sessions", arguments: { status: "disputed", days: 1 } }] }) {
  server.use(
    http.get("/api/v1/assistant/status", () => HttpResponse.json({ enabled: true, model: "qwen" })),
    http.post("/api/v1/assistant/chat", async () => {
      await delay(50);
      return HttpResponse.json(reply);
    }),
  );
}

const show = () => renderPage(<Assistant />, { path: "/assistant", route: "/assistant" });

describe("assistant", () => {
  it("says it is working while the reply is computed", async () => {
    api();
    show();
    await userEvent.click(await screen.findByText("Show me today's disputed sessions"));
    expect(await screen.findByRole("status", { name: /querying platform data/i })).toBeInTheDocument();
    expect(await screen.findByText("Two loads were disputed today.")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("status", { name: /querying platform data/i })).not.toBeInTheDocument(),
    );
  });

  it("shows which data a reply came from, with the parameters it ran with", async () => {
    api();
    show();
    await userEvent.type(await screen.findByLabelText("Message"), "Disputes today?{Enter}");
    const sources = await screen.findByRole("button", { expanded: false, name: /Truck sessions/ });
    await userEvent.click(sources);
    expect(sources).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("status: disputed · days: 1")).toBeInTheDocument();
  });

  it("warns when a reply queried no data", async () => {
    api({ reply: "Hello!", tools_used: [] });
    show();
    await userEvent.type(await screen.findByLabelText("Message"), "Hi{Enter}");
    expect(await screen.findByText(/No platform data was queried/)).toBeInTheDocument();
  });
});
