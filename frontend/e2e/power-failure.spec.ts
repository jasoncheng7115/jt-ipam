import { test, expect, type Page } from "@playwright/test";

/**
 * 迴路失效模擬（交接包 physical-v1.2 SPEC.md 第 6 節、P3）：
 *   1. 機櫃電力卡迴路 A 的「失效模擬」→ 抽屜：單電源那台「失去供電」、確認雙路那台「剩餘容量不足」
 *      （B 規劃容量 600 W，接手後 500＋只接 B 的 200 超過）、沒確認的「無法判斷」；存活迴路 B 失效前 / 後
 *   2. 電力頁迴路分頁的「失效模擬」按鈕：一次選 A、B 兩路 → 四台都失去供電、沒有存活迴路
 *   （各種結果的規則由 tests/test_power_failure_preview.py 涵蓋）
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

async function dev(page: Page, name: string, rack: string, load: number, ports: [string, string, number | null][],
                   failover: string) {
  const d = await api(page, "POST", "/api/v1/devices", { name, type: "server", rack_id: rack });
  const ids: string[] = [];
  for (const [n, o] of ports) ids.push((await api(page, "POST", "/api/v1/device-power-ports", { device_id: d.id, name: n, outlet_id: o })).id);
  const prof = await api(page, "GET", `/api/v1/devices/${d.id}/power-profile`);
  await api(page, "PUT", `/api/v1/devices/${d.id}/power-profile`, {
    revision: prof.revision, planning_input_w: load, supply_mode: ports.length > 1 ? "shared" : "single", failover_capability: failover,
    ports: ids.map((id, i) => ({ id, normal_share_pct: ports[i][2] })) });
  return d.id as string;
}

test("迴路失效模擬：機櫃卡與電力頁", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const rack = await api(page, "POST", "/api/v1/racks", { name: `e2e-pf-${u}` });
  const panel = await api(page, "POST", "/api/v1/power-panels", { name: `e2e-pfp-${u}` });
  const devices: string[] = [], outlets: string[] = [], feeds: string[] = [];
  try {
    const fa = await api(page, "POST", "/api/v1/power-feeds", { panel_id: panel.id, name: "A", rack_id: rack.id, planning_capacity_w: 3000 });
    const fb = await api(page, "POST", "/api/v1/power-feeds", { panel_id: panel.id, name: "B", rack_id: rack.id, planning_capacity_w: 600 });
    feeds.push(fa.id, fb.id);
    const o: Record<string, string> = {};
    for (const [f, tag] of [[fa, "A"], [fb, "B"]] as const) {
      for (let i = 1; i <= 3; i++) {
        const r = await api(page, "POST", "/api/v1/power-outlets", { feed_id: f.id, rack_id: rack.id, label: `${tag}${i}-${u}` });
        o[`${tag}${i}`] = r.id; outlets.push(r.id);
      }
    }
    devices.push(await dev(page, `e2e-single-${u}`, rack.id, 200, [["PSU1", o.A1, null]], "unknown"));
    devices.push(await dev(page, `e2e-dual-${u}`, rack.id, 500, [["PSU1", o.A2, 50], ["PSU2", o.B2, 50]], "confirmed_full_load"));
    devices.push(await dev(page, `e2e-unconf-${u}`, rack.id, 300, [["PSU1", o.A3, 50], ["PSU2", o.B3, 50]], "unknown"));
    devices.push(await dev(page, `e2e-bonly-${u}`, rack.id, 200, [["PSU1", o.B1, null]], "unknown"));

    // 1. 機櫃電力卡 → 迴路 A 失效
    await page.goto(`/racks?rack=${rack.id}`);
    const rc = page.getByTestId("rack-power-card");
    await expect(rc.getByTestId("rack-power-feeds")).toBeVisible({ timeout: 20_000 });
    await rc.getByTestId("rack-power-feeds").locator("tr", { hasText: `e2e-pfp-${u} / A` }).getByRole("button").click();
    const dr = page.getByTestId("power-failure-drawer");
    await expect(dr.getByTestId("pf-count-loses_power")).toContainText("1", { timeout: 15_000 });
    await expect(dr.getByTestId("pf-count-insufficient_capacity")).toContainText("1");
    await expect(dr.getByTestId("pf-count-undetermined")).toContainText("1");
    await expect(dr.getByTestId("pf-lost")).toContainText("200 W");
    const devs = dr.getByTestId("power-failure-devices");
    await expect(devs.locator("tr", { hasText: `e2e-single-${u}` })).toContainText("失去供電");
    await expect(devs.locator("tr", { hasText: `e2e-dual-${u}` })).toContainText("剩餘容量不足");
    await expect(devs.locator("tr", { hasText: `e2e-unconf-${u}` })).toContainText("沒確認單顆電源能不能撐整機");
    const surv = dr.getByTestId("power-failure-survivors").locator("tr", { hasText: `e2e-pfp-${u} / B` });
    // B 失效前 250＋150＋200＝600；失效後 dual 整台 500 移過來＋200、沒確認的那台不算 → 至少 700，超過 600
    await expect(surv).toContainText("600 W");
    await expect(surv).toContainText("至少 700 W");
    await expect(surv).toContainText("超過容量");
    await expect(dr).toContainText("不代表上游 UPS / 配電盤彼此獨立");
    await page.screenshot({ path: "test-results/power-failure-rack.png" });
    await page.keyboard.press("Escape");

    // 2. 電力頁 → A、B 一起失效
    await page.goto("/power");
    await page.locator(".n-tabs-tab", { hasText: "供電迴路" }).click();
    await page.getByTestId("power-failure-open").click();
    const sel = dr.getByTestId("power-failure-feeds");
    for (const n of ["A", "B"]) {
      await sel.click();
      await sel.locator("input").fill(`e2e-pfp-${u} / ${n}`);
      await page.locator(".n-base-select-option", { hasText: `e2e-pfp-${u} / ${n}` }).click();
    }
    await dr.getByTestId("power-failure-run").click();
    await expect(dr.getByTestId("pf-count-loses_power")).toContainText("4", { timeout: 15_000 });
    await expect(dr.getByTestId("pf-count-undetermined")).toContainText("0");
    await expect(dr.getByTestId("pf-lost")).toContainText("1,200 W");
    await expect(dr).toContainText("受影響的裝置沒有接在其他迴路上");
    await page.screenshot({ path: "test-results/power-failure-both.png" });
  } finally {
    for (const id of devices) await call(page, "DELETE", `/api/v1/devices/${id}`);
    for (const id of outlets) await call(page, "DELETE", `/api/v1/power-outlets/${id}`);
    for (const id of feeds) await call(page, "DELETE", `/api/v1/power-feeds/${id}`);
    await call(page, "DELETE", `/api/v1/power-panels/${panel.id}`);
    await call(page, "DELETE", `/api/v1/racks/${rack.id}`);
  }
});
