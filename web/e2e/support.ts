import { type APIRequestContext, type Page, expect } from "@playwright/test";

export const API_PORT = 8010;
export const WEB_PORT = 5180;
export const API_URL = `http://localhost:${API_PORT}`;
/** Posts crossings and plate reads the way an edge node does. */
export const EDGE_KEY = "e2e-edge-key";

export const USERS = ["manager-desktop", "manager-tablet", "floor"] as const;
export type User = (typeof USERS)[number];

const temporary = (user: User) => `${user}-temporary`;
const chosen = (user: User) => `${user} chosen password`;

/**
 * Sign in through the login page. Every account starts on a temporary password, and
 * the portal makes the person choose their own before anything else, so the first
 * sign-in goes through that too. A retried test finds the password already chosen.
 */
export async function signIn(page: Page, user: User): Promise<void> {
  await page.goto("/");
  const attempt = async (password: string) => {
    await page.getByLabel("Username").fill(user);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
  };
  await attempt(temporary(user));
  const forced = page.getByRole("button", { name: "Set password" });
  const refused = page.locator("form").getByRole("alert");
  const shell = page.getByRole("heading", { name: "Operations" });
  await expect(forced.or(refused).or(shell).first()).toBeVisible();
  if (await refused.isVisible()) {
    await attempt(chosen(user));
  } else if (await forced.isVisible()) {
    await page.getByLabel("Temporary password").fill(temporary(user));
    await page.getByLabel("New password", { exact: true }).fill(chosen(user));
    await page.getByLabel("New password again").fill(chosen(user));
    await forced.click();
  }
  await expect(page.getByRole("heading", { name: "Operations" })).toBeVisible();
}

/** The signed-in person's token, for reading ids the way the portal does. */
export async function tokenOf(page: Page): Promise<string> {
  const token = await page.evaluate(() => sessionStorage.getItem("ivaas.token"));
  expect(token).toBeTruthy();
  return token!;
}

export interface Bay {
  id: string;
  chokepoint: string;
  lpr: string;
}

export async function demoBay(request: APIRequestContext, token: string): Promise<Bay> {
  const auth = { Authorization: `Bearer ${token}` };
  const bays = await (await request.get(`${API_URL}/api/v1/bays`, { headers: auth })).json();
  const cams: { id: string; role: string }[] = await (
    await request.get(`${API_URL}/api/v1/bays/${bays[0].id}/cameras`, { headers: auth })
  ).json();
  return {
    id: bays[0].id,
    chokepoint: cams.find((c) => c.role === "chokepoint")!.id,
    lpr: cams.find((c) => c.role === "lpr")!.id,
  };
}

/** What the edge node sends while a truck is loaded: its plate, then stacks crossing. */
export async function edgeSees(
  request: APIRequestContext,
  bay: Bay,
  load: { plate: string; stacks: number[] },
): Promise<void> {
  const headers = { "X-IVaaS-Key": EDGE_KEY };
  const at = () => new Date().toISOString();
  const plate = await request.post(`${API_URL}/api/v1/ingest/plates`, {
    headers,
    data: { bay_id: bay.id, camera_id: bay.lpr, plate: load.plate, confidence: 0.93, read_at: at() },
  });
  expect(plate.ok()).toBeTruthy();
  for (const [i, crates] of load.stacks.entries()) {
    const crossing = await request.post(`${API_URL}/api/v1/ingest/crossings`, {
      headers,
      data: {
        bay_id: bay.id,
        camera_id: bay.chokepoint,
        track_id: i + 1,
        direction: "loading",
        crates,
        confidence: 0.9,
        crossed_at: at(),
      },
    });
    expect(crossing.ok()).toBeTruthy();
  }
}
