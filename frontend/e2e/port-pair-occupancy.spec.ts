import { test, expect, type Page } from "@playwright/test";

/**
 * 跳接面板配對、埠占用與資料檢查（交接包 physical-v1.2 第 1 項，2026-11-21）。
 *   1. 面板 F1 配 R1 → 改配 R2：R1 不再指向 F1
 *   2. R3 已配 F3，把 F1 改配 R3：跳出確認；確定後 F3 的配對清掉
 *   3. 連線視窗：選到已被別條線接走的埠 → 顯示「已經接了另一條纜線」，不會留下半條線
 *   4. 佈線頁「資料檢查」列出舊式插座直連
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

async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const res = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: res.status, json: res.status === 204 ? null : await res.json().catch(() => null) };
  }, { method, path, body });
}

async function pickOption(page: Page, testId: string, text: string) {
  await page.locator(".n-modal").getByTestId(testId).click();
  await page.locator(".n-base-select-option", { hasText: new RegExp(`^${text}`) }).first().click({ timeout: 10_000 });
}

test("面板改配對、確認重配、占用的埠、資料檢查", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const pp = (await api(page, "POST", "/api/v1/devices", { name: `e2e-pp-${u}`, type: "patch_panel" })).json;
  const sw = (await api(page, "POST", "/api/v1/devices", { name: `e2e-sw-${u}`, type: "switch" })).json;
  const port = async (device: string, name: string, type = "network") =>
    (await api(page, "POST", "/api/v1/device-ports", { device_id: device, name, type })).json;
  const P: Record<string, any> = {};
  for (const n of ["F1", "F3"]) P[n] = await port(pp.id, n, "front");
  for (const n of ["R1", "R2", "R3"]) P[n] = await port(pp.id, n, "rear");
  P.G1 = await port(sw.id, "Gi1/0/1");
  P.G2 = await port(sw.id, "Gi1/0/2");
  const peerOf = async (id: string) => (await api(page, "GET", `/api/v1/device-ports?device_id=${pp.id}`)).json
    .find((x: any) => x.id === id)?.peer_port_id ?? null;
  const cleanup: (() => Promise<unknown>)[] = [];
  try {
    expect((await api(page, "PATCH", `/api/v1/device-ports/${P.F1.id}`, { peer_port_id: P.R1.id })).status).toBe(200);
    expect((await api(page, "PATCH", `/api/v1/device-ports/${P.F3.id}`, { peer_port_id: P.R3.id })).status).toBe(200);

    // 1. 改配 R2
    await page.goto(`/devices/${pp.id}`);
    await page.getByTestId("port-edit-F1").click({ timeout: 20_000 });
    await pickOption(page, "port-peer", "R2");
    await page.getByTestId("port-save").click();
    await expect(page.locator(".n-modal")).toHaveCount(0, { timeout: 10_000 });
    expect(await peerOf(P.R2.id)).toBe(P.F1.id);
    expect(await peerOf(P.R1.id), "R1 不可以還指著 F1").toBeNull();

    // 2. 改配已被 F3 用掉的 R3：先問
    await page.getByTestId("port-edit-F1").click();
    await pickOption(page, "port-peer", "R3");
    await page.getByTestId("port-save").click();
    const dlg = page.locator(".n-dialog");
    await expect(dlg).toContainText("R3 已經跟 F3 配對", { timeout: 10_000 });
    await page.screenshot({ path: "test-results/port-peer-confirm.png" });
    await dlg.getByRole("button", { name: "重新配對" }).click();
    await expect(page.locator(".n-modal")).toHaveCount(0, { timeout: 10_000 });
    expect(await peerOf(P.R3.id)).toBe(P.F1.id);
    expect(await peerOf(P.F3.id)).toBeNull();
    expect(await peerOf(P.R2.id)).toBeNull();

    // 3. 連線：Gi1/0/1 已經接了 R1
    const c = await api(page, "POST", "/api/v1/cables/connect", { a_port_id: P.G1.id, b_port_id: P.R1.id, label: `e2e-${u}` });
    expect(c.status).toBe(201);
    cleanup.push(() => api(page, "DELETE", `/api/v1/cables/${c.json.id}`));
    const before = (await api(page, "GET", "/api/v1/cables?page_size=500")).json.total;
    await page.getByTestId("port-connect-R2").click();
    const modal = page.locator(".n-modal");
    await modal.getByTestId("connect-device").click();
    await modal.getByTestId("connect-device").locator("input").fill(`e2e-sw-${u}`);
    await page.locator(".n-base-select-option", { hasText: `e2e-sw-${u}` }).first().click({ timeout: 10_000 });
    await pickOption(page, "connect-port", "Gi1/0/1");
    await modal.getByTestId("connect-do").click();
    await expect(page.locator(".n-message", { hasText: "已經接了另一條纜線" })).toBeVisible({ timeout: 10_000 });
    expect((await api(page, "GET", "/api/v1/cables?page_size=500")).json.total, "不可以留下半條線").toBe(before);
    // 改接沒被占用的 Gi1/0/2 → 成功
    await pickOption(page, "connect-port", "Gi1/0/2");
    await modal.getByTestId("connect-do").click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    const cables = (await api(page, "GET", "/api/v1/cables?page_size=500")).json.items;
    for (const x of cables) if (x.a_device_id === pp.id || x.b_device_id === pp.id) cleanup.push(() => api(page, "DELETE", `/api/v1/cables/${x.id}`));

    // 4. 資料檢查：舊式插座直連
    const panel = (await api(page, "POST", "/api/v1/power-panels", { name: `e2e-pnl-${u}` })).json;
    cleanup.push(() => api(page, "DELETE", `/api/v1/power-panels/${panel.id}`));
    const outlet = (await api(page, "POST", "/api/v1/power-outlets", { label: `e2e-out-${u}`, device_id: sw.id })).json;
    cleanup.unshift(() => api(page, "DELETE", `/api/v1/power-outlets/${outlet.id}`));
    await page.goto("/cabling");
    await page.getByTestId("physical-check-open").click();
    const sec = page.getByTestId("pc-legacy_outlet_links");
    await expect(sec).toContainText(`e2e-out-${u}`, { timeout: 15_000 });
    await expect(sec).toContainText(`e2e-sw-${u}`);
    await page.screenshot({ path: "test-results/physical-check.png" });
  } finally {
    for (const f of cleanup) await f();
    for (const d of [pp, sw]) if (d?.id) await api(page, "DELETE", `/api/v1/devices/${d.id}`);
  }
});
