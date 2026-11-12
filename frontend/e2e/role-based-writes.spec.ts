/**
 * 角色授權在畫面上要有用（客戶 2026-11-12）：LDAP 帳號掛了「系統管理員」角色，「新增機櫃」卻反灰、
 * 提示要管理員，一定要打開帳號的「管理員」開關才行。
 *
 * 用一個「不是管理員、只有角色」的臨時帳號實際登入、新增一座機櫃；換成「唯讀檢視者」按鈕就反灰並說明原因。
 * LDAP「測試帳密」的結果要留在畫面上，看得出為什麼是／不是管理員（回應用 page.route 模擬，不需要真的目錄）。
 */
import crypto from "node:crypto";
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.setTimeout(120_000);

async function adminHeaders(request: APIRequestContext) {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  expect(r.ok()).toBeTruthy();
  return { Authorization: `Bearer ${(await r.json()).access_token}` };
}

async function roleId(request: APIRequestContext, name: string): Promise<string> {
  const r = await request.get("/api/v1/groups?page_size=200", { headers: await adminHeaders(request) });
  const g = (await r.json()).items.find((x: { name: string }) => x.name === name);
  expect(g, `內建角色「${name}」要存在`).toBeTruthy();
  return g.id;
}

async function login(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(username);
  await page.getByPlaceholder(/密碼|Password/).fill(password);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("掛「系統管理員」角色的非管理員帳號可以新增機櫃；唯讀檢視者按鈕反灰並說明", async ({ page, request }) => {
  const h = await adminHeaders(request);
  const username = `e2e-role-${Date.now().toString(36)}`;
  const password = `Tmp-${crypto.randomBytes(9).toString("base64url")}`;
  const u = await request.post("/api/v1/users", { headers: h, data: { username, email: "", password, is_admin: false } });
  expect(u.status(), await u.text()).toBe(201);
  const userId = (await u.json()).id;
  const sysadmin = await roleId(request, "系統管理員");
  const viewer = await roleId(request, "唯讀檢視者");
  const rackName = `E2E-ROLE-${Date.now().toString(36).toUpperCase()}`;
  try {
    expect((await request.post(`/api/v1/groups/${sysadmin}/members/${userId}`, { headers: h })).status()).toBe(204);
    await login(page, username, password);
    await page.goto("/racks");
    const add = page.getByRole("button", { name: "新增機櫃" });
    await expect(add).toBeEnabled({ timeout: 20_000 });
    await add.click();
    const modal = page.locator(".n-modal");
    await modal.locator(".n-form-item", { hasText: "名稱" }).first().locator("input").fill(rackName);
    const saved = page.waitForResponse((r) => r.url().endsWith("/api/v1/racks") && r.request().method() === "POST");
    await modal.getByRole("button", { name: "儲存" }).click();
    expect((await saved).status()).toBe(201);
    await page.screenshot({ path: "test-results/role-based-writes-sysadmin.png" });

    // 換成唯讀檢視者：重新登入後按鈕反灰，滑過去說明要什麼權限
    await request.delete(`/api/v1/groups/${sysadmin}/members/${userId}`, { headers: h });
    await request.post(`/api/v1/groups/${viewer}/members/${userId}`, { headers: h });
    await page.context().clearCookies();
    await login(page, username, password);
    await page.goto("/racks");
    const add2 = page.getByRole("button", { name: "新增機櫃" });
    await expect(add2).toBeDisabled({ timeout: 20_000 });
    await expect(add2).toHaveAttribute("title", /沒有新增權限/);
  } finally {
    const list = await request.get(`/api/v1/racks?q=${rackName}&page_size=5`, { headers: h });
    for (const r of (await list.json()).items ?? []) await request.delete(`/api/v1/racks/${r.id}`, { headers: h });
    await request.delete(`/api/v1/users/${userId}`, { headers: h });
  }
});

test("LDAP 測試帳密：顯示管理員判定與目錄回的群組", async ({ page }) => {
  await page.route("**/api/v1/system/ldap/test-auth", (route) => route.fulfill({ json: {
    ok: true, dn: "CN=Nest Nine,CN=Users,DC=corp,DC=test", username: "nest09", display_name: "Nest Nine", email: null,
    is_admin: true, admin_groups_configured: true, admin_via: { group: "IPAM Admins", via: "nested" },
    groups: ["CN=Net Team,CN=Users,DC=corp,DC=test"],
  } }));
  await login(page, ADMIN_USER, ADMIN_PASS);
  await page.goto("/system-settings");
  const card = page.locator(".n-card", { hasText: "LDAP / AD" }).first();
  await card.getByPlaceholder("測試帳號").fill("nest09");
  await card.getByPlaceholder("測試密碼").fill("x");
  await card.getByRole("button", { name: "測試帳密" }).click();
  const res = page.getByTestId("ldap-auth-result");
  await expect(res).toContainText("CN=Nest Nine,CN=Users,DC=corp,DC=test");
  await expect(page.getByTestId("ldap-auth-admin")).toContainText("是，比對到「IPAM Admins」（經由巢狀群組）");
  await expect(res).toContainText("目錄回傳的直接所屬群組（1 個）");
  await expect(res).toContainText("CN=Net Team,CN=Users,DC=corp,DC=test");
  await res.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/role-based-writes-ldap.png" });

  // 沒比對到
  await page.unroute("**/api/v1/system/ldap/test-auth");
  await page.route("**/api/v1/system/ldap/test-auth", (route) => route.fulfill({ json: {
    ok: true, dn: "CN=Plain,CN=Users,DC=corp,DC=test", username: "plain08", display_name: null, email: null,
    is_admin: false, admin_groups_configured: true, admin_via: null, groups: [],
  } }));
  await card.getByPlaceholder("測試密碼").fill("x");
  await card.getByRole("button", { name: "測試帳密" }).click();
  await expect(page.getByTestId("ldap-auth-admin")).toContainText("否，沒有比對到");
});
