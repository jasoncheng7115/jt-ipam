/**
 * 「IP 位址」總表的未納管提示（客戶 2026-11-11：新站台開了掃描，這頁卻是空的，不知道要去哪裡開）。
 * 掃描代理預設不自動收錄（未授權 IP 偵測），掃到的只記成「未納管」。這裡說出數量、在哪幾個子網路、開關在哪；可以關掉。
 */
import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("IP 位址頁：說出掃到幾個未納管、在哪些子網路、開關在哪；關掉後不再出現", async ({ page }) => {
  await page.route("**/api/v1/addresses/unmanaged-summary", (r) => r.fulfill({ json: {
    count: 137, subnets: [{ subnet_id: "00000000-0000-0000-0000-0000000000a1", cidr: "198.51.100.0/24", count: 113 },
                          { subnet_id: "00000000-0000-0000-0000-0000000000a2", cidr: "203.0.113.0/24", count: 24 }],
    agents_total: 1, agents_auto_create: 0 } }));
  await login(page);
  await page.evaluate(() => localStorage.removeItem("unmanaged_hint_dismissed"));
  await page.goto("/addresses");
  const hint = page.getByTestId("unmanaged-hint");
  await expect(hint).toContainText("137 個還沒登錄的位址");
  await expect(hint).toContainText("自動收錄未登錄的 IP");
  await expect(hint).toContainText("198.51.100.0/24（113 個）");
  await expect(page.getByTestId("unmanaged-hint-agents")).toBeVisible();
  await hint.screenshot({ path: "test-results/unmanaged-hint.png" });
  await page.getByTestId("unmanaged-hint-agents").click();
  await expect(page).toHaveURL(/\/scan-agents/);

  await page.goto("/addresses");
  await page.getByTestId("unmanaged-hint").locator(".n-alert__close").click();
  await expect(page.getByTestId("unmanaged-hint")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".n-data-table")).toBeVisible();
  await expect(page.getByTestId("unmanaged-hint")).toHaveCount(0);
  await page.evaluate(() => localStorage.removeItem("unmanaged_hint_dismissed"));
});
