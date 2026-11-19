import { test, expect, type Page } from "@playwright/test";

/**
 * 編輯 NAT 規則按儲存（GitHub issue #59）：以前不管有沒有改，PATCH 都回 422「Extra inputs are not permitted (type)」。
 *   1. 打開編輯、什麼都不改、按儲存 → 成功
 *   2. 改類型再儲存 → 表格上的類型跟著變
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const r = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: r.status, json: r.status === 204 ? null : await r.json().catch(() => null) };
  }, { method, path, body });
}

test("編輯 NAT 規則：不改直接儲存、改類型再儲存都成功", async ({ page }) => {
  const name = `e2e-nat-edit-${Date.now().toString(36)}`;
  await login(page);
  const made = await api(page, "POST", "/api/v1/nat", { name, type: "one_to_one" });
  expect(made.status, JSON.stringify(made.json)).toBe(201);
  try {
    await page.goto("/nat");
    const row = page.locator(".n-data-table-tbody tr", { hasText: name });
    await expect(row).toBeVisible({ timeout: 15_000 });
    const patches: number[] = [];
    page.on("response", (r) => { if (r.request().method() === "PATCH" && r.url().includes("/api/v1/nat/")) patches.push(r.status()); });

    await row.locator("td").last().locator("button").first().click();      // 編輯（鉛筆）
    const modal = page.locator(".n-modal");
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    expect(patches).toEqual([200]);

    await row.locator("td").last().locator("button").first().click();
    await modal.locator(".n-form-item", { hasText: "類型" }).locator(".n-base-selection").first().click();
    await page.locator(".n-base-select-option", { hasText: /Port forward/i }).first().click();
    await page.screenshot({ path: "test-results/nat-edit-type.png" });
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    expect(patches).toEqual([200, 200]);
    const got = await api(page, "GET", `/api/v1/nat/${made.json.id}`);
    expect(got.json.type).toBe("port_forward");
  } finally {
    await api(page, "DELETE", `/api/v1/nat/${made.json.id}`);
  }
});
