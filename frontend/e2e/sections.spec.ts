import { test, expect } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";

test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS env 才能跑");

test.beforeEach(async ({ page }) => {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 10_000 });
});

test.describe("section / subnet 主路徑", () => {
  test("可以開到 /sections 頁面", async ({ page }) => {
    await page.goto("/sections");
    // n-data-table 一定會渲染（即使無資料）
    await expect(page.locator(".n-data-table")).toBeVisible({ timeout: 10_000 });
  });

  test("admin 可以看到 sidebar 的「管理」分組", async ({ page }) => {
    await page.goto("/");
    // n-menu 展開或子項
    await expect(page.getByText(/管理|Admin/)).toBeVisible({ timeout: 10_000 });
  });

  test("可以開 /audit 並按驗證鏈按鈕", async ({ page }) => {
    await page.goto("/audit");
    const verifyBtn = page.getByRole("button", { name: /驗證|verify/i });
    await expect(verifyBtn).toBeVisible({ timeout: 10_000 });
    await verifyBtn.click();
    // 應該看到「鏈完整」或「ok」訊息（n-message 為浮動）
    await expect(page.locator("body")).toContainText(/完整|intact|ok/i, {
      timeout: 5_000,
    });
  });
});
