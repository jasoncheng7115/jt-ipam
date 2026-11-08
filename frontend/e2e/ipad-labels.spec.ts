/**
 * iPad 橫向寬度（2026-11-07 使用者截圖）：
 * 1. 子網路詳細的欄位標題被擠成一字一行（「CIDR」「閘道」「說明」）→ 標題不折字，讓內容欄換行
 * 2. IP 詳細「各來源最後出現」的來源名稱溢出、壓到時間欄 → 只在括號前換行，不溢出
 */
import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.use({ viewport: { width: 1000, height: 700 }, hasTouch: true });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("子網路詳細：欄位標題不被擠成一字一行", async ({ page }) => {
  await login(page);
  await page.goto("/subnets");
  await page.getByText("10.20.0.0/24", { exact: true }).first().click();
  const headers = page.locator(".n-descriptions-table-header");
  await expect(headers.first()).toBeVisible({ timeout: 15_000 });
  const rows = await headers.evaluateAll((els) => els.map((e) => {
    const lh = parseFloat(getComputedStyle(e).lineHeight) || 22;
    const r = document.createRange(); r.selectNodeContents(e);
    return { text: e.textContent?.trim(), lines: Math.round(r.getBoundingClientRect().height / lh) };
  }));
  expect(rows.length).toBeGreaterThan(5);
  for (const r of rows) expect(r.lines, `「${r.text}」折成 ${r.lines} 行`).toBeLessThanOrEqual(1);
  // 這個寬度改成兩欄：CIDR 不會被拆成兩行
  const cidr = page.locator(".n-descriptions-table-content", { hasText: "10.20.0.0/24" }).first();
  const h = await cidr.evaluate((e) => { const r = document.createRange(); r.selectNodeContents(e); return r.getBoundingClientRect().height; });
  expect(h).toBeLessThan(30);
  expect(await page.locator(".n-descriptions-table-row").first().locator(".n-descriptions-table-header").count()).toBe(2);
  await page.locator(".n-descriptions").first().screenshot({ path: "test-results/ipad-subnet-desc.png" });
});

test("IP 詳細：各來源最後出現的來源名稱不溢出", async ({ page }) => {
  await login(page);
  await page.goto("/addresses?q=10.20.0.40");
  await page.locator("tr", { hasText: "10.20.0.40" }).first().getByText("10.20.0.40").first().click();
  const sec = page.getByTestId("ip-seen-section");
  await expect(sec).toBeVisible({ timeout: 15_000 });
  const over = await sec.locator("tbody tr td:first-child").evaluateAll((tds) => tds
    .filter((td) => td.scrollWidth > td.clientWidth + 1).map((td) => td.textContent));
  expect(over).toEqual([]);
  await sec.screenshot({ path: "test-results/ipad-seen.png" });
});
