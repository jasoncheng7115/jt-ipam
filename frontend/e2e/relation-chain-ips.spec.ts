/**
 * 關係圖的 IP 卡片（客戶 11-02）：以前點下去是拿第一個 IP 去 IP 清單模糊搜尋，198.51.100.21 會連 .214 一起出來，
 * 也看不到另一個網段（iDRAC 管理網段）的 IP。現在：裝置有好幾個 IP → 跳出這台所有的 IP（含網段），點了進那個 IP；
 * 只有一個 IP → 直接進那個 IP 的頁面。資料都是這支測試建的，結束時刪掉。
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

test("關係圖 IP 卡片：列出裝置所有 IP（跨網段），點了進那個 IP；只有一個 IP 直接進去", async ({ page, request }) => {
  const auth = { Authorization: `Bearer ${await token(request)}` };
  const tag = Date.now().toString(36);
  const post = async (url: string, data: unknown) => {
    const r = await request.post(url, { headers: auth, data });
    expect(r.ok(), `${url} ${await r.text()}`).toBeTruthy();
    return r.json();
  };
  const sec = await post("/api/v1/sections", { name: `rel-e2e-${tag}` });
  const lan = await post("/api/v1/subnets", { section_id: sec.id, cidr: "198.51.100.0/26", allow_overlap: true });
  const mgmt = await post("/api/v1/subnets", { section_id: sec.id, cidr: "203.0.113.0/26", description: "management", allow_overlap: true });
  const dev = await post("/api/v1/devices", { name: `rel-srv-${tag}`, type: "server" });
  const one = await post("/api/v1/devices", { name: `rel-one-${tag}`, type: "server" });
  try {
    const osIp = await post("/api/v1/addresses", { subnet_id: lan.id, ip: "198.51.100.21", hostname: `srv-${tag}.example.local`, device_id: dev.id });
    const bmc = await post("/api/v1/addresses", { subnet_id: mgmt.id, ip: "203.0.113.21", hostname: `idrac-${tag}.example.local`, device_id: dev.id });
    await post("/api/v1/addresses", { subnet_id: lan.id, ip: "198.51.100.24", hostname: "not-this-device" });
    const solo = await post("/api/v1/addresses", { subnet_id: lan.id, ip: "198.51.100.30", device_id: one.id });

    await login(page);
    await page.goto(`/devices/${dev.id}`);
    const card = page.getByTestId("rel-ip");
    await expect(card).toContainText("198.51.100.21", { timeout: 20_000 });   // 關係圖在裝置資料之後才載入
    await expect(card).toContainText("另 1 個 IP");
    await card.click();
    const list = page.getByTestId("rel-ip-list");
    await expect(list).toBeVisible();
    const rows = list.getByTestId("rel-ip-row");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("198.51.100.21");
    await expect(rows.nth(0)).toContainText("198.51.100.0/26");
    await expect(rows.nth(1)).toContainText("203.0.113.21");
    await expect(rows.nth(1)).toContainText(`idrac-${tag}.example.local`);
    await expect(rows.nth(1)).toContainText("203.0.113.0/26");
    await expect(list).not.toContainText("198.51.100.24");
    await page.mouse.move(4, 4);
    await page.locator(".rel-chain").screenshot({ path: "test-results/rel-chain-ips.png" });
    await list.screenshot({ path: "test-results/rel-chain-ip-list.png" });
    await rows.nth(1).click();
    await expect(page).toHaveURL(new RegExp(`/addresses/${bmc.id}$`));
    expect(osIp.id).toBeTruthy();

    // 只有一個 IP：直接進那個 IP 的頁面，不是去清單搜尋
    await page.goto(`/devices/${one.id}`);
    await expect(page.getByTestId("rel-ip")).toContainText("198.51.100.30", { timeout: 20_000 });
    await expect(page.getByTestId("rel-more")).toHaveCount(0);
    await page.getByTestId("rel-ip").click();
    await expect(page).toHaveURL(new RegExp(`/addresses/${solo.id}$`));
  } finally {
    await request.delete(`/api/v1/devices/${dev.id}`, { headers: auth });
    await request.delete(`/api/v1/devices/${one.id}`, { headers: auth });
    await request.delete(`/api/v1/subnets/${lan.id}`, { headers: auth });
    await request.delete(`/api/v1/subnets/${mgmt.id}`, { headers: auth });
    await request.delete(`/api/v1/sections/${sec.id}`, { headers: auth });
  }
});
