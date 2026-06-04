import { test, expect } from "@playwright/test";

/**
 * 免登入 headless 冒煙測試 —— 不需後端、不需密碼，可在 CI 直接對 `vite preview` 跑。
 * 驗證前端 build 出來的 bundle 真的能在瀏覽器啟動、路由導到 /login、登入頁正常渲染、
 * 且沒有未捕捉的 JS 例外（網路請求失敗的 console error 不算，因為冒煙沒有後端）。
 */
test.describe("smoke (no backend)", () => {
  test("app boots → 導到 /login 且登入表單渲染正常", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (e) => pageErrors.push(String(e)));

    await page.goto("/");
    // 未登入一律導到 /login
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });

    // 帳號 / 密碼欄位 + 登入鈕
    await expect(page.getByPlaceholder(/帳號|Username/)).toBeVisible();
    await expect(page.getByPlaceholder(/密碼|Password/)).toBeVisible();
    await expect(page.getByRole("button", { name: "登入", exact: true })).toBeVisible();

    // SSO 按鈕不論後端是否啟用都該顯示
    await expect(page.getByRole("button", { name: /OIDC/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /SAML/ })).toBeVisible();

    // 不該有未捕捉的 JS 例外（bundle 壞掉 / i18n key crash 等會在這裡爆出來）
    expect(pageErrors, `uncaught JS errors:\n${pageErrors.join("\n")}`).toEqual([]);
  });
});
