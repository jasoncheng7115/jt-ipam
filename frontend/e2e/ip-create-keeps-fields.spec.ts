import { test, expect, type Page } from "@playwright/test";

/**
 * 新增 IP 時，新增視窗上看得到的設定要存進去（2026-11-21 掃描表單缺口時發現）：
 * 以前只送 12 個欄位，主控台開關、略過的掃描項目、略過 PTR 在新增時選了也不會存。
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
  return page.evaluate(async ({ method, path, body }) => {
    const res = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: res.status, json: res.status === 204 ? null : await res.json().catch(() => null) };
  }, { method, path, body });
}

test("新增 IP：主控台開關、略過的掃描項目、略過 PTR 都會存", async ({ page }) => {
  await login(page);
  const n = 100 + Math.floor(Math.random() * 150);
  const cidr = `10.251.${n}.0/24`;
  const sections = (await api(page, "GET", "/api/v1/sections?page_size=1")).json.items;
  const sub = (await api(page, "POST", "/api/v1/subnets", { cidr, section_id: sections[0].id })).json;
  try {
    await page.goto(`/subnets/${sub.id}`);
    await page.getByRole("button", { name: "新增位址" }).first().click({ timeout: 20_000 });
    const modal = page.locator(".n-modal");
    await modal.getByPlaceholder("198.51.100.10").fill(`10.251.${n}.20`);
    await modal.locator(".n-form-item", { hasText: "啟用 SSH 連線管理" }).locator(".n-switch").click();
    await modal.locator(".n-form-item", { hasText: "略過 PTR" }).locator(".n-switch").click();
    await modal.locator(".n-checkbox", { hasText: "mDNS 名稱" }).click();
    await page.screenshot({ path: "test-results/ip-create-fields.png" });
    await modal.getByRole("button", { name: "新增", exact: true }).click();
    await expect(modal).toHaveCount(0, { timeout: 15_000 });
    const list = (await api(page, "GET", `/api/v1/addresses?subnet_id=${sub.id}&page_size=50`)).json.items;
    const ip = list.find((x: any) => String(x.ip).split("/")[0] === `10.251.${n}.20`);
    expect(ip, "IP 建起來了").toBeTruthy();
    expect(ip.ssh_enabled).toBe(true);
    expect(ip.ptr_ignore).toBe(true);
    expect(ip.excluded_probes).toEqual(["mdns"]);
  } finally {
    await api(page, "DELETE", `/api/v1/subnets/${sub.id}`);
  }
});
