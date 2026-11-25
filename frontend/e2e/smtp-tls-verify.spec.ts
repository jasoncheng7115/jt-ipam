import { test, expect } from "@playwright/test";

/**
 * SMTP 憑證驗證（內部原始碼掃描；使用者 11-25：「但如果是自簽憑證呢」）：
 * 加密模式才出現「驗證伺服器憑證」；開著時可以貼自簽憑證或企業 CA，存檔後顯示「目前信任」；
 * 貼錯格式要講清楚；關掉驗證時 CA 欄位收起、改顯示風險說明。最後把設定還原。
 */
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

// 只有公開的憑證本體（沒有私鑰），CN=relay.e2e.test
const PEM = "-----BEGIN CERTIFICATE-----\nMIIBozCCAUigAwIBAgIUS4cApV8zjwH3K9LzsYfVfOqoQ58wCgYIKoZIzj0EAwIw\nGTEXMBUGA1UEAwwOcmVsYXkuZTJlLnRlc3QwHhcNMjYxMTI1MDEyMDIwWhcNMzYx\nMTIyMDEyMDIwWjAZMRcwFQYDVQQDDA5yZWxheS5lMmUudGVzdDBZMBMGByqGSM49\nAgEGCCqGSM49AwEHA0IABO6cBceXMhRbFK1lCLcbv2npWXRjEFcnU+lP8WJ+CP4H\noxqq62xy4WLyP2vk35owJTXkMECIWQrT8RgNefA2/qyjbjBsMB0GA1UdDgQWBBQV\ncuyKB3vDFvHtMUQnBKI+IPVt1zAfBgNVHSMEGDAWgBQVcuyKB3vDFvHtMUQnBKI+\nIPVt1zAPBgNVHRMBAf8EBTADAQH/MBkGA1UdEQQSMBCCDnJlbGF5LmUyZS50ZXN0\nMAoGCCqGSM49BAMCA0kAMEYCIQDWpjAyCXUN2zHHG2G4w5jpAW4ORjJqxQWRkY4V\nzss2XwIhANev6XblcpVyiCfqwsWdgPHRqx6CjeJMgfLpYCjYFuhm\n-----END CERTIFICATE-----\n";

test("自簽憑證的轉信主機：貼 CA 憑證照樣驗證；關閉驗證有風險說明", async ({ page }) => {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
  await page.goto("/notification-channels");

  const verify = page.getByTestId("smtp-verify-tls");
  await expect(verify).toBeVisible({ timeout: 15_000 });
  await expect(verify).toHaveClass(/n-switch--active/);
  const ca = page.getByTestId("smtp-ca-pem").locator("textarea");
  // 頁面上有好幾個「儲存」（通知矩陣也有）：取 Email 卡片自己的
  const save = page.locator(".n-card", { has: verify }).getByRole("button", { name: "儲存" });

  // SMTP Host 填 IP：提醒憑證多半比對不到（不存檔，只看提示）
  const host = page.getByTestId("smtp-host").locator("input");
  const prevHost = await host.inputValue();
  await host.fill("192.0.2.25");
  await expect(page.getByText(/用 IP 連線時，憑證通常比對不到/)).toBeVisible();
  await host.fill("mail.example.net");
  await expect(page.getByText(/用 IP 連線時，憑證通常比對不到/)).toHaveCount(0);
  await host.fill(prevHost);

  try {
    await ca.fill("not a certificate");
    await save.click();
    await expect(page.getByText(/CA 憑證不是有效的 PEM/)).toBeVisible();
    await expect(page.getByText(/cryptography\.io/)).toHaveCount(0);

    await ca.fill(PEM);
    await save.click();
    await expect(page.getByTestId("smtp-ca-current")).toContainText("CN=relay.e2e.test");

    // 關掉驗證：CA 欄位收起，出現風險說明
    await verify.click();
    await expect(page.getByTestId("smtp-ca-pem")).toHaveCount(0);
    await expect(page.getByText(/中間人可以攔截 SMTP 帳密/)).toBeVisible();
    await page.waitForTimeout(400);   // 等開關動畫結束再截圖
    await page.screenshot({ path: "test-results/smtp-tls-verify-off.png", fullPage: true });
    await verify.click();
    await expect(page.getByTestId("smtp-ca-current")).toBeVisible();
    await page.waitForTimeout(400);
    await page.screenshot({ path: "test-results/smtp-tls-verify-ca.png", fullPage: true });
  } finally {
    // 還原：驗證開著、不留自訂 CA（用畫面操作，存取權杖只在記憶體裡）
    if (!(await verify.getAttribute("class"))?.includes("n-switch--active")) await verify.click();
    await ca.fill("");
    await save.click();
    await expect(page.getByTestId("smtp-ca-current")).toHaveCount(0);
  }
});
