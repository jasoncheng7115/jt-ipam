import { test, expect, type Page } from "@playwright/test";

/**
 * 表單欄位缺口（2026-11-21 掃描）：
 *   1. 裝置的製造商清空後存檔，以前舊值還在（送的是 undefined，後端當作沒改）
 *   2. 機櫃的「預設呈現面」以前存了卻沒有輸入框，示意圖也不照它顯示
 *   3. 連接埠的「排序」以前存了卻沒有輸入框
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.setTimeout(150_000);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function api(page: Page, method: string, path: string, body?: unknown) {
  return (await page.evaluate(async ({ method, path, body }) => {
    const res = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: res.status, json: res.status === 204 ? null : await res.json().catch(() => null) };
  }, { method, path, body })).json;
}

test("清空裝置製造商、機櫃預設呈現面、連接埠排序", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const dev = await api(page, "POST", "/api/v1/devices", { name: `e2e-gap-${u}`, type: "server", vendor: "ACME" });
  const rack = await api(page, "POST", "/api/v1/racks", { name: `e2e-gap-${u}` });
  try {
    // 1. 裝置頁編輯：清空製造商
    await page.goto(`/devices/${dev.id}`);
    await page.getByRole("button", { name: "編輯" }).first().click({ timeout: 20_000 });
    const modal = page.locator(".n-modal");
    await modal.locator(".n-form-item", { hasText: "製造商" }).locator("input").fill("");
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    expect((await api(page, "GET", `/api/v1/devices/${dev.id}`)).vendor).toBeNull();

    // 3. 連接埠：排序
    await page.getByRole("button", { name: "新增連接埠" }).click();
    await modal.locator(".n-form-item", { hasText: "名稱" }).first().locator("input").fill("eth9");
    await modal.getByTestId("port-position").locator("input").fill("7");
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    const ports = await api(page, "GET", `/api/v1/device-ports?device_id=${dev.id}`);
    expect(ports.find((p: any) => p.name === "eth9")?.position).toBe(7);

    // 2. 機櫃：預設呈現面設成背面，示意圖打開時就是背面
    await page.goto(`/racks?rack=${rack.id}`);
    await page.getByPlaceholder("篩選").first().fill(`e2e-gap-${u}`);
    const row = page.locator(".n-data-table-tbody tr", { hasText: `e2e-gap-${u}` });
    await row.locator("td").last().locator("button").nth(1).click();
    await modal.getByTestId("rack-default-face").getByText("背面").click();
    await page.screenshot({ path: "test-results/rack-default-face.png" });
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    const racks = (await api(page, "GET", "/api/v1/racks?page_size=500")).items;
    expect(racks.find((r: any) => r.id === rack.id)?.face).toBe("rear");
    await page.reload();
    await expect(page.locator(".n-button--primary-type", { hasText: /^背面$/ }).first()).toBeVisible({ timeout: 20_000 });
  } finally {
    await api(page, "DELETE", `/api/v1/devices/${dev.id}`);
    await api(page, "DELETE", `/api/v1/racks/${rack.id}`);
  }
});
