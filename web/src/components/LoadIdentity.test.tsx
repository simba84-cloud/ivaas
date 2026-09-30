import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { session } from "../test/fixtures";
import { server } from "../test/server";
import { AssignTruck, CorrectCount, CountCell, PlateCell } from "./LoadIdentity";

const S = session();

const wrap = (ui: ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

describe("load identity", () => {
  it("flags a load no plate was read for, and one not in the fleet", () => {
    const { rerender } = wrap(<PlateCell session={session({ plate: null, identification: "unidentified" })} />);
    expect(screen.getByText("Unidentified")).toBeInTheDocument();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <PlateCell session={session({ plate: "QQQ 1", identification: "unregistered" })} />
      </QueryClientProvider>,
    );
    expect(screen.getByText("Not in fleet")).toBeInTheDocument();
  });

  it("says nothing extra about a registered truck", () => {
    wrap(<PlateCell session={session({ plate: "ABC 1234", identification: "registered" })} />);
    expect(screen.queryByText("Unidentified")).not.toBeInTheDocument();
    expect(screen.queryByText("Not in fleet")).not.toBeInTheDocument();
  });

  it("shows a correction beside the AI count, which stays visible", () => {
    wrap(<CountCell session={session({ ai_count: 100, override_count: 96, override_by: "operator" })} />);
    expect(screen.getByText("100")).toHaveClass("line-through");
    expect(screen.getByText(/96/)).toBeInTheDocument();
    expect(screen.getByText("corrected")).toBeInTheDocument();
  });

  it("records a correction with its reason", async () => {
    const sent = vi.fn();
    server.use(
      http.post(`/api/v1/sessions/${S.id}/override`, async ({ request }) => {
        sent(await request.json());
        return HttpResponse.json(S);
      }),
    );
    wrap(<CorrectCount session={session({ ai_count: 100 })} />);
    const count = screen.getByLabelText("Corrected count");
    await userEvent.clear(count);
    await userEvent.type(count, "96");
    await userEvent.selectOptions(screen.getByLabelText("Reason"), "double_counted");
    await userEvent.click(screen.getByRole("button", { name: "Record correction" }));
    expect(sent).toHaveBeenCalledWith({ count: 96, reason: "double_counted" });
  });

  it("assigns a registered truck to a load", async () => {
    const sent = vi.fn();
    server.use(
      http.get("/api/v1/fleet", () =>
        HttpResponse.json([{ id: "v1", plate: "ABC 1234", fleet_number: "SL-01", operator: "", notes: "", active: true, created_at: null }]),
      ),
      http.post(`/api/v1/sessions/${S.id}/vehicle`, async ({ request }) => {
        sent(await request.json());
        return HttpResponse.json(S);
      }),
    );
    wrap(<AssignTruck session={session({ plate: null })} />);
    await userEvent.selectOptions(await screen.findByLabelText("Registered truck"), "v1");
    await userEvent.click(screen.getByRole("button", { name: "Set truck" }));
    expect(sent).toHaveBeenCalledWith({ vehicle_id: "v1" });
  });
});
