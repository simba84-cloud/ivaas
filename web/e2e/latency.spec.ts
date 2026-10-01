import { expect, test } from "@playwright/test";
import { demoBay, edgeCounts, signIn, tokenOf } from "./support";

/** T6.2: a chokepoint crossing is on screen within 3 s of the edge node reporting it. */
const BUDGET_MS = 3_000;
const CROSSINGS = 10;

declare global {
  interface Window {
    __shown?: Record<number, number>;
  }
}

test("a crossing reaches the live bay on screen within 3 seconds", async ({ page, request }) => {
  await signIn(page, "latency");
  const bay = await demoBay(request, await tokenOf(page));
  // timed from a live connection: a page still connecting is a different measure
  await expect(page.getByTitle("Receiving live events")).toBeVisible();

  const live = page.getByRole("region", { name: "Live bay" });
  const start = live.getByRole("button", { name: "Start loading" });
  const end = live.getByRole("button", { name: "End session" });
  await expect(start.or(end)).toBeVisible();
  if (await end.isVisible()) await end.click();
  await start.click();
  await expect(live.getByRole("status", { name: "Crates counted: 0" })).toBeVisible();

  // the moment each count is painted, stamped in the page itself (same wall clock as
  // this process, so the gap is the whole trip: edge POST, API, bus, socket, refetch)
  await page.evaluate(() => {
    window.__shown = {};
    const stamp = () => {
      const el = document.querySelector('[role="status"][aria-label^="Crates counted: "]');
      const n = Number(el?.getAttribute("aria-label")?.split(": ")[1]);
      if (Number.isFinite(n) && !(n in window.__shown!)) window.__shown![n] = Date.now();
    };
    new MutationObserver(stamp).observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["aria-label"],
    });
  });

  const took: number[] = [];
  for (let n = 1; n <= CROSSINGS; n++) {
    const sent = Date.now();
    await edgeCounts(request, bay, 1, n);
    await expect(live.getByRole("status", { name: `Crates counted: ${n}` })).toBeVisible({
      timeout: 10_000,
    });
    const shown = await page.evaluate((k) => window.__shown?.[k], n);
    expect(shown, `count ${n} was stamped when painted`).toBeTruthy();
    took.push(shown! - sent);
    // the first one over budget fails the test, with its figure, before any more are sent
    const ms = took[n - 1];
    expect(ms, `crossing ${n} took ${ms} ms to reach the screen`).toBeLessThanOrEqual(BUDGET_MS);
  }
  await end.click();

  const sorted = [...took].sort((a, b) => a - b);
  const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1)];
  const summary =
    `p50 ${at(0.5)} ms, p95 ${at(0.95)} ms, max ${sorted.at(-1)} ms ` +
    `over ${CROSSINGS} crossings`;
  test.info().annotations.push({ type: "crossing to screen", description: summary });
  console.log(`T6.2 crossing to screen: ${summary}`);
});
