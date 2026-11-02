/**
 * 電路詳細資料（客戶許願清單 2026-10-28）：從電路清單點進來、舊欄位轉成 IPAM 關聯、每個 WAN IP 的使用跡象、
 * 新增電路聯絡人、在裝置頁設定租用設備後出現在電路頁。
 *
 * 打真的後端；資料都是這支測試自己建的（192.0.2.248/29），結束時刪掉。
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

test("電路詳細資料：舊欄位轉成 WAN 關聯、新增聯絡人、裝置頁設定租用後出現在電路頁", async ({ page, request }) => {
  const auth = { Authorization: `Bearer ${await token(request)}` };
  const tag = Date.now().toString(36);
  const post = async (url: string, data: unknown) => {
    const r = await request.post(url, { headers: auth, data });
    expect(r.ok(), `${url} ${await r.text()}`).toBeTruthy();
    return r.json();
  };
  const sec = await post("/api/v1/sections", { name: `wan-e2e-${tag}` });
  // e2e 庫已經有 192.0.2.0/24；這一段裡沒有任何位址，允許重疊即可
  const sub = await post("/api/v1/subnets", { section_id: sec.id, cidr: "192.0.2.248/29", description: "ISP block",
                                              allow_overlap: true });
  await post("/api/v1/addresses", { subnet_id: sub.id, ip: "192.0.2.249", hostname: "isp-gw" });
  await post("/api/v1/addresses", { subnet_id: sub.id, ip: "192.0.2.250", hostname: "fw-wan" });
  const prov = await post("/api/v1/providers", { name: `ISP-${tag}` });
  const circuit = await post("/api/v1/circuits", { cid: `WAN-${tag}`, provider_id: prov.id, status: "active",
                                                   ip_address: "192.0.2.250/29", gateway: "192.0.2.249" });
  const dev = await post("/api/v1/devices", { name: `isp-cpe-${tag}`, type: "router" });
  try {
    await login(page);
    // 從電路清單點電路編號進來
    await page.goto("/advanced/circuits");
    await page.locator('.n-tab-pane .n-tabs-tab[data-name="circuits"]').click();
    const pane = page.locator(".n-tab-pane:visible").last();
    await pane.getByPlaceholder("篩選").fill(`WAN-${tag}`);
    await pane.locator("tbody tr", { hasText: `WAN-${tag}` }).getByText(`WAN-${tag}`).click();
    await expect(page).toHaveURL(new RegExp(`/advanced/circuits/${circuit.id}$`));
    await expect(page.getByTestId("circuit-cid")).toHaveText(`WAN-${tag}`);

    // 舊的文字欄：提示可以轉換 → 轉成子網路＋閘道兩筆關聯
    const wan = page.getByTestId("circuit-wan");
    await expect(wan).toContainText("192.0.2.250/29");
    await page.getByTestId("circuit-wan-legacy").click();
    await expect(page.getByTestId("circuit-wan-link")).toHaveCount(2, { timeout: 15_000 });
    await expect(wan).toContainText("192.0.2.248/29");
    await expect(wan).toContainText("ISP 閘道");
    await expect(wan).toContainText("可用 6 個，已登記 2 個");
    await expect(wan.locator("tbody tr", { hasText: "192.0.2.250" }).first()).toContainText("fw-wan");

    // 新增電路聯絡人（直接建一位）
    await page.getByTestId("contact-add-circuit").click();
    const dlg = page.locator(".n-modal");
    await dlg.getByText("建立新的聯絡人").click();
    await page.getByTestId("contact-name").locator("input").fill(`李機房-${tag}`);
    await page.getByTestId("contact-role").click();
    await page.locator(".n-base-select-option", { hasText: "機房現場" }).click();
    await page.getByTestId("contact-save").click();
    const contacts = page.getByTestId("circuit-contacts");
    await expect(contacts).toContainText(`李機房-${tag}`);
    await expect(contacts).toContainText("機房現場");

    // 裝置頁設定租用 → 電路頁列出
    await page.goto(`/devices/${dev.id}`);
    await page.getByTestId("device-lease-edit").click();
    await page.getByTestId("device-lease-circuit").click();
    await page.locator(".n-base-select-option", { hasText: `WAN-${tag}` }).click();
    // 選了電路，供應商自動帶出
    await expect(page.getByTestId("device-lease-provider")).toContainText(`ISP-${tag}`);
    await page.getByTestId("device-lease-save").click();
    const card = page.getByTestId("device-lease-card");
    await expect(card).toContainText(`WAN-${tag}`);
    await card.getByText(`WAN-${tag}`).click();
    await expect(page).toHaveURL(new RegExp(`/advanced/circuits/${circuit.id}$`));
    await expect(page.getByTestId("circuit-leases")).toContainText(`isp-cpe-${tag}`);
    await page.mouse.move(4, 4);
    // 給人看版面用（test-results 不進版控）
    for (const id of ["circuit-contacts", "circuit-wan", "circuit-leases"]) {
      await page.getByTestId(id).screenshot({ path: `test-results/${id}.png` });
    }
  } finally {
    const cs = await (await request.get("/api/v1/contacts?page_size=500", { headers: auth })).json();
    for (const c of cs.items ?? []) {
      if (String(c.name).endsWith(tag)) await request.delete(`/api/v1/contacts/${c.id}`, { headers: auth });
    }
    await request.delete(`/api/v1/devices/${dev.id}`, { headers: auth });
    await request.delete(`/api/v1/circuits/${circuit.id}`, { headers: auth });
    await request.delete(`/api/v1/providers/${prov.id}`, { headers: auth });
    await request.delete(`/api/v1/subnets/${sub.id}`, { headers: auth });
    await request.delete(`/api/v1/sections/${sec.id}`, { headers: auth });
  }
});

test("站對站 VPN 跟電路一起管：電路區的 VPN 頁籤點進詳細資料，設聯絡人、看出跑在哪條電路上", async ({ page, request }) => {
  const auth = { Authorization: `Bearer ${await token(request)}` };
  const tag = Date.now().toString(36);
  const post = async (url: string, data: unknown) => {
    const r = await request.post(url, { headers: auth, data });
    expect(r.ok(), `${url} ${await r.text()}`).toBeTruthy();
    return r.json();
  };
  const sec = await post("/api/v1/sections", { name: `vpn-e2e-${tag}` });
  const sub = await post("/api/v1/subnets", { section_id: sec.id, cidr: "192.0.2.232/29", allow_overlap: true });
  const prov = await post("/api/v1/providers", { name: `ISP-${tag}` });
  const circuit = await post("/api/v1/circuits", { cid: `WAN-${tag}`, provider_id: prov.id, status: "active" });
  await post(`/api/v1/circuits/${circuit.id}/addresses`, { target: "192.0.2.232/29" });
  const vpn = await post("/api/v1/vpn-tunnels", { name: `hq-branch-${tag}`, type: "ipsec_ikev2",
                                                 a_endpoint: "192.0.2.234", b_endpoint: "198.51.100.199" });
  try {
    await login(page);
    await page.goto("/advanced/circuits");
    await page.locator('.n-tab-pane .n-tabs-tab[data-name="vpn"]').click();
    const table = page.getByTestId("vpn-tunnel-table");
    await table.getByText(`hq-branch-${tag}`).click();
    await expect(page).toHaveURL(new RegExp(`/vpn/${vpn.id}$`));
    await expect(page.getByTestId("vpn-name")).toHaveText(`hq-branch-${tag}`);
    // A 端 192.0.2.234 在電路的 WAN 位址段裡
    const carriers = page.getByTestId("vpn-carriers");
    await expect(carriers).toContainText(`WAN-${tag}`);
    await expect(carriers).toContainText("192.0.2.234");
    // 聯絡人
    await page.getByTestId("contact-add-vpn_tunnel").click();
    await page.locator(".n-modal").getByText("建立新的聯絡人").click();
    await page.getByTestId("contact-name").locator("input").fill(`分公司IT-${tag}`);
    await page.getByTestId("contact-save").click();
    await expect(page.getByTestId("vpn-contacts")).toContainText(`分公司IT-${tag}`);
    await page.mouse.move(4, 4);
    for (const id of ["vpn-contacts", "vpn-carriers"]) {
      await page.getByTestId(id).screenshot({ path: `test-results/${id}.png` });
    }
  } finally {
    const cs = await (await request.get("/api/v1/contacts?page_size=500", { headers: auth })).json();
    for (const c of cs.items ?? []) {
      if (String(c.name).endsWith(tag)) await request.delete(`/api/v1/contacts/${c.id}`, { headers: auth });
    }
    await request.delete(`/api/v1/vpn-tunnels/${vpn.id}`, { headers: auth });
    await request.delete(`/api/v1/circuits/${circuit.id}`, { headers: auth });
    await request.delete(`/api/v1/providers/${prov.id}`, { headers: auth });
    await request.delete(`/api/v1/subnets/${sub.id}`, { headers: auth });
    await request.delete(`/api/v1/sections/${sec.id}`, { headers: auth });
  }
});
