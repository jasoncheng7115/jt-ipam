/**
 * IP 詳細頁「對外路徑」（客戶許願清單 2026-10-28）：有 NAT 轉發時出現，畫出 WAN 位址:埠 → 防火牆 → 內部主機:埠，
 * 可以插入中間設備（虛線框）、刪除。資料都是這支測試建的，結束時刪掉。
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function token(request: APIRequestContext): Promise<string> {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  expect(r.ok()).toBeTruthy();
  return (await r.json()).access_token;
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("對外路徑：WAN → 防火牆 → 內部主機，插入 WAF 再移除", async ({ page, request }) => {
  const auth = { Authorization: `Bearer ${await token(request)}` };
  const tag = Date.now().toString(36);
  const post = async (url: string, data: unknown) => {
    const r = await request.post(url, { headers: auth, data });
    expect(r.ok(), `${url} ${await r.text()}`).toBeTruthy();
    return r.json();
  };
  const sec = await post("/api/v1/sections", { name: `path-e2e-${tag}` });
  const wan = await post("/api/v1/subnets", { section_id: sec.id, cidr: "192.0.2.240/29", allow_overlap: true });
  const lan = await post("/api/v1/subnets", { section_id: sec.id, cidr: "192.0.2.224/28", allow_overlap: true });
  const ext = await post("/api/v1/addresses", { subnet_id: wan.id, ip: "192.0.2.242" });
  const web = await post("/api/v1/addresses", { subnet_id: lan.id, ip: "192.0.2.230", hostname: `web-${tag}` });
  const nat = await post("/api/v1/nat", { name: `https-${tag}`, type: "port_forward", protocol: "tcp",
                                          src_ip_id: ext.id, src_port: 443, dst_ip_id: web.id, dst_port: 8443 });
  try {
    await login(page);
    await page.goto(`/addresses/${web.id}`);
    const card = page.getByTestId("exposure-path");
    await expect(card).toBeVisible({ timeout: 20_000 });
    const row = card.getByTestId("exposure-path-row");
    await expect(row).toContainText("192.0.2.242:443");
    await expect(row).toContainText("192.0.2.230:8443");
    await expect(row).toContainText(`https-${tag}`);
    // 插入 WAF（填名稱）
    await page.getByTestId("exposure-add-hop").click();
    await page.getByTestId("exposure-hop-label").locator("input").fill(`雲端 WAF ${tag}`);
    await page.getByTestId("exposure-hop-save").click();
    await expect(row).toContainText(`雲端 WAF ${tag}`);
    await expect(card.locator(".hop.manual")).toHaveCount(1);
    await page.mouse.move(4, 4);
    await card.screenshot({ path: "test-results/exposure-path.png" });
    // 從 WAN IP 的頁面也看得到同一條（手動設備唯讀）
    await page.goto(`/addresses/${ext.id}`);
    await expect(page.getByTestId("exposure-path")).toContainText(`雲端 WAF ${tag}`);
    await expect(page.getByTestId("exposure-add-hop")).toHaveCount(0);
  } finally {
    await request.delete(`/api/v1/nat/${nat.id}`, { headers: auth });
    await request.delete(`/api/v1/addresses/${web.id}`, { headers: auth });
    await request.delete(`/api/v1/addresses/${ext.id}`, { headers: auth });
    await request.delete(`/api/v1/subnets/${lan.id}`, { headers: auth });
    await request.delete(`/api/v1/subnets/${wan.id}`, { headers: auth });
    await request.delete(`/api/v1/sections/${sec.id}`, { headers: auth });
  }
});
