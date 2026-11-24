/**
 * 子網路的「掃描範圍」（客戶 2026-10-11：主站台照實際遮罩設成 10.0.0.0/8，掃描直接炸掉，只好改成 /23 才活著）。
 *
 * 大子網路預設「自動」：已知位址每輪掃、其餘背景探索，子網路不必切小。畫面要說清楚每個選項會怎麼掃，
 * 存檔後後端要真的記住。用 198.19.0.0/16（測試用位址，65,534 個位址）建一個暫時的子網路，測完刪掉。
 */
import { test, expect } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
const API = process.env.E2E_API_URL || "http://127.0.0.1:8010";
const CIDR = "198.19.0.0/16";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function ok(res: any, what: string) {
  if (!res.ok()) throw new Error(`${what} 失敗 HTTP ${res.status()}：${await res.text()}`);
  return res;
}

async function auth(request: any): Promise<Record<string, string>> {
  const r = await ok(await request.post(`${API}/api/v1/auth/login`, {
    data: { username: ADMIN_USER, password: ADMIN_PASS } }), "登入");
  return { Authorization: `Bearer ${(await r.json()).access_token}` };
}

async function findSubnet(request: any, h: Record<string, string>) {
  const list = await (await ok(await request.get(`${API}/api/v1/subnets?page_size=500`, { headers: h }), "列出網段")).json();
  return (list.items ?? []).find((s: any) => String(s.cidr) === CIDR);
}

test("大子網路：掃描範圍的說明跟著選項變，存檔後記住", async ({ page, request }) => {
  const h = await auth(request);
  let sub = await findSubnet(request, h);
  if (!sub) {
    const secs = await (await ok(await request.get(`${API}/api/v1/sections?page_size=200`, { headers: h }), "列出區段")).json();
    const sec = (secs.items ?? secs).find((s: any) => s.name === "e2e 樣本區段");
    if (!sec) throw new Error("找不到 seed_e2e 的區段（先跑 seed_e2e）");
    sub = await (await ok(await request.post(`${API}/api/v1/subnets`, {
      headers: h, data: { section_id: sec.id, cidr: CIDR, description: "e2e 掃描範圍" } }), "建立子網路")).json();
  }
  // 偵測強度只在勾了作業系統或連接埠偵測時出現：先用 API 把探測設成含作業系統（勾選框依代理能力可能反灰）
  await ok(await request.patch(`${API}/api/v1/subnets/${sub.id}`, {
    headers: h, data: { scan_method: ["icmp", "os"], scan_intensity: "normal" } }), "設定探測");
  try {
    await page.goto("/login");
    await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
    await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
    await page.getByRole("button", { name: /登入/ }).click();
    await page.waitForURL((u: URL) => !u.pathname.includes("/login"));
    await page.goto(`/subnets/${sub.id}`);
    await page.getByRole("button", { name: /^編輯$/ }).first().click();

    // 掃描範圍只在啟用掃描後出現
    await expect(page.getByTestId("subnet-scan-scope")).toHaveCount(0);
    const scanToggle = page.locator(".n-form-item", { hasText: "啟用掃描" }).locator(".n-checkbox").first();
    await scanToggle.scrollIntoViewIfNeeded();
    await scanToggle.click();

    const scope = page.getByTestId("subnet-scan-scope");
    const hint = page.getByTestId("subnet-scan-scope-hint");
    await scope.scrollIntoViewIfNeeded();
    await expect(scope).toContainText("自動（建議）");
    await expect(hint).toContainText("65,534 個位址");
    await expect(hint).toContainText("不必切小");

    // 「整個子網路」：每輪 4,096 個 → 16 輪才繞完，要警告
    await scope.click();
    await page.locator(".n-base-select-option", { hasText: "整個子網路" }).click();
    await expect(hint).toContainText("要 16 輪");

    await scope.click();
    await page.locator(".n-base-select-option", { hasText: "只掃已知位址" }).click();
    await expect(hint).toContainText("不會自動發現新設備");

    // 偵測強度（客戶 2026-11-24：SEP 把一般強度的 OS 偵測當成攻擊）：預設一般，說明提到端點防護
    const intensity = page.getByTestId("subnet-scan-intensity");
    const iHint = page.getByTestId("subnet-scan-intensity-hint");
    await intensity.scrollIntoViewIfNeeded();
    await expect(intensity).toContainText("一般");
    await expect(iHint).toContainText("Symantec Endpoint Protection");
    await intensity.click();
    await page.locator(".n-base-select-option", { hasText: "對端點防護友善" }).click();
    await expect(iHint).toContainText("不做作業系統指紋");
    await expect(iHint).toContainText("1.23.0");
    await page.screenshot({ path: "test-results/subnet-scan-intensity.png" });

    // 啟用掃描一定要選代理（隨便一台 e2e 代理），然後存檔
    const agentField = page.locator(".n-form-item", { hasText: "掃描代理" }).locator(".n-select").first();
    await agentField.click();
    await page.locator(".n-base-select-option").filter({ hasText: /e2e-agent/ }).first().click();
    await page.locator(".n-modal").getByRole("button", { name: /儲存/ }).click();
    await expect(page.locator(".n-modal")).toHaveCount(0, { timeout: 15_000 });

    const saved = await (await ok(await request.get(`${API}/api/v1/subnets/${sub.id}`, { headers: h }), "讀子網路")).json();
    expect(saved.scan_scope).toBe("known");
    expect(saved.scan_intensity).toBe("gentle");
    expect(saved.scan_enabled).toBe(true);
  } finally {
    await request.delete(`${API}/api/v1/subnets/${sub.id}`, { headers: h });
  }
});
