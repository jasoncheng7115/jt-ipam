import { test, expect, type Page } from "@playwright/test";

/**
 * 佈線清單的追線與篩選（交接包 physical-v1.2 設計圖 04、05，CABLING.md 驗收案例，2026-11-21）：
 *   1. 篩選條件在網址上（可以分享）：?q=<線標> 打開就套用
 *   2. 「追線」側欄：sw Gi1/0/1 →A01-01→ 面板 F1 ↔ R1 →A01-02→ pve eno1，「2 條纜線 · 1 組面板內部對應」
 *   3. 從 B 端追；拿掉 F1↔R1 配對後停在 F1 並說明原因
 *   4. 「只看未填線標」與機櫃篩選
 *   5. 裝置頁的面板埠位：點 F1 標出 R1、兩邊線標、內部對應已登錄，可以追完整路徑
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

test("追線側欄、網址篩選、未配對停止原因", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const made: [string, string][] = [];
  try {
    const rack = await api(page, "POST", "/api/v1/racks", { name: `e2e-tr-${u}` });
    const sw = await api(page, "POST", "/api/v1/devices", { name: `e2e-sw-${u}`, type: "switch", rack_id: rack.id });
    const pp = await api(page, "POST", "/api/v1/devices", { name: `e2e-pp-${u}`, type: "patch_panel", rack_id: rack.id });
    const pve = await api(page, "POST", "/api/v1/devices", { name: `e2e-pve-${u}`, type: "server" });
    const port = async (dev: string, name: string, type = "network") =>
      (await api(page, "POST", "/api/v1/device-ports", { device_id: dev, name, type })).id;
    const g = await port(sw.id, "Gi1/0/1"), f1 = await port(pp.id, "F1", "front"), r1 = await port(pp.id, "R1", "rear");
    const eno = await port(pve.id, "eno1"), g2 = await port(sw.id, "Gi1/0/2"), eno2 = await port(pve.id, "eno2");
    await api(page, "PATCH", `/api/v1/device-ports/${f1}`, { peer_port_id: r1 });
    const c1 = await api(page, "POST", "/api/v1/cables/connect", { a_port_id: g, b_port_id: f1, label: `A01-01-${u}`, type: "cat6a" });
    const c2 = await api(page, "POST", "/api/v1/cables/connect", { a_port_id: r1, b_port_id: eno, label: `A01-02-${u}`, type: "cat6a" });
    const c3 = await api(page, "POST", "/api/v1/cables/connect", { a_port_id: g2, b_port_id: eno2 });   // 沒有線標
    made.push(["cables", c1.id], ["cables", c2.id], ["cables", c3.id], ["devices", sw.id], ["devices", pp.id],
              ["devices", pve.id], ["racks", rack.id]);

    // 1. 網址上的篩選
    await page.goto(`/cabling?q=A01-01-${u}`);
    const table = page.locator(".n-data-table-tbody");
    await expect(table.locator("tr")).toHaveCount(1, { timeout: 20_000 });

    // 2. 追線
    await page.getByTestId(`cable-trace-A01-01-${u}`).click();
    const drawer = page.getByTestId("cable-trace");
    await expect(drawer.getByTestId("trace-node")).toHaveCount(4, { timeout: 15_000 });
    await expect(drawer.getByTestId("trace-node").last()).toContainText(`e2e-pve-${u}`);
    await expect(drawer.getByTestId("trace-summary")).toContainText("2 條纜線 · 1 組面板內部對應");
    await expect(drawer.getByTestId("trace-seg-panel_passthrough")).toContainText("不計纜線");
    // 段落標籤要對準：第一段是 A01-01、面板對應在 F1 與 R1 之間、最後一段是 A01-02
    const segs = drawer.locator("[data-testid^='trace-seg-']");
    await expect(segs).toHaveCount(3);
    await expect(segs.nth(0)).toContainText(`A01-01-${u}`);
    await expect(segs.nth(1)).toContainText("面板內部對應");
    await expect(segs.nth(2)).toContainText(`A01-02-${u}`);
    await page.screenshot({ path: "test-results/cable-trace.png" });

    // 2b. 面板埠位（設計圖 06）：點 F1 → 對應 R1、兩邊線標、已登錄
    await page.keyboard.press("Escape");
    await page.goto(`/devices/${pp.id}`);
    await page.getByTestId("ports-view").getByText("面板埠位").click({ timeout: 20_000 });
    await page.getByTestId("pg-F1").click();
    const side = page.getByTestId("panel-grid-side");
    await expect(side).toContainText("F1 ↔ R1");
    await expect(side).toContainText(`A01-01-${u}`);
    await expect(side).toContainText(`A01-02-${u}`);
    await expect(side.getByTestId("pg-pair-state")).toContainText("已登錄");
    await page.screenshot({ path: "test-results/panel-grid.png", fullPage: true });
    await side.getByTestId("pg-trace").click();
    // 穿過面板的整條路：交換器 → F1 → R1 → 主機
    const chain = page.locator(".n-modal .trace-chain");
    await expect(chain).toContainText(`e2e-pve-${u}`, { timeout: 15_000 });
    await expect(chain).toContainText(`e2e-sw-${u}`);
    await page.screenshot({ path: "test-results/panel-full-trace.png" });
    await page.keyboard.press("Escape");
    await page.goto(`/cabling?q=A01-01-${u}`);
    await page.getByTestId(`cable-trace-A01-01-${u}`).click({ timeout: 20_000 });

    // 3. 從 B 端（面板 F1）出發：第一段仍是 A01-01，走回交換器
    await drawer.getByText("從 B 端追").click();
    await expect(drawer.getByTestId("trace-node")).toHaveCount(2, { timeout: 15_000 });
    await expect(drawer.getByTestId("trace-node").last()).toContainText(`e2e-sw-${u}`);
    // 拿掉 F1↔R1 配對 → 從 A 端追，停在 F1 並說明
    await api(page, "PATCH", `/api/v1/device-ports/${f1}`, { peer_port_id: null });
    await drawer.getByText("從 A 端追").click();
    await expect(page.getByTestId("trace-stop")).toContainText("F1 沒有登錄背面對應", { timeout: 15_000 });
    await page.keyboard.press("Escape");

    // 4. 只看未填線標＋機櫃
    await page.goto(`/cabling?nolabel=1&rack=${rack.id}`);
    await expect(table.locator("tr")).toHaveCount(1, { timeout: 20_000 });
    await expect(table).toContainText(`e2e-sw-${u}`);
    await expect(page.getByTestId("cable-filter-nolabel")).toHaveClass(/n-checkbox--checked/);
  } finally {
    for (const [kind, id] of made) await call(page, "DELETE", `/api/v1/${kind}/${id}`);
  }
});
