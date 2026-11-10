/**
 * GitHub issue #58：NAT 規則的 IP 下拉只載第一頁（500 筆）。站台 740 個 IP 時，不在第一頁的選不到、打字也搜不到；
 * 表格上不在第一頁的 IP 顯示成 ID 前 8 碼。現在搜尋走後端、表格的位址與裝置名稱由清單 API 帶。
 */
import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

const FAR = { id: "00000000-0000-0000-0000-000000000740", ip: "198.51.100.240", hostname: "far-host" };
const first500 = Array.from({ length: 500 }, (_, i) => ({ id: `00000000-0000-0000-0001-${String(i).padStart(12, "0")}`,
  ip: `203.0.113.${i % 250}`, hostname: `near-${i}` }));

test("IP 超過一頁：打字搜尋選得到不在第一頁的位址，表格顯示位址不是 ID", async ({ page }) => {
  const asked: string[] = [];
  await page.route(/\/api\/v1\/addresses\?/, (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams.get("q") || "";
    asked.push(q);
    const items = q ? [FAR].filter((a) => a.ip.includes(q) || a.hostname.includes(q)) : first500;
    return route.fulfill({ json: { items, total: q ? items.length : 740, page: 1, page_size: 200 } });
  });
  await page.route(/\/api\/v1\/nat(\?.*)?$/, (route) => route.request().method() !== "GET" ? route.continue()
    : route.fulfill({ json: { items: [{
      id: "n-1", name: "pf-far", type: "port_forward", protocol: "tcp", src_ip_id: FAR.id, dst_ip_id: null, device_id: "d-1",
      src_ip: FAR.ip, dst_ip: null, device_name: "fw-far", src_interface: "wan", src_port: 443, dst_port: null,
      description: null, disabled: false, source_kind: "manual", source_label: "手動", created_at: new Date().toISOString(),
      updated_at: new Date().toISOString() }], total: 1, page: 1, page_size: 200 } }));
  let created: any = null;
  await page.route(/\/api\/v1\/nat$/, (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    created = route.request().postDataJSON();
    return route.fulfill({ status: 201, json: { ...created, id: "n-2", created_at: new Date().toISOString(), updated_at: new Date().toISOString() } });
  });
  await login(page);
  await page.goto("/nat");
  // 表格：不在第一頁的 IP 與裝置顯示名稱，不是 ID 前 8 碼
  const row = page.locator("tr", { hasText: "pf-far" });
  await expect(row).toContainText(FAR.ip);
  await expect(row).toContainText("fw-far");
  await expect(row).not.toContainText("00000000…");

  await page.getByRole("button", { name: "新增", exact: true }).first().click();
  await page.locator(".n-form-item", { hasText: "名稱" }).first().locator("input").fill("pf-new");
  const src = page.getByTestId("nat-src-ip-select");
  await src.click();
  await page.keyboard.type("198.51.100.240");
  await expect(page.locator(".n-base-select-option", { hasText: "198.51.100.240 — far-host" })).toBeVisible();
  expect(asked).toContain("198.51.100.240");          // 關鍵字送到後端，不是在已載入的 500 筆裡找
  await page.locator(".n-base-select-option", { hasText: "198.51.100.240 — far-host" }).click();
  await page.getByRole("button", { name: "儲存" }).last().click();
  await expect.poll(() => created?.src_ip_id).toBe(FAR.id);
});
