import { test, expect, type Page } from "@playwright/test";

/**
 * 規劃電力（GitHub issue #54；交接包 physical-v1.2 重做）：
 *   1. 裝置頁「電源」：兩顆 800 W 的 PSU，填整機規劃負載 500 W、按「平均分攤」（先問）→
 *      預覽 A 250 / B 250（不是兩顆 800 W 加起來）；存檔
 *   2. 機櫃頁電力卡片：已知規劃負載 500 W、資料完整度 1 / 2 台（另一台沒填）、資料不完整提示；
 *      迴路表本機櫃分配 250 W；「未填寫」篩選
 *   （舊值確認、使用率「至少」、分攤未知等計算由 tests/test_power_planning.py 涵蓋）
 *   3. 電力頁：迴路表單填規劃容量；沒填容量時顯示「未設定規劃容量」，不用電壓 × 電流
 *   4. 刪除還在用的插座被擋下
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

test("規劃電力：裝置負載與分攤、機櫃卡片、迴路容量", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const rack = await api(page, "POST", "/api/v1/racks", { name: `e2e-pw-${u}` });
  const panel = await api(page, "POST", "/api/v1/power-panels", { name: `e2e-pp-${u}` });
  const devices: string[] = [], outlets: string[] = [], feeds: string[] = [];
  try {
    const fa = await api(page, "POST", "/api/v1/power-feeds", { panel_id: panel.id, name: "A", rack_id: rack.id, planning_capacity_w: 3000 });
    const fb = await api(page, "POST", "/api/v1/power-feeds", { panel_id: panel.id, name: "B", rack_id: rack.id });
    feeds.push(fa.id, fb.id);
    const oa = await api(page, "POST", "/api/v1/power-outlets", { feed_id: fa.id, rack_id: rack.id, label: `A-${u}` });
    const ob = await api(page, "POST", "/api/v1/power-outlets", { feed_id: fb.id, rack_id: rack.id, label: `B-${u}` });
    outlets.push(oa.id, ob.id);
    const srv = await api(page, "POST", "/api/v1/devices", { name: `e2e-srv-${u}`, type: "server", rack_id: rack.id });
    const sw = await api(page, "POST", "/api/v1/devices", { name: `e2e-sw-${u}`, type: "switch", rack_id: rack.id });
    devices.push(srv.id, sw.id);
    for (const [n, o] of [["PSU1", oa.id], ["PSU2", ob.id]]) {
      await api(page, "POST", "/api/v1/device-power-ports", { device_id: srv.id, name: n, outlet_id: o, max_watts: 800 });
    }

    // 1. 裝置頁：填負載 500、平均分攤、存檔
    await page.goto(`/devices/${srv.id}`);
    const card = page.getByTestId("device-power");
    await card.scrollIntoViewIfNeeded();
    await card.getByTestId("power-load").locator("input").fill("500");
    await card.getByTestId("power-even").click();
    const dlg = page.locator(".n-dialog");
    await expect(dlg).toContainText("規劃假設");
    await dlg.getByRole("button", { name: "套用" }).click();
    await expect(card.getByTestId("power-preview-feed")).toHaveCount(2);
    await expect(card.getByTestId("power-preview-feed").first()).toContainText("250 W");
    await expect(card.getByTestId("power-share-total")).toContainText("100% · 500 W");
    await card.getByTestId("power-save").click();
    await expect(page.locator(".n-message")).toContainText("已儲存", { timeout: 10_000 });
    await page.screenshot({ path: "test-results/device-power.png", fullPage: true });
    const prof = await api(page, "GET", `/api/v1/devices/${srv.id}/power-profile`);
    expect(prof.planning_input_w).toBe(500);
    expect(prof.ports.map((p: any) => p.normal_share_pct)).toEqual([50, 50]);

    // 2. 機櫃電力卡片
    await page.goto(`/racks?rack=${rack.id}`);
    const rc = page.getByTestId("rack-power-card");
    await expect(rc.getByTestId("rack-power-known")).toContainText("500 W", { timeout: 20_000 });
    await expect(rc.getByTestId("rack-power-completeness")).toContainText("1 / 2 台");
    await expect(rc.getByTestId("rack-power-partial")).toBeVisible();
    const feedRow = rc.getByTestId("rack-power-feeds").locator("tr", { hasText: `e2e-pp-${u} / A` });
    await expect(feedRow).toContainText("250 W");
    await expect(feedRow).toContainText("3,000 W");
    await expect(rc.getByTestId("rack-power-feeds").locator("tr", { hasText: `e2e-pp-${u} / B` })).toContainText("未設定規劃容量");
    await rc.getByTestId("rack-power-only-unknown").click();
    await expect(rc.getByTestId("rack-power-devices")).toContainText(`e2e-sw-${u}`);
    await expect(rc.getByTestId("rack-power-devices")).not.toContainText(`e2e-srv-${u}`);
    await rc.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "test-results/rack-power-card.png", fullPage: true });

    // 3. 電力頁：迴路表單有規劃容量；B 沒設
    await page.goto("/power");
    await page.locator(".n-tabs-tab", { hasText: "供電迴路" }).click();
    const rowB = page.locator(".n-data-table-tbody tr", { hasText: `e2e-pp-${u}` }).filter({ hasText: /^B/ }).first();
    await expect(rowB).toContainText("未設定規劃容量", { timeout: 15_000 });
    await rowB.locator("td").last().locator("button").first().click();
    const modal = page.locator(".n-modal");
    await modal.getByTestId("feed-capacity").locator("input").fill("2000");
    await page.screenshot({ path: "test-results/power-feed-form.png" });
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(rowB).toContainText("250 W / 2,000 W（12.5%）", { timeout: 10_000 });
    // 沒填負載的那台沒接任何迴路：B 的資料是完整的，可以說「低於警戒」
    await expect(rowB).toContainText("低於警戒");

    // 4. 刪掉還接著電源的插座 → 擋下
    const del = await call(page, "DELETE", `/api/v1/power-outlets/${oa.id}`);
    expect(del.status).toBe(409);
  } finally {
    for (const id of devices) await call(page, "DELETE", `/api/v1/devices/${id}`);
    for (const id of outlets) await call(page, "DELETE", `/api/v1/power-outlets/${id}`);
    for (const id of feeds) await call(page, "DELETE", `/api/v1/power-feeds/${id}`);
    await call(page, "DELETE", `/api/v1/power-panels/${panel.id}`);
    await call(page, "DELETE", `/api/v1/racks/${rack.id}`);
  }
});
