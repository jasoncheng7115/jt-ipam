import { test, expect, type Page } from "@playwright/test";

/**
 * 區段的編輯表單：列表頁與詳細頁共用同一份欄位（以前列表頁有「上層區段」沒有「嚴格模式」，詳細頁剛好相反）。
 * 上層選單不出現自己與自己底下的區段（後端也擋，回 section_parent_cycle）。
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.setTimeout(120_000);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function api(page: Page, method: string, path: string, body?: unknown) {
  const r = await page.evaluate(async ({ method, path, body }) => {
    const res = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: res.status, json: res.status === 204 ? null : await res.json().catch(() => null) };
  }, { method, path, body });
  expect(r.status, `${method} ${path} ${JSON.stringify(r.json)}`).toBeLessThan(300);
  return r.json;
}

/** 上層選單打字搜尋後看得到哪些選項（選單是虛擬捲動，不搜尋只讀得到畫面上那幾個） */
async function parentOptions(page: Page, q: string): Promise<string[]> {
  const sel = page.locator(".n-modal").getByTestId("section-form-parent");
  await sel.click();
  await sel.locator("input").fill(q);
  await page.waitForTimeout(300);
  const opts = await page.locator(".n-base-select-option").allInnerTexts();
  await page.keyboard.press("Escape");
  return opts.map((o) => o.trim());
}

test("列表頁與詳細頁的區段表單一樣；上層選單排除自己與子區段", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const top = await api(page, "POST", "/api/v1/sections", { name: `e2e-top-${u}` });
  const child = await api(page, "POST", "/api/v1/sections", { name: `e2e-child-${u}`, parent_id: top.id });
  const other = await api(page, "POST", "/api/v1/sections", { name: `e2e-other-${u}` });
  try {
    // 列表頁：編輯 top
    await page.goto("/sections");
    await page.getByPlaceholder("篩選").first().fill(`e2e-top-${u}`);            // 樣本多時不一定在第一頁
    const row = page.locator(".n-data-table-tbody tr", { hasText: `e2e-top-${u}` });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.locator("td").last().locator("button").first().click();
    const modal = page.locator(".n-modal");
    await expect(modal.getByTestId("section-form-strict")).toBeVisible();
    let opts = await parentOptions(page, u);
    expect(opts).toContain(`e2e-other-${u}`);
    expect(opts).not.toContain(`e2e-top-${u}`);
    expect(opts).not.toContain(`e2e-child-${u}`);
    await page.screenshot({ path: "test-results/section-form-list.png" });
    await modal.getByRole("button", { name: /取消/ }).click();

    // 詳細頁：同一份欄位，改上層再儲存
    await page.goto(`/sections/${child.id}`);
    await page.getByRole("button", { name: "編輯" }).first().click();
    await expect(modal.getByTestId("section-form-strict")).toBeVisible();
    opts = await parentOptions(page, u);
    expect(opts).toContain(`e2e-top-${u}`);
    expect(opts).not.toContain(`e2e-child-${u}`);
    await modal.getByTestId("section-form-parent").click();
    await modal.getByTestId("section-form-parent").locator("input").fill(`e2e-other-${u}`);
    await page.locator(".n-base-select-option", { hasText: `e2e-other-${u}` }).click();
    await page.screenshot({ path: "test-results/section-form-detail.png" });
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    const got = await api(page, "GET", `/api/v1/sections/${child.id}`);
    expect(got.parent_id).toBe(other.id);
  } finally {
    for (const id of [child.id, top.id, other.id]) await api(page, "DELETE", `/api/v1/sections/${id}`);
  }
});
