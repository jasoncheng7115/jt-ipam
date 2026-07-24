import { test, expect, type Page } from "@playwright/test";

// 客戶回報：虛擬化 → 叢集手動新增的無法刪除。此測試走完整瀏覽器流程：新增 → 刪除 → 消失。
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";

test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS env 才能跑");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test.describe("虛擬化 → 叢集 新增/刪除（issue 回報）", () => {
  test("手動新增叢集後可以刪除", async ({ page }) => {
    await login(page);
    await page.goto("/virt");

    const name = `e2e-cluster-${Date.now()}`;

    // 新增叢集
    await page.getByRole("button", { name: "新增叢集" }).click();
    const dialog = page.locator(".n-modal");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("textbox").first().fill(name);
    await dialog.getByRole("button", { name: "儲存" }).click();

    // 出現在列表
    const row = page.locator(".n-data-table-tr", { hasText: name });
    await expect(row).toBeVisible({ timeout: 10_000 });

    // 刪除：點該列的紅色刪除鈕 → popconfirm 的正向按鈕（action 區最後一顆）
    await row.locator("button.n-button--error-type").click();
    const confirmBtn = page.locator(".n-popconfirm__action button").last();
    await expect(confirmBtn).toBeVisible();
    await page.mouse.move(0, 0);           // 移開滑鼠，收掉刪除鈕的 tooltip（會蓋住 popconfirm）
    await confirmBtn.click({ force: true });

    // 從列表消失
    await expect(page.locator(".n-data-table-tr", { hasText: name })).toHaveCount(0, { timeout: 10_000 });
  });
});
