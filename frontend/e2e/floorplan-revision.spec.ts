import { test, expect, type Page } from "@playwright/test";

/**
 * 平面配置的版本檢查與未儲存提示（交接包 physical-v1.2 第 10 節，2026-11-21）：
 *   1. 載入平面圖後，另一個人先存了配置（用 API 模擬）→ 這邊再存檔：跳出「剛被別人改過」、不蓋掉
 *   2. 調整了還沒存就離開頁面：跳出「還有沒儲存的配置」，選留下就不離開
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
// 1×1 PNG 當底圖
async function uploadPlan(page: Page, locId: string) {
  return page.evaluate(async (id) => {
    const b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const fd = new FormData();
    fd.append("file", new Blob([bin], { type: "image/png" }), "plan.png");
    const res = await fetch(`/api/v1/locations/${id}/floorplan`, { method: "POST", body: fd,
      headers: { Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return res.status;
  }, locId);
}

test("平面配置：別人剛存過不蓋掉、未儲存離開先問", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const loc = (await call(page, "POST", "/api/v1/locations", { name: `e2e-fp-${u}` })).json;
  const r1 = (await call(page, "POST", "/api/v1/racks", { name: `fp-a-${u}`, location_id: loc.id })).json;
  const r2 = (await call(page, "POST", "/api/v1/racks", { name: `fp-b-${u}`, location_id: loc.id })).json;
  try {
    expect(await uploadPlan(page, loc.id)).toBeLessThan(300);
    await page.goto(`/locations/${loc.id}`);
    await page.locator(".n-tabs-tab", { hasText: "平面配置" }).click();
    await page.getByTestId("fp-edit").click({ timeout: 15_000 });
    await page.getByTestId(`fp-tray-fp-a-${u}`).click();

    // 2. 還沒存就離開 → 先問；選留下
    await page.getByRole("menuitem", { name: "機櫃" }).first().click();
    const dlg = page.locator(".n-dialog");
    await expect(dlg).toContainText("還有沒儲存的配置", { timeout: 10_000 });
    await dlg.getByRole("button", { name: "留在這頁" }).click();
    await expect(page).toHaveURL(new RegExp(`/locations/${loc.id}`));

    // 1. 另一個人先存了（API 不帶 revision，等於舊用戶端）→ 這邊存檔被擋
    const other = await call(page, "PUT", `/api/v1/locations/${loc.id}/rack-positions`,
                             { positions: [{ id: r2.id, pos_x: 0.8, pos_y: 0.2, pos_rot: 0 }] });
    expect(other.status).toBe(200);
    await page.getByTestId("fp-save").click();
    await expect(dlg).toContainText("平面配置剛被別人改過", { timeout: 10_000 });
    await page.screenshot({ path: "test-results/floorplan-conflict.png" });
    const racks = (await call(page, "GET", `/api/v1/racks?location_id=${loc.id}`)).json.items;
    expect(racks.find((x: any) => x.id === r2.id).pos_x).toBeCloseTo(0.8);
    expect(racks.find((x: any) => x.id === r1.id).pos_x, "被擋下的存檔不可以寫進去").toBeNull();
    await dlg.getByRole("button", { name: "重新載入" }).click();
  } finally {
    for (const r of [r1, r2]) await call(page, "DELETE", `/api/v1/racks/${r.id}`);
    await call(page, "DELETE", `/api/v1/locations/${loc.id}`);
  }
});
