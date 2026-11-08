/**
 * iPad Safari（2026-11-07）：剛載入時版面比視窗矮，下面一大片空白；切到別的 app 再回來才正常。
 * 版面高度改用實際視窗高度（--app-height），視窗變了就跟著變。
 */
import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.use({ viewport: { width: 1180, height: 820 }, hasTouch: true });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

const rootBottom = (page: Page) => page.evaluate(() => {
  const r = document.querySelector(".app-root")!.getBoundingClientRect();
  return { bottom: Math.round(r.bottom), inner: window.innerHeight };
});

test("版面填滿視窗高度，視窗變高也跟著填滿（不留下方空白）", async ({ page }) => {
  await login(page);
  await page.goto("/");
  await page.locator(".app-root").first().waitFor();
  let m = await rootBottom(page);
  expect(m.bottom).toBe(m.inner);
  await page.setViewportSize({ width: 1180, height: 960 });
  await expect.poll(async () => { m = await rootBottom(page); return m.bottom; }).toBe(960);
  await page.screenshot({ path: "test-results/ipad-viewport.png" });
});
