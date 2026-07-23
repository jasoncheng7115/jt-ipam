import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";

test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS env 才能跑");

// 主登入按鈕用 exact 對齊「登入」，避免抓到 SSO 按鈕（用 OIDC 單一登入 / 用 SAML 單一登入）
async function doLogin(page: Page, user: string, pass: string) {
  await page.getByPlaceholder(/帳號|Username/).fill(user);
  await page.getByPlaceholder(/密碼|Password/).fill(pass);
  await page.getByRole("button", { name: "登入", exact: true }).click();
}

test.describe("auth flow", () => {
  test("登入後離開 /login", async ({ page }) => {
    await page.goto("/login");
    await expect(page).toHaveURL(/\/login/);
    await doLogin(page, ADMIN_USER, ADMIN_PASS);
    await expect(page).not.toHaveURL(/\/login/, { timeout: 10_000 });
    await expect(page.locator("body")).toContainText(/admin/i);
  });

  test("登入頁 SSO 按鈕只在該供應商啟用時顯示（未設定 → 不出現）", async ({ page }) => {
    // v0.4.182 起：SSO 按鈕依 /auth/realms 的 sso.{oidc,saml} 條件顯示，避免點到未設定的供應商跳原始錯誤。
    // 此測試實例未設定 SSO，兩顆按鈕都不該出現。
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "登入", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /OIDC/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /SAML/ })).toHaveCount(0);
  });

  test("錯密碼回錯誤訊息", async ({ page }) => {
    await page.goto("/login");
    await doLogin(page, ADMIN_USER, "wrong-password-zzz");
    await expect(page.locator("body")).toContainText(/失敗|failed|invalid/i, {
      timeout: 5_000,
    });
  });
});
