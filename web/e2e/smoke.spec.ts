import { expect, test } from "@playwright/test";

/**
 * Runs against a deployed preview with a storage state captured by a signed-in
 * browser (see README). It proves the pages render real rows, not that the
 * numbers are right - the Python view tests own the numbers.
 */
const PAGES = [
  { path: "/", heading: "Players" },
  { path: "/squad", heading: "Squad & lineup" },
  { path: "/market", heading: "Market" },
  { path: "/health", heading: "Calibration & health" },
];

for (const page of PAGES) {
  test(`${page.heading} renders`, async ({ page: browser }) => {
    await browser.goto(page.path);
    await expect(browser.getByRole("heading", { name: page.heading })).toBeVisible();
    await expect(browser.locator("table tbody tr").first()).toBeVisible();
  });
}

test("an anonymous visitor is sent to the login page", async ({ browser: b }) => {
  const anonymous = await b.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await anonymous.newPage();
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await anonymous.close();
});
