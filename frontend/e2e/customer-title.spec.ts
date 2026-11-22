import { test, expect, type Page } from "@playwright/test";

/**
 * 單位的「顯示名稱」：表單以前沒有輸入框（資料有、存檔也送，畫面卻改不到），列表也沒有這一欄。
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.setTimeout(90_000);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("單位表單可以填顯示名稱，列表與詳細頁都看得到", async ({ page }) => {
  const name = `e2e-cust-${Date.now().toString(36)}`;
  const title = `${name} 股份有限公司`;
  await login(page);
  await page.goto("/customers");
  await page.getByRole("button", { name: /新增/ }).first().click();
  const modal = page.locator(".n-modal");
  await modal.locator(".n-form-item", { hasText: "名稱" }).first().locator("input").fill(name);
  await modal.getByTestId("customer-form-title").locator("input").fill(title);
  await page.screenshot({ path: "test-results/customer-form-title.png" });
  await modal.getByRole("button", { name: /儲存|建立/ }).click();
  await expect(modal).toHaveCount(0, { timeout: 10_000 });
  await page.getByPlaceholder("搜尋名稱").fill(name);
  await page.getByPlaceholder("搜尋名稱").press("Enter");
  const row = page.locator(".n-data-table-tbody tr", { hasText: name });
  await expect(row).toContainText(title, { timeout: 10_000 });
  const id = await page.evaluate(async (n) => {
    const r = await fetch(`/api/v1/customers?page_size=500&q=${n}`, { headers: { Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return (await r.json()).items.find((c: any) => c.name === n)?.id;
  }, name);
  expect(id).toBeTruthy();
  await page.goto(`/customers/${id}`);
  await expect(page.locator("body")).toContainText(title, { timeout: 10_000 });
  await page.evaluate(async (cid) => {
    await fetch(`/api/v1/customers/${cid}`, { method: "DELETE", headers: { Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
  }, id);
});
