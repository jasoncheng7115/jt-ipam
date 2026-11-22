import { test, expect, type Page } from "@playwright/test";

/**
 * 新增纜線跟編輯同一份欄位（2026-11-21）：以前新增只能填類型與線標，顏色、長度、狀態、說明要建好再編輯一次；
 * 兩端裝置選單也只載前 500 台。
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

async function pick(page: Page, testId: string, text: string, remote = true) {
  const sel = page.locator(".n-modal").getByTestId(testId);
  await sel.click();
  if (remote) await sel.locator("input").fill(text);
  await page.locator(".n-base-select-option", { hasText: text }).first().click({ timeout: 10_000 });
}

test("新增纜線：顏色、長度、狀態、說明一次填好；裝置可搜尋", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const a = await api(page, "POST", "/api/v1/devices", { name: `e2e-cab-a-${u}`, type: "switch" });
  const b = await api(page, "POST", "/api/v1/devices", { name: `e2e-cab-b-${u}`, type: "server" });
  await api(page, "POST", "/api/v1/device-ports", { device_id: a.id, name: "Gi1/0/1", type: "network" });
  await api(page, "POST", "/api/v1/device-ports", { device_id: b.id, name: "eth0", type: "network" });
  try {
    await page.goto("/cabling");
    await page.getByRole("button", { name: "新增纜線" }).click({ timeout: 20_000 });
    await pick(page, "cable-a-device", `e2e-cab-a-${u}`);
    await pick(page, "cable-a-port", "Gi1/0/1", false);
    await pick(page, "cable-b-device", `e2e-cab-b-${u}`);
    await pick(page, "cable-b-port", "eth0", false);
    const modal = page.locator(".n-modal");
    await modal.getByTestId("cable-label").locator("input").fill(`L-${u}`);
    await modal.getByTestId("cable-color").locator("input").fill("blue");
    await modal.getByTestId("cable-length").locator("input").fill("3");
    await modal.locator(".n-form-item", { hasText: "狀態" }).locator(".n-base-selection").click();
    await page.locator(".n-base-select-option", { hasText: "規劃中" }).click();
    await page.screenshot({ path: "test-results/cable-create.png" });
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    const cables = (await api(page, "GET", "/api/v1/cables?page_size=500")).items;
    const c = cables.find((x: any) => x.label === `L-${u}`);
    expect(c, "纜線建起來了").toBeTruthy();
    expect([c.color, Number(c.length_m), c.status]).toEqual(["blue", 3, "planned"]);
    await api(page, "DELETE", `/api/v1/cables/${c.id}`);
  } finally {
    await api(page, "DELETE", `/api/v1/devices/${a.id}`);
    await api(page, "DELETE", `/api/v1/devices/${b.id}`);
  }
});
