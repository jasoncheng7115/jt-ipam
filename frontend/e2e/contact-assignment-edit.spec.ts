import { test, expect, type Page } from "@playwright/test";

/**
 * 電路上已指派的聯絡人可以改角色與優先序（2026-11-21：以前建好就改不了，API 有、畫面沒有）。
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
  return (await page.evaluate(async ({ method, path, body }) => {
    const res = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: res.status, json: res.status === 204 ? null : await res.json().catch(() => null) };
  }, { method, path, body })).json;
}

test("改已指派聯絡人的優先序", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const prov = await api(page, "POST", "/api/v1/providers", { name: `e2e-isp2-${u}` });
  const cir = await api(page, "POST", "/api/v1/circuits", { cid: `E2E-${u}`, provider_id: prov.id });
  const ct = await api(page, "POST", "/api/v1/contacts", { name: `e2e-noc-${u}` });
  await api(page, "POST", "/api/v1/contact-assignments", { contact_id: ct.id, role_id: null, object_type: "circuit",
                                                          object_id: cir.id });
  try {
    await page.goto(`/advanced/circuits/${cir.id}`);
    const row = page.locator(".n-data-table-tbody tr", { hasText: `e2e-noc-${u}` });
    await row.getByTestId("contact-assign-edit").click({ timeout: 20_000 });
    const modal = page.locator(".n-modal");
    await modal.getByTestId("contact-edit-priority").locator("input").fill("9");
    await page.screenshot({ path: "test-results/contact-assign-edit.png" });
    await modal.getByTestId("contact-edit-save").click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    await expect(row).toContainText("9");
    const detail = await api(page, "GET", `/api/v1/circuits/${cir.id}/detail`);
    expect(detail.contacts.circuit.find((c: any) => c.contact.name === `e2e-noc-${u}`)?.priority).toBe(9);
  } finally {
    await api(page, "DELETE", `/api/v1/circuits/${cir.id}`);
    await api(page, "DELETE", `/api/v1/contacts/${ct.id}`);
    await api(page, "DELETE", `/api/v1/providers/${prov.id}`);
  }
});
