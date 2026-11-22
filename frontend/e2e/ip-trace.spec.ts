import { test, expect, type Page } from "@playwright/test";

/**
 * IP 詳情的「實體線路」（交接包 physical-v1.2 CABLING.md 畫面第 5 點，2026-11-21）：
 *   1. IP 只掛在裝置上 → 從那台裝置的埠挑一個，按追線一路走到交換器
 *      （MAC 相同的網卡、FDB 交換器埠兩種候選由 tests/test_ip_trace_candidates.py 涵蓋）
 *   2. 沒有任何關聯的 IP → 「尚未對應裝置或連接埠」
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

test("IP 詳情：MAC 相同的網卡追到交換器；沒有關聯時明講", async ({ page }) => {
  const u = Date.now().toString(36);
  const n = 100 + Math.floor(Math.random() * 150);
  await login(page);
  const made: [string, string][] = [];
  try {
    const sections = (await api(page, "GET", "/api/v1/sections?page_size=1")).items;
    const sub = await api(page, "POST", "/api/v1/subnets", { cidr: `10.251.${n}.0/24`, section_id: sections[0].id });
    const srv = await api(page, "POST", "/api/v1/devices", { name: `e2e-iptr-srv-${u}`, type: "server" });
    const sw = await api(page, "POST", "/api/v1/devices", { name: `e2e-iptr-sw-${u}`, type: "switch" });
    const nic = await api(page, "POST", "/api/v1/device-ports", { device_id: srv.id, name: "eno1" });
    const gi = await api(page, "POST", "/api/v1/device-ports", { device_id: sw.id, name: "Gi1/0/9" });
    const c = await api(page, "POST", "/api/v1/cables/connect", { a_port_id: nic.id, b_port_id: gi.id, label: `IPTR-${u}` });
    made.push(["cables", c.id], ["devices", srv.id], ["devices", sw.id], ["subnets", sub.id]);
    // 網卡的 MAC 是 LibreNMS 匯入的欄位、API 寫不進去：這裡走「只有裝置關聯」的路（MAC 那條由後端測試涵蓋）
    const ip = await api(page, "POST", "/api/v1/addresses", { subnet_id: sub.id, ip: `10.251.${n}.20`, device_id: srv.id });
    const lone = await api(page, "POST", "/api/v1/addresses", { subnet_id: sub.id, ip: `10.251.${n}.21` });

    await page.goto(`/addresses/${ip.id}`);
    const card = page.getByTestId("ip-cable-trace");
    await expect(card).toContainText(`e2e-iptr-srv-${u} 的哪一個埠`, { timeout: 20_000 });
    await card.getByTestId("ip-trace-port").click();
    await page.locator(".n-base-select-option", { hasText: "eno1" }).click();
    await card.getByRole("button", { name: "端到端追線" }).click();
    await expect(card.getByTestId("trace-node").last()).toContainText(`e2e-iptr-sw-${u}`, { timeout: 15_000 });
    await expect(card.getByTestId("trace-summary")).toContainText("1 條纜線");
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "test-results/ip-trace.png", fullPage: true });

    await page.goto(`/addresses/${lone.id}`);
    await expect(page.getByTestId("ip-trace-none")).toContainText("尚未對應裝置或連接埠", { timeout: 20_000 });
  } finally {
    for (const [kind, id] of made) await call(page, "DELETE", `/api/v1/${kind}/${id}`);
  }
});
