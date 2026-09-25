import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import ChangePassword from "./ChangePassword";

const fill = async (current: string, next: string, again: string) => {
  await userEvent.type(screen.getByLabelText(/Temporary password|Current password/), current);
  await userEvent.type(screen.getByLabelText("New password"), next);
  await userEvent.type(screen.getByLabelText("New password again"), again);
};

describe("password change", () => {
  it("will not submit until both entries match and are long enough", async () => {
    renderPage(<ChangePassword forced onDone={vi.fn()} />, { path: "/p", route: "/p" });
    const submit = screen.getByRole("button", { name: /Set password/ });
    expect(submit).toBeDisabled();

    await fill("temp1234", "short", "short");
    expect(submit).toBeDisabled();

    await userEvent.clear(screen.getByLabelText("New password"));
    await userEvent.clear(screen.getByLabelText("New password again"));
    await userEvent.type(screen.getByLabelText("New password"), "a quiet loading bay");
    await userEvent.type(screen.getByLabelText("New password again"), "a different thing");
    expect(screen.getByText("The two do not match.")).toBeInTheDocument();
    expect(submit).toBeDisabled();
  });

  it("hands the fresh token back so the session survives the change", async () => {
    const done = vi.fn();
    server.use(
      http.post("/api/v1/auth/password", () =>
        HttpResponse.json({ access_token: "new.token.value" }),
      ),
    );
    renderPage(<ChangePassword forced onDone={done} />, { path: "/p", route: "/p" });

    await fill("temp1234abcd", "a quiet loading bay", "a quiet loading bay");
    await userEvent.click(screen.getByRole("button", { name: /Set password/ }));

    expect(done).toHaveBeenCalledWith("new.token.value");
  });

  it("explains why the API refused", async () => {
    server.use(
      http.post("/api/v1/auth/password", () =>
        HttpResponse.json({ detail: "the current password is not correct" }, { status: 409 }),
      ),
    );
    renderPage(<ChangePassword forced onDone={vi.fn()} />, { path: "/p", route: "/p" });

    await fill("wrongpassword", "a quiet loading bay", "a quiet loading bay");
    await userEvent.click(screen.getByRole("button", { name: /Set password/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/current password is not correct/);
  });

  it("tells the person their other sessions will end", async () => {
    renderPage(<ChangePassword forced onDone={vi.fn()} />, { path: "/p", route: "/p" });
    expect(screen.getByText(/signs out your other sessions/)).toBeInTheDocument();
  });
});
