import { test, expect, type Page } from "@playwright/test";

/**
 * 無線網路頁：以前只有 SSID 表格，點對點的無線連線沒有任何畫面；SSID 表單也只能填名稱與說明。
 *   1. 無線連線頁籤：新增（兩端裝置用遠端搜尋）、表格顯示裝置名稱與距離、編輯、刪除
 *   2. SSID 表單可以填認證方式，表格顯示
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.setTimeout(120_000);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function api(page: Page, method: string, path: string, body?: unknown) {
  const r = await page.evaluate(async ({ method, path, body }) => {
    const res = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: res.status, json: res.status === 204 ? null : await res.json().catch(() => null) };
  }, { method, path, body });
  return r;
}

async function pickRemote(page: Page, testId: string, text: string) {
  const sel = page.locator(".n-modal").getByTestId(testId);
  await sel.click();
  await sel.locator("input").fill(text);
  await page.locator(".n-base-select-option", { hasText: text }).first().click({ timeout: 10_000 });
}

test("無線連線：新增、表格、編輯、刪除；SSID 認證方式", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const a = (await api(page, "POST", "/api/v1/devices", { name: `e2e-ap-a-${u}`, type: "ap" })).json;
  const b = (await api(page, "POST", "/api/v1/devices", { name: `e2e-ap-b-${u}`, type: "ap" })).json;
  let ssidId: string | null = null;
  try {
    await page.goto("/advanced/wireless");
    await page.locator(".n-tabs-tab", { hasText: "無線連線" }).click();
    await page.getByTestId("wlink-create").click();
    const modal = page.locator(".n-modal");
    await modal.getByTestId("wlink-name").locator("input").fill(`e2e-bridge-${u}`);
    await pickRemote(page, "wlink-a", `e2e-ap-a-${u}`);
    await pickRemote(page, "wlink-b", `e2e-ap-b-${u}`);
    await modal.getByTestId("wlink-distance").locator("input").fill("850");
    await page.screenshot({ path: "test-results/wlink-form.png" });
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    const row = page.getByTestId("wlink-table").locator("tr", { hasText: `e2e-bridge-${u}` });
    await expect(row).toContainText(`e2e-ap-a-${u}`, { timeout: 10_000 });
    await expect(row).toContainText(`e2e-ap-b-${u}`);
    await expect(row).toContainText("850 m");
    await page.screenshot({ path: "test-results/wlink-table.png" });

    // 編輯：距離改掉，兩端裝置要帶回來（不在第一頁也要顯示名稱）
    await row.locator("td").last().locator("button").first().click();
    await expect(modal.getByTestId("wlink-a")).toContainText(`e2e-ap-a-${u}`);
    await modal.getByTestId("wlink-distance").locator("input").fill("900");
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(row).toContainText("900 m", { timeout: 10_000 });

    // SSID：認證方式
    await page.locator(".n-tabs-tab", { hasText: /^SSID/ }).click();
    await page.getByRole("button", { name: /新增/ }).first().click();
    await modal.locator(".n-form-item", { hasText: "SSID" }).first().locator("input").fill(`e2e-ssid-${u}`);
    await modal.getByTestId("ssid-auth").click();
    await page.locator(".n-base-select-option", { hasText: "wpa3-personal" }).click();
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    await expect(page.getByTestId("ssid-table").locator("tr", { hasText: `e2e-ssid-${u}` })).toContainText("wpa3-personal");
    const ssids = (await api(page, "GET", "/api/v1/wireless/ssids?page_size=500")).json.items;
    ssidId = ssids.find((s: any) => s.ssid === `e2e-ssid-${u}`)?.id ?? null;

    // 刪除無線連線
    await page.locator(".n-tabs-tab", { hasText: "無線連線" }).click();
    await row.locator("td").last().locator("button").last().click();
    await page.mouse.move(5, 5);                                   // 刪除鈕的提示框會蓋住確認鈕
    await page.locator(".n-popconfirm").getByRole("button", { name: /確定|確認|OK/ }).click();
    await expect(row).toHaveCount(0, { timeout: 10_000 });
  } finally {
    const links = (await api(page, "GET", "/api/v1/wireless/links?page_size=500")).json?.items ?? [];
    for (const l of links.filter((x: any) => x.name === `e2e-bridge-${u}`)) await api(page, "DELETE", `/api/v1/wireless/links/${l.id}`);
    if (ssidId) await api(page, "DELETE", `/api/v1/wireless/ssids/${ssidId}`);
    await api(page, "DELETE", `/api/v1/devices/${a.id}`);
    await api(page, "DELETE", `/api/v1/devices/${b.id}`);
  }
});
