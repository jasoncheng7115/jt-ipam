import { test, expect, type Page } from "@playwright/test";

/**
 * 自訂欄位的值（2026-11-21）：以前定義得出來、任何表單都填不了；定義了必填欄位時，新增會一律失敗。
 *   1. 新增 IP 視窗出現自訂欄位（必填文字、單選），填了會存
 *   2. 裝置詳細頁的編輯視窗帶出既有值，改了會存；日期型欄位用全站的 jt-date-picker（右下按鈕有圖示）
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
  return page.evaluate(async ({ method, path, body }) => {
    const res = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: res.status, json: res.status === 204 ? null : await res.json().catch(() => null) };
  }, { method, path, body });
}

test("新增 IP 與編輯裝置都能填自訂欄位", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const made: string[] = [];
  const def = async (object_type: string, body: Record<string, unknown>) => {
    const r = await api(page, "POST", "/api/v1/custom-fields", { object_type, ...body });
    expect(r.status, JSON.stringify(r.json)).toBe(201);
    made.push(r.json.id);
  };
  const n = 100 + Math.floor(Math.random() * 150);
  const sections = (await api(page, "GET", "/api/v1/sections?page_size=1")).json.items;
  const sub = (await api(page, "POST", "/api/v1/subnets", { cidr: `10.252.${n}.0/24`, section_id: sections[0].id })).json;
  const dev = (await api(page, "POST", "/api/v1/devices", { name: `e2e-cf-${u}`, type: "server" })).json;
  try {
    await def("ip", { name: `asset_${u}`, label_zh_tw: `資產編號${u}`, field_type: "text", required: true });
    await def("ip", { name: `tier_${u}`, label_zh_tw: `等級${u}`, field_type: "select", options: { choices: ["gold", "silver"] } });
    await def("device", { name: `rack_tag_${u}`, label_zh_tw: `機櫃標籤${u}`, field_type: "text" });
    await def("device", { name: `warranty_${u}`, label_zh_tw: `保固到期${u}`, field_type: "date" });

    // 1. 新增 IP
    await page.goto(`/subnets/${sub.id}`);
    await page.getByRole("button", { name: "新增位址" }).first().click({ timeout: 20_000 });
    const modal = page.locator(".n-modal");
    await modal.getByPlaceholder("198.51.100.10").fill(`10.252.${n}.30`);
    await modal.getByTestId(`cf-asset_${u}`).locator("input").fill("A-0001");
    await modal.getByTestId(`cf-tier_${u}`).locator(".n-base-selection").click();
    await page.locator(".n-base-select-option", { hasText: "gold" }).click();
    await modal.getByTestId(`cf-asset_${u}`).scrollIntoViewIfNeeded();
    await page.screenshot({ path: "test-results/custom-fields-ip.png" });
    await modal.getByRole("button", { name: "新增", exact: true }).click();
    await expect(modal).toHaveCount(0, { timeout: 15_000 });
    const ips = (await api(page, "GET", `/api/v1/addresses?subnet_id=${sub.id}&page_size=50`)).json.items;
    const ip = ips.find((x: any) => String(x.ip).split("/")[0] === `10.252.${n}.30`);
    expect(ip?.custom_fields).toEqual({ [`asset_${u}`]: "A-0001", [`tier_${u}`]: "gold" });

    // 2. 裝置詳細頁編輯
    await page.goto(`/devices/${dev.id}`);
    await page.getByRole("button", { name: "編輯" }).first().click({ timeout: 20_000 });
    await modal.getByTestId(`cf-rack_tag_${u}`).locator("input").fill("R12-U5");
    const dateInput = modal.getByTestId(`cf-warranty_${u}`).locator("input");
    await dateInput.click();
    await expect(page.getByTestId("date-clear")).toBeVisible();   // 共用元件的圖示按鈕
    await dateInput.fill("2027-03-31");
    await dateInput.press("Enter");
    await modal.getByTestId(`cf-rack_tag_${u}`).locator("input").click();
    await modal.getByRole("button", { name: "儲存" }).click();
    await expect(modal).toHaveCount(0, { timeout: 15_000 });
    expect((await api(page, "GET", `/api/v1/devices/${dev.id}`)).json.custom_fields)
      .toEqual({ [`rack_tag_${u}`]: "R12-U5", [`warranty_${u}`]: "2027-03-31" });
    // 再開一次，既有值帶回來
    await page.getByRole("button", { name: "編輯" }).first().click();
    await expect(modal.getByTestId(`cf-rack_tag_${u}`).locator("input")).toHaveValue("R12-U5");
  } finally {
    for (const id of made) await api(page, "DELETE", `/api/v1/custom-fields/${id}`);
    await api(page, "DELETE", `/api/v1/devices/${dev.id}`);
    await api(page, "DELETE", `/api/v1/subnets/${sub.id}`);
  }
});
