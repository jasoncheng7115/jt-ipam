import { test, expect, type Page } from "@playwright/test";

/**
 * 進階模組補上的欄位（2026-11-21 掃描）：供應商的 ASN / 入口網址 / NOC、聯絡人的職稱 / 地址、群組的上層。
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

test("供應商 ASN / 入口 / NOC、聯絡人職稱、群組上層", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const prov = await api(page, "POST", "/api/v1/providers", { name: `e2e-isp-${u}` });
  const ct = await api(page, "POST", "/api/v1/contacts", { name: `e2e-ct-${u}` });
  const g1 = await api(page, "POST", "/api/v1/contact-groups", { name: `e2e-cg1-${u}` });
  const g2 = await api(page, "POST", "/api/v1/contact-groups", { name: `e2e-cg2-${u}`, parent_id: g1.id });
  try {
    const modal = page.locator(".n-modal");
    // 供應商
    await page.goto("/advanced/circuits");
    await page.locator(".n-tabs-tab", { hasText: "電路供應商" }).click();
    const prow = page.locator(".n-data-table-tbody tr", { hasText: `e2e-isp-${u}` });
    await prow.locator("td").last().locator("button").first().click({ timeout: 20_000 });
    await modal.getByTestId("provider-asn").locator("input").fill("3462");
    await modal.getByTestId("provider-portal").locator("input").fill("https://portal.example.net");
    await modal.getByTestId("provider-noc").locator("input").fill("noc@example.net");
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(prow).toContainText("AS3462", { timeout: 10_000 });
    await expect(prow).toContainText("noc@example.net");
    await page.screenshot({ path: "test-results/advanced-provider.png" });

    // 聯絡人職稱
    await page.goto("/advanced/contacts");
    const crow = page.locator(".n-data-table-tbody tr", { hasText: `e2e-ct-${u}` });
    await crow.locator("td").last().locator("button").first().click({ timeout: 20_000 });
    await modal.getByTestId("contact-title").locator("input").fill("業務經理");
    await modal.getByTestId("contact-address").locator("textarea").fill("台北市");
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(crow).toContainText("業務經理", { timeout: 10_000 });

    // 聯絡人群組：上層選單不出現自己與子群組
    await page.locator(".n-tabs-tab", { hasText: "聯絡人群組" }).click();
    // cg2 的「上層群組」欄也寫著 cg1，用名稱欄（第一欄）找 cg1 那一列
    const grow = page.locator(".n-data-table-tbody tr").filter({ has: page.locator("td").first().getByText(`e2e-cg1-${u}`, { exact: true }) });
    await grow.locator("td").last().locator("button").first().click();
    const sel = modal.getByTestId("contact-group-parent");
    await sel.click();
    await sel.locator("input").fill(u);
    const opts = (await page.locator(".n-base-select-option").allInnerTexts()).map((x) => x.trim());
    expect(opts).not.toContain(`e2e-cg1-${u}`);
    expect(opts).not.toContain(`e2e-cg2-${u}`);
    await page.keyboard.press("Escape");
    await modal.getByRole("button", { name: /取消/ }).click();
    await expect(page.locator(".n-data-table-tbody tr", { hasText: `e2e-cg2-${u}` })).toContainText(`e2e-cg1-${u}`);
  } finally {
    await api(page, "DELETE", `/api/v1/contact-groups/${g2.id}`);
    await api(page, "DELETE", `/api/v1/contact-groups/${g1.id}`);
    await api(page, "DELETE", `/api/v1/contacts/${ct.id}`);
    await api(page, "DELETE", `/api/v1/providers/${prov.id}`);
  }
});
