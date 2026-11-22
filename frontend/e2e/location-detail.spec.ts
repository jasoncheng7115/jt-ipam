import { test, expect, type Page } from "@playwright/test";

/**
 * 機房詳情（交接包 physical-v1.2 設計圖 03、07，規格 R01–R04、R06，2026-11-21）：
 *   1. 機房清單點名稱進詳情（R01）；「編輯」按鈕回清單並直接開編輯視窗
 *   2. 總覽：機櫃 2、裝置 3（含未入櫃 1）、關聯 IP 1；待處理有「未入櫃」「位置歸屬待確認」
 *   3. 裝置與 IP：位置衝突那台說明「機櫃在這個機房；統計算在這裡」
 *   4. 佈線：房內 1、跨機房 1
 *   5. 清單的裝置數跟詳情一致（R03）
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.setTimeout(180_000);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}
async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const res = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: res.status, json: res.status === 204 ? null : await res.json().catch(() => null) };
  }, { method, path, body });
}
async function api(page: Page, method: string, path: string, body?: unknown) {
  const r = await call(page, method, path, body);
  expect(r.status, `${method} ${path} ${JSON.stringify(r.json)}`).toBeLessThan(300);
  return r.json;
}

test("機房詳情：總覽、裝置與 IP、佈線、編輯入口", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const made: [string, string][] = [];
  try {
    const l1 = await api(page, "POST", "/api/v1/locations", { name: `e2e-tyo-${u}` });
    const l2 = await api(page, "POST", "/api/v1/locations", { name: `e2e-osa-${u}` });
    const r1 = await api(page, "POST", "/api/v1/racks", { name: `e2e-r1-${u}`, location_id: l1.id });
    const r2 = await api(page, "POST", "/api/v1/racks", { name: `e2e-r2-${u}`, location_id: l1.id });
    const r3 = await api(page, "POST", "/api/v1/racks", { name: `e2e-r3-${u}`, location_id: l2.id });
    const app = await api(page, "POST", "/api/v1/devices", { name: `e2e-app-${u}`, type: "server", rack_id: r1.id });
    const moved = await api(page, "POST", "/api/v1/devices", { name: `e2e-moved-${u}`, type: "server", rack_id: r2.id });
    // 「moved」的地點記成大阪，但機櫃在東京 → 位置衝突，算在東京
    await api(page, "PATCH", `/api/v1/devices/${moved.id}`, { location_id: l2.id });
    const desk = await api(page, "POST", "/api/v1/devices", { name: `e2e-desk-${u}`, type: "workstation", location_id: l1.id });
    const osa = await api(page, "POST", "/api/v1/devices", { name: `e2e-osa-dev-${u}`, type: "server", rack_id: r3.id });
    for (const d of [app, moved, desk, osa]) made.push(["devices", d.id]);
    made.push(["racks", r1.id], ["racks", r2.id], ["racks", r3.id], ["locations", l1.id], ["locations", l2.id]);
    const port = async (dev: string, name: string) => (await api(page, "POST", "/api/v1/device-ports", { device_id: dev, name })).id;
    const pa1 = await port(app.id, "eth1"), pa2 = await port(app.id, "eth2");
    const pm = await port(moved.id, "eth1"), po = await port(osa.id, "eth1");
    const c1 = await api(page, "POST", "/api/v1/cables/connect", { a_port_id: pa1, b_port_id: pm, label: `in-${u}` });
    const c2 = await api(page, "POST", "/api/v1/cables/connect", { a_port_id: pa2, b_port_id: po, label: `x-${u}` });
    made.unshift(["cables", c1.id], ["cables", c2.id]);

    // 0. 選單：機房、機櫃、佈線、電力排在一起（不在「進階」裡）
    await page.goto("/locations");
    const headers = page.locator(".n-menu-item-content-header");
    await expect(headers.filter({ hasText: /^機櫃$/ }).first()).toBeVisible({ timeout: 15_000 });
    const items = await headers.allInnerTexts();
    const top = items.map((x) => x.trim());
    const i = top.indexOf("機櫃");
    expect(top.slice(i, i + 4)).toEqual(["機櫃", "機房 / 地點", "佈線", "電力"]);

    // 5. 清單的裝置數（有效位置）＋ 1. 點名稱進詳情
    const row = page.locator(".n-data-table-tbody tr", { hasText: `e2e-tyo-${u}` });
    await expect(row).toContainText("3", { timeout: 15_000 });
    await row.getByText(`e2e-tyo-${u}`).click();
    await expect(page).toHaveURL(new RegExp(`/locations/${l1.id}`));

    // 2. 總覽
    await expect(page.getByTestId("ld-racks")).toContainText("2", { timeout: 15_000 });
    await expect(page.getByTestId("ld-devices")).toContainText("3");
    await expect(page.getByTestId("ld-devices")).toContainText("未入櫃 1 台");
    await expect(page.getByTestId("ld-pending-unracked")).toBeVisible();
    await expect(page.getByTestId("ld-pending-location_conflicts")).toContainText("1 筆位置歸屬待確認");
    await expect(page.getByTestId("ld-rack-table")).toContainText(`e2e-r1-${u}`);
    await page.screenshot({ path: "test-results/location-overview.png", fullPage: true });

    // 3. 從待處理點進位置衝突
    await page.getByTestId("ld-pending-location_conflicts").click();
    const dt = page.getByTestId("ld-dev-table");
    await expect(dt).toContainText(`e2e-moved-${u}`, { timeout: 10_000 });
    await expect(dt).toContainText("機櫃在這個機房；統計算在這裡");
    await expect(dt).not.toContainText(`e2e-app-${u}`);
    await page.screenshot({ path: "test-results/location-devices.png", fullPage: true });

    // 3b. 點標題排序（後端排序，跨頁也對）。Naive UI 第一下是由大到小、第二下由小到大
    await page.locator("[data-testid=ld-dev-filter] label", { hasText: "全部" }).click();
    await expect(dt).toContainText(`e2e-app-${u}`, { timeout: 10_000 });
    const nameTh = dt.locator("th", { hasText: "裝置" }).first();
    await nameTh.click();
    await expect(dt.locator("tbody tr").first()).toContainText(`e2e-moved-${u}`, { timeout: 10_000 });
    await expect(dt.locator("tbody tr").last()).toContainText(`e2e-app-${u}`);
    await nameTh.click();
    await expect(dt.locator("tbody tr").first()).toContainText(`e2e-app-${u}`, { timeout: 10_000 });

    // 4. 佈線
    await page.locator(".n-tabs-tab", { hasText: "佈線" }).click();
    await expect(page.getByTestId("ld-cab-total")).toContainText("2", { timeout: 10_000 });
    await expect(page.getByTestId("ld-cab-in")).toContainText("1");
    await expect(page.getByTestId("ld-cab-cross")).toContainText("1");
    await expect(page.getByTestId("ld-cab-table")).toContainText(`e2e-osa-${u}`);
    // 兩端照全站寫法「裝置@埠」，@ 是品牌綠、裝置名稱是連結
    const crossRow = page.getByTestId("ld-cab-table").locator("tr", { hasText: `x-${u}` });
    await expect(crossRow).toContainText(`e2e-app-${u}@eth2`);
    await expect(crossRow.locator(".swp-at").first()).toHaveCSS("color", "rgb(24, 160, 88)");
    await expect(crossRow.locator("a.swp-dev", { hasText: `e2e-app-${u}` })).toBeVisible();
    // 匯出按鈕
    await expect(page.locator("button:visible", { hasText: "匯出" }).first()).toBeVisible();
    await page.screenshot({ path: "test-results/location-cabling.png", fullPage: true });

    // 1. 編輯入口：回清單並開編輯視窗
    await page.getByTestId("location-edit").click();
    await expect(page).toHaveURL(/\/locations$/);
    await expect(page.locator(".n-modal")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator(".n-modal input").first()).toHaveValue(`e2e-tyo-${u}`);
  } finally {
    for (const [kind, id] of made) await call(page, "DELETE", `/api/v1/${kind}/${id}`);
  }
});
