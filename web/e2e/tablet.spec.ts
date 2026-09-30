import { expect, test } from "@playwright/test";
import { signIn } from "./support";

/**
 * T6.7: usable at tablet width, which is what supervisors carry on the floor. Every
 * page the site manager's own navigation offers opens with its heading in view and
 * nothing pushed off the side of the screen.
 */
test("every page a site manager can open fits a tablet", async ({ page }) => {
  await signIn(page, "floor");

  // below desktop width the side rail gives way to a strip across the top
  const strip = page.getByRole("navigation").filter({ has: page.getByRole("link", { name: "Daily Reports" }) });
  await expect(strip).toBeVisible();
  // a link's name carries its badge ("Alerts 3"), so pages are followed by address
  const pages = await strip.getByRole("link").evaluateAll((links) =>
    links.map((a) => ({ name: a.textContent?.trim() ?? "", href: a.getAttribute("href") ?? "" })),
  );
  expect(pages.length).toBeGreaterThan(5);

  const overflowing: string[] = [];
  for (const { name, href } of pages) {
    await strip.locator(`a[href="${href}"]`).click();
    await expect(page).toHaveURL(new RegExp(`${href === "/" ? "/$" : href}`));
    await expect(page.getByRole("heading", { level: 1 })).toBeInViewport();
    const [scroll, width] = await page.evaluate(() => [
      document.documentElement.scrollWidth,
      document.documentElement.clientWidth,
    ]);
    if (scroll > width) overflowing.push(`${name} (${href}): ${scroll}px wide in ${width}px`);
  }
  expect(overflowing, "pages wider than the tablet").toEqual([]);
});
