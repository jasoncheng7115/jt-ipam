/**
 * Checkmk 整合頁：新增（網址少了網站名稱會被擋）、測試連線、同步、主機清單、未監控的位址、刪除。
 *
 * 要真的 Checkmk：`E2E_CHECKMK_URL`（例如 http://<容器 IP>:5000/cmk，scripts/checkmk-test-target/run.sh 起的），
 * 帳號是那支腳本建的 guest 角色自動化帳號；後端要把那個位址放進 OUTBOUND_ALLOW_CIDRS。沒設就略過。
 */
import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
const CMK_URL = process.env.E2E_CHECKMK_URL || "";
test.skip(!ADMIN_PASS || !CMK_URL, "需要 E2E_ADMIN_PASS 與 E2E_CHECKMK_URL");
test.setTimeout(180_000);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("新增、測試、同步、主機清單、缺口、刪除（真的 Checkmk）", async ({ page }) => {
  const name = `e2e-cmk-${Date.now().toString(36)}`;
  await login(page);
  await page.goto("/checkmk");
  await expect(page.locator(".n-card-header")).toContainText("整合 Checkmk", { timeout: 20_000 });
  await expect(page.locator(".n-menu").getByText("Checkmk", { exact: true })).toBeAttached();

  // 網址只有主機、沒有網站名稱 → 存檔就被擋，說明要填網站名稱
  await page.getByTestId("cmk-create").click();
  await page.getByTestId("cmk-name").locator("input").fill(name);
  await page.getByTestId("cmk-url").locator("input").fill(CMK_URL.replace(/\/[^/]+$/, ""));
  await page.getByTestId("cmk-user").locator("input").fill("jt-ipam-ro");
  await page.getByTestId("cmk-secret").locator("input").fill("RoSecret-2026-abcdef");
  await page.getByTestId("cmk-save").click();
  await expect(page.locator(".n-message")).toContainText("網址要包含網站名稱");

  await page.getByTestId("cmk-url").locator("input").fill(`${CMK_URL}/check_mk/index.py`);
  await page.getByTestId("cmk-save").click();
  const row = page.getByTestId("cmk-table").locator("tr", { hasText: name });
  await expect(row).toBeVisible();
  await expect(row).toContainText("check_mk/index.py");     // 存的是使用者貼的網址；API 路徑由後端補
  await expect(row).toContainText("jt-ipam-ro");

  await row.getByTestId("cmk-test").click();
  const health = page.getByTestId("cmk-health");
  await expect(health).toContainText("可讀取主機清單，共 5 台", { timeout: 20_000 });
  await expect(health).toContainText(/2\.[45]\.0/);
  await page.screenshot({ path: "test-results/checkmk-test.png" });
  await page.keyboard.press("Escape");

  await row.getByTestId("cmk-sync").click();
  await expect(page.getByTestId("cmk-table").locator("tr", { hasText: name }).getByTestId("cmk-sync"))
    .toBeEnabled({ timeout: 60_000 });
  await page.waitForTimeout(1000);
  await page.getByTestId("cmk-table").locator("tr", { hasText: name }).getByTestId("cmk-hosts").click();
  const hosts = page.getByTestId("cmk-hosts-table");
  await expect(hosts.locator("tr", { hasText: "web01" })).toContainText("UP", { timeout: 20_000 });
  await expect(hosts.locator("tr", { hasText: "web01" })).toContainText("/dc");
  await expect(hosts.locator("tr", { hasText: "sw-core" })).toContainText("192.0.2.1, 192.0.2.254");
  await expect(hosts.locator("tr", { hasText: "db01" })).toContainText("DOWN");
  await page.screenshot({ path: "test-results/checkmk-hosts.png" });
  await page.keyboard.press("Escape");

  await page.getByTestId("cmk-table").locator("tr", { hasText: name }).getByTestId("cmk-gap").click();
  await expect(page.locator(".n-modal").last()).toContainText("沒有被這台 Checkmk 監控");
  await page.keyboard.press("Escape");

  await page.getByTestId("cmk-table").locator("tr", { hasText: name }).getByTestId("cmk-delete").click();
  await page.mouse.move(5, 5);                                // 刪除鈕的提示框會蓋住確認鈕
  await page.locator(".n-popconfirm").getByRole("button", { name: /確定|確認|OK/ }).click();
  await expect(page.getByTestId("cmk-table").locator("tr", { hasText: name })).toHaveCount(0);
});
