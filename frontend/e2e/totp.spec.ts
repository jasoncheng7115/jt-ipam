import { test, expect, type Page } from "@playwright/test";
import crypto from "node:crypto";

// issue: 啟用 TOTP 後「安全」頁沒有顯示已啟用。此測試走完整瀏覽器流程驗證狀態顯示。
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";

test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS env 才能跑");

// RFC 6238 TOTP（SHA1 / 6 碼 / 30s）—— 與後端 pyotp 預設一致
function base32Decode(s: string): Buffer {
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  const out: number[] = [];
  for (const c of s.replace(/=+$/, "").toUpperCase()) {
    const v = A.indexOf(c);
    if (v < 0) continue;
    bits += v.toString(2).padStart(5, "0");
  }
  for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(out);
}
function totp(secret: string, atMs = Date.now()): string {
  const key = base32Decode(secret);
  let counter = Math.floor(atMs / 1000 / 30);
  const buf = Buffer.alloc(8);
  for (let i = 7; i >= 0; i--) { buf[i] = counter & 0xff; counter = Math.floor(counter / 256); }
  const h = crypto.createHmac("sha1", key).update(buf).digest();
  const o = h[h.length - 1] & 0xf;
  const code = ((h[o] & 0x7f) << 24) | ((h[o + 1] & 0xff) << 16) | ((h[o + 2] & 0xff) << 8) | (h[o + 3] & 0xff);
  return (code % 1_000_000).toString().padStart(6, "0");
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function openSecurityTab(page: Page) {
  await page.goto("/settings");
  // Naive UI 的分頁不是 role=tab，用 .n-tabs-tab class 定位
  await page.locator(".n-tabs-tab", { hasText: "安全" }).click();
}

test.describe("TOTP 狀態顯示（issue 回報）", () => {
  test("啟用→顯示已啟用→重載仍在→停用→回未啟用", async ({ page }) => {
    await login(page);
    await openSecurityTab(page);

    // 初始：未啟用，只有「啟用 TOTP」
    await expect(page.getByText("未啟用", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "啟用 TOTP" })).toBeVisible();
    await expect(page.getByRole("button", { name: "停用 TOTP" })).toHaveCount(0);

    // 點啟用 → 攔截 enroll 回應取得 secret
    const [resp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/v1/auth/totp/enroll") && r.request().method() === "POST"),
      page.getByRole("button", { name: "啟用 TOTP" }).click(),
    ]);
    const { secret } = await resp.json();
    expect(secret).toBeTruthy();

    // 避開 30s 邊界（剩不到 3s 就等下一個窗口，免得驗證碼過期）
    const rem = 30 - (Math.floor(Date.now() / 1000) % 30);
    if (rem < 3) await page.waitForTimeout(3500);

    await page.getByPlaceholder("123456").fill(totp(secret));
    await page.getByRole("button", { name: "確認啟用" }).click();

    // 已啟用：狀態變「已啟用」、按鈕變「停用 TOTP」
    await expect(page.getByText("已啟用", { exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "停用 TOTP" })).toBeVisible();
    await expect(page.getByRole("button", { name: "啟用 TOTP" })).toHaveCount(0);

    // 重載後仍顯示已啟用（狀態來自 /me、非只是前端暫存）
    await page.reload();
    await openSecurityTab(page);
    await expect(page.getByText("已啟用", { exact: true })).toBeVisible({ timeout: 10_000 });

    // 停用 → popconfirm 確認 → 回未啟用
    await page.getByRole("button", { name: "停用 TOTP" }).click();
    await page.getByRole("button", { name: /確定|確認|Confirm|是/ }).click();
    await expect(page.getByText("未啟用", { exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "啟用 TOTP" })).toBeVisible();
  });
});
