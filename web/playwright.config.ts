import { defineConfig, devices } from "@playwright/test";
import { API_PORT, API_URL, EDGE_KEY, USERS, WEB_PORT } from "./e2e/support";

/**
 * End to end (proposal T6.1, T6.7): the real portal against the real API.
 *
 * The API runs in memory (no Postgres, NATS or MediaMTX), on its own port so it never
 * meets a compose stack on 8000, with its own accounts: a site manager for each
 * browser and one for the tablet checks, each still on a temporary password, and an
 * edge key the tests post crossings with, as a node would.
 */
export default defineConfig({
  testDir: "./e2e",
  // one API, one bay: the journeys take turns at it
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    // the demo site is in Harare; a supervisor's browser is too
    timezoneId: "Africa/Harare",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop",
      testIgnore: /tablet\.spec/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // what supervisors carry on the floor (T6.7); Chromium, so CI needs one browser
      name: "tablet",
      use: { ...devices["Desktop Chrome"], viewport: { width: 820, height: 1180 }, hasTouch: true },
    },
  ],
  webServer: [
    {
      name: "api",
      cwd: "../services/api",
      command: `uv run uvicorn ivaas.adapters.http.app:app --port ${API_PORT}`,
      url: `${API_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        IVAAS_STORAGE: "memory",
        IVAAS_EVENTS: "memory",
        IVAAS_AUTH_MODE: "local",
        IVAAS_LOCAL_USERS: JSON.stringify(
          Object.fromEntries(USERS.map((u) => [u, [`${u}-temporary`, "site_manager"]])),
        ),
        IVAAS_SERVICE_API_KEYS: JSON.stringify({ [EDGE_KEY]: "e2e-edge" }),
      },
    },
    {
      name: "web",
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: false,
      env: { IVAAS_API_URL: API_URL },
    },
  ],
});
