/**
 * 多 LAN 路由器的閘道不回 ping 卻在用（客戶 2026-10-14）：
 *   - IP 詳細頁：同 MAC 的另一個位址在線 → 提示「可能是同一台設備的介面位址」，一鍵掛到同一台設備並勾「上線狀態跟著設備」
 *   - 編輯表單：開關、沒掛設備時的提醒
 *   - 燈號：勾了沒掛設備 → 灰色「使用中（不回應探測）」，不是紅燈
 *   - 系統設定的上線判定：列出「跟著所屬設備」
 * 在線的那個位址用路由攔截（e2e 環境沒有掃描代理，蓋不了探測時間）；套用是真的存檔。
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function token(request: APIRequestContext): Promise<Record<string, string>> {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  return { Authorization: `Bearer ${(await r.json()).access_token}` };
}

/** 挑一個這個子網路裡還沒用的位址：隨機挑會撞到樣本或之前留下的位址（409，後面整個失敗） */
async function freeOctet(request: APIRequestContext, H: Record<string, string>, subnetId: string, from: number, to: number) {
  const r = await (await request.get(`/api/v1/addresses?subnet_id=${subnetId}&page_size=1000`, { headers: H })).json();
  const used = new Set((r.items ?? []).map((a: any) => Number(String(a.ip).split(".")[3])));
  for (let o = from; o <= to; o++) if (!used.has(o)) return o;
  throw new Error(`10.20.0.${from}-${to} 都被用掉了`);
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("同 MAC 的位址在線 → 提示並一鍵掛到同一台設備、勾「上線狀態跟著設備」", async ({ page, request }) => {
  const H = await token(request);
  const tag = `gw${Date.now().toString(36)}`;
  const subs = await (await request.get("/api/v1/subnets?page_size=500", { headers: H })).json();
  const sub = subs.items.find((s: any) => s.cidr === "10.20.0.0/24");
  const dev = await (await request.post("/api/v1/devices", { headers: H, data: { name: `${tag}-rtr`, type: "router" } })).json();
  // 刪掉的位址會進冷卻期（重跑時可能抽到上一輪用過的）→ 換一個再試
  let ipRes = null as Awaited<ReturnType<typeof request.post>> | null;
  for (let i = 0; i < 10; i += 1) {
    const octet = await freeOctet(request, H, sub.id, 150, 229);
    ipRes = await request.post("/api/v1/addresses", { headers: H, data: {
      subnet_id: sub.id, ip: `10.20.0.${octet}`, hostname: `${tag}-gw`, mac: "02:00:5e:42:00:01", state: "active" } });
    if (ipRes.ok() || !(await ipRes.text()).includes("ip_in_cooldown")) break;
  }
  if (!ipRes) throw new Error("unreachable");
  expect(ipRes.ok(), await ipRes.text()).toBeTruthy();
  const gw = await ipRes.json();
  try {
    const now = new Date().toISOString();
    await page.route(`**/api/v1/addresses/${gw.id}/same-mac`, (route) => route.fulfill({ json: [{
      ...gw, id: "00000000-0000-4000-8000-0000000000a1", ip: "192.0.2.1", device_id: dev.id,
      last_seen_scanner: now, exclude_from_ping: false, liveness_from_device: false }] }));
    await login(page);
    await page.goto(`/addresses/${gw.id}`);
    const hint = page.getByTestId("ip-same-mac-hint");
    await expect(hint).toBeVisible({ timeout: 15_000 });
    await expect(hint).toContainText("192.0.2.1");
    await expect(hint).toContainText("同一個 MAC");
    const saved = page.waitForResponse((r) => r.request().method() === "PATCH" && r.url().includes(`/addresses/${gw.id}`));
    await page.getByTestId("ip-same-mac-apply").click();
    const res = await saved;
    expect(res.request().postDataJSON()).toEqual({ liveness_from_device: true, device_id: dev.id });
    const body = await res.json();
    expect(body.liveness_from_device).toBe(true);
    expect(body.device_id).toBe(dev.id);
    await expect(hint).toHaveCount(0, { timeout: 10_000 });
    await expect(page.getByTestId("ip-liveness-from-device-row")).toBeVisible();
  } finally {
    await request.delete(`/api/v1/addresses/${gw.id}`, { headers: H });
    await request.delete(`/api/v1/devices/${dev.id}`, { headers: H });
  }
});

test("勾了沒掛設備：表單提醒、燈號是灰色「使用中（不回應探測）」", async ({ page, request }) => {
  const H = await token(request);
  const tag = `gx${Date.now().toString(36)}`;
  const subs = await (await request.get("/api/v1/subnets?page_size=500", { headers: H })).json();
  const sub = subs.items.find((s: any) => s.cidr === "10.20.0.0/24");
  const octet = await freeOctet(request, H, sub.id, 100, 149);
  const gw = await (await request.post("/api/v1/addresses", { headers: H, data: {
    subnet_id: sub.id, ip: `10.20.0.${octet}`, hostname: `${tag}-gw`, state: "active" } })).json();
  try {
    await login(page);
    await page.goto(`/addresses/${gw.id}`);
    await page.getByRole("button", { name: "編輯" }).first().click();
    const sw = page.getByTestId("ip-liveness-from-device");
    await expect(sw).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("ip-liveness-no-device")).toHaveCount(0);
    await sw.click();
    await expect(page.getByTestId("ip-liveness-no-device")).toContainText("還沒有掛設備");
    const saved = page.waitForResponse((r) => r.request().method() === "PATCH" && r.url().includes(`/addresses/${gw.id}`));
    await page.getByRole("button", { name: /儲存/ }).last().click();
    expect((await saved).request().postDataJSON().liveness_from_device).toBe(true);
    await expect(page.getByTestId("ip-liveness-from-device-row")).toContainText("還沒有掛設備");
    // 燈號：灰色＋說明（不是紅燈）
    const dot = page.locator(".live-dot").first();
    await expect(dot).toBeVisible();
    await dot.hover();
    await expect(page.locator(".live-dot-tip")).toContainText("使用中（不回應探測）");
    await expect(page.getByTestId("live-dot-silent")).toBeVisible();
    const bg = await dot.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bg).not.toBe("rgb(239, 68, 68)");
  } finally {
    await request.delete(`/api/v1/addresses/${gw.id}`, { headers: H });
  }
});

test("系統設定的上線判定列出「跟著所屬設備」", async ({ page }) => {
  await login(page);
  await page.goto("/system-settings");
  await expect(page.getByText("跟著所屬設備").first()).toBeVisible({ timeout: 20_000 });
});
