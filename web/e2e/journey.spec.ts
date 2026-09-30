import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { type User, demoBay, edgeSees, signIn, tokenOf } from "./support";

/**
 * T6.1: a site manager signs in, watches the live bay count a truck, ends the
 * session, and exports the day's report, which has that truck's load in it. Run on
 * a desktop and at tablet width, each as its own person with its own truck.
 */
test("a site manager watches a load, ends it, and exports the day's report", async ({
  page,
  request,
}, info) => {
  const user: User = info.project.name === "tablet" ? "manager-tablet" : "manager-desktop";
  const plate = info.project.name === "tablet" ? "E2E 8200" : "E2E 1024";

  await signIn(page, user);
  const bay = await demoBay(request, await tokenOf(page));

  const live = page.getByRole("region", { name: "Live bay" });
  const end = live.getByRole("button", { name: "End session" });
  const start = live.getByRole("button", { name: "Start loading" });
  // one bay for every run: a retry can find the load an earlier attempt left open
  await expect(start.or(end)).toBeVisible();
  if (await end.isVisible()) await end.click();

  // the live bay: open a load, as the operator does before the truck is read
  await start.click();
  await expect(end).toBeVisible();

  // the edge node reads the plate and counts two stacks; the portal hears it live
  await edgeSees(request, bay, { plate, stacks: [24, 16] });
  await expect(live.getByRole("status", { name: "Crates counted: 40" })).toBeVisible();
  await expect(live.getByText(plate, { exact: true })).toBeVisible();

  await end.click();
  await expect(start).toBeVisible();

  // the day's report, on demand, through the navigation a supervisor would use
  await page.getByRole("link", { name: "Daily Reports" }).click();
  await expect(page.getByRole("heading", { name: "Daily reports" })).toBeVisible();

  const csv = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV" }).click();
  const rows = (await readFile(await (await csv).path(), "utf8")).trim().split(/\r?\n/);
  expect(rows[0]).toMatch(/^date,site,opened,closed,plate,/);
  const row = rows.find((r) => r.includes(plate));
  expect(row, `the report has ${plate}'s load`).toBeTruthy();
  const cols = rows[0].split(",");
  const load = Object.fromEntries(row!.split(",").map((v, i) => [cols[i], v]));
  expect(load).toMatchObject({
    direction: "loading",
    ai_count: "40",
    count_of_record: "40",
    corrected_count: "",
    tally_count: "",
    status: "closed",
  });

  const pdf = page.waitForEvent("download");
  await page.getByRole("button", { name: "PDF" }).click();
  const bytes = await readFile(await (await pdf).path());
  expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
});
