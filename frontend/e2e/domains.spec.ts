/**
 * 網域管理（客戶許願清單 2026-10-28）：新增（關掉立即查 RDAP，測試不連外）、手填到期日顯示剩幾天與「手填」、
 * 編輯 DNS 託管、詳細資料頁、刪除。RDAP 本身在後端測試用假的註冊管理機構測。
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function token(request: APIRequestContext): Promise<string> {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  expect(r.ok()).toBeTruthy();
  return (await r.json()).access_token;
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("網域：新增、手填到期日、編輯 DNS 託管、詳細資料、刪除", async ({ page, request }) => {
  const auth = { Authorization: `Bearer ${await token(request)}` };
  const tag = Date.now().toString(36);
  const name = `jt-e2e-${tag}.example.com`;
  try {
    await login(page);
    await page.goto("/advanced/domains");
    await page.getByTestId("domain-add").click();
    await page.getByTestId("domain-name").locator("input").fill(name.toUpperCase());
    const dlg = page.locator(".n-modal");
    // 到期日手填 100 天後；關掉「建立後立刻查 RDAP」
    const due = new Date(Date.now() + 100 * 86400_000);
    const dueText = `${due.getFullYear()}-${String(due.getMonth() + 1).padStart(2, "0")}-${String(due.getDate()).padStart(2, "0")}`;
    await dlg.locator(".n-date-picker input").fill(dueText);
    await dlg.locator(".n-date-picker input").press("Enter");
    await dlg.locator(".n-switch").last().click();
    await page.getByTestId("domain-save").click();
    const table = page.getByTestId("domain-table");
    const row = table.locator("tbody tr", { hasText: name });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText("手填");
    await expect(row).toContainText("剩 100 天");      // 用站台時區算，不會差一天

    // 詳細資料 → 編輯 DNS 託管
    await row.getByText(name).click();
    await expect(page.getByTestId("domain-title")).toHaveText(name);
    await page.getByTestId("domain-edit").click();
    await page.locator(".n-modal .n-form-item", { hasText: "DNS 託管" }).locator("input").fill("自架 BIND");
    await page.getByTestId("domain-save").click();
    await expect(page.locator(".n-descriptions")).toContainText("自架 BIND");
    await page.mouse.move(4, 4);
    await page.locator(".n-card").first().screenshot({ path: "test-results/domain-detail.png" });
  } finally {
    const list = await (await request.get("/api/v1/domains", { headers: auth, params: { q: tag } })).json();
    for (const d of list.items ?? []) await request.delete(`/api/v1/domains/${d.id}`, { headers: auth });
  }
});
