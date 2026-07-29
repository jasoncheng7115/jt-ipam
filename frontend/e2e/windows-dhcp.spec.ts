import { test, expect, type Page } from "@playwright/test";

// Windows DHCP Server 整合（Beta）：新增 → 測試連線（預期失敗但要有可讀錯誤）→ 刪除。
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

test.describe("Windows DHCP Server（Beta）", () => {
  test("新增 → 測試連線回可讀錯誤 → 刪除", async ({ page }) => {
    await login(page);
    await page.goto("/windows-dhcp");

    // 頁面有 Beta 標記
    await expect(page.getByText("Beta", { exact: true })).toBeVisible();

    const name = `e2e-wdhcp-${Date.now()}`;
    await page.getByRole("button", { name: "新增" }).first().click();
    const dialog = page.locator(".n-modal");
    await expect(dialog).toBeVisible();

    const boxes = dialog.getByRole("textbox");
    await boxes.nth(0).fill(name);                    // 名稱
    await boxes.nth(1).fill("192.0.2.240");           // 主機（TEST-NET，必連不到）
    await boxes.nth(2).fill("CORP\\svc-e2e");         // 帳號
    await dialog.locator('input[type="password"]').fill("dummy-pass");
    await dialog.getByRole("button", { name: "儲存" }).click();

    const row = page.locator(".n-data-table-tr", { hasText: name });
    await expect(row).toBeVisible({ timeout: 10_000 });

    // 測試連線：連不到必須回可讀錯誤訊息（而不是整頁爆掉）
    await row.locator("button").nth(1).click();
    await expect(page.locator(".n-message")).toBeVisible({ timeout: 20_000 });

    // 刪除
    await row.locator("button.n-button--error-type").click();
    const confirmBtn = page.locator(".n-popconfirm__action button").last();
    await expect(confirmBtn).toBeVisible();
    await page.mouse.move(0, 0);            // 收掉 tooltip，避免蓋住 popconfirm
    await confirmBtn.click({ force: true });
    await expect(page.locator(".n-data-table-tr", { hasText: name })).toHaveCount(0, { timeout: 10_000 });
  });
});
