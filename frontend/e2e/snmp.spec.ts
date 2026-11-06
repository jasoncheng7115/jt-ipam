/**
 * SNMP 收集（管理 → 外部系統整合 → SNMP，0217）的畫面：
 *   - 憑證：新增一組 v2c（畫面要警告明文）
 *   - 收集目標：替一台交換器新增（收集項目預設值跟著裝置類型：MAC 表＋鄰居＋介面，不讀 ARP）
 *   - 測試：按下去真的 POST，結果畫得出 sysName 與逐區段列數（e2e 環境沒有掃描代理 → 用路由攔截代理的回報）
 *   - 使用中的憑證刪不掉：顯示 409 的原因並列出用它的裝置
 *   - 裝置詳情的 SNMP 區塊（只在有收集目標時出現）、子網路的「對閘道啟用 SNMP 收集」
 * 建立的東西都在 finally 裡刪掉（用 API，不靠畫面）。
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS env 才能跑");

async function token(request: APIRequestContext): Promise<Record<string, string>> {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  return { Authorization: `Bearer ${(await r.json()).access_token}` };
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

/** naive-ui 的遠端搜尋選單：點開、打字、選第一個符合的選項 */
async function pickOption(page: Page, testid: string, text: string) {
  const sel = page.getByTestId(testid);
  await sel.click();
  await sel.locator("input").fill(text);
  await page.locator(".n-base-select-option", { hasText: text }).first().click();
}

async function cleanup(request: APIRequestContext, H: Record<string, string>, opts: {
  deviceIds?: string[]; credName?: string; subnetId?: string; addressIds?: string[];
}) {
  for (const id of opts.deviceIds ?? []) {
    const r = await request.get(`/api/v1/snmp/targets?device_id=${id}`, { headers: H });
    if (r.ok()) {
      for (const tg of (await r.json()).items ?? []) await request.delete(`/api/v1/snmp/targets/${tg.id}`, { headers: H });
    }
  }
  if (opts.credName) {
    const r = await request.get("/api/v1/snmp/credentials", { headers: H });
    for (const c of (await r.json()).items ?? []) {
      if (c.name === opts.credName) await request.delete(`/api/v1/snmp/credentials/${c.id}`, { headers: H });
    }
  }
  for (const id of opts.addressIds ?? []) await request.delete(`/api/v1/addresses/${id}`, { headers: H });
  for (const id of opts.deviceIds ?? []) await request.delete(`/api/v1/devices/${id}`, { headers: H });
  if (opts.subnetId) await request.delete(`/api/v1/subnets/${opts.subnetId}`, { headers: H });
}

test("憑證 → 收集目標 → 測試 → 使用中的憑證刪不掉 → 裝置詳情", async ({ page, request }) => {
  // 一整串流程，單獨跑就要 27 秒，預設 30 秒在發版 e2e 平行跑時會超過（10-22 發版時逾時一次）
  test.setTimeout(60_000);
  const H = await token(request);
  const tag = `snmp${Date.now().toString(36)}`;
  const credName = `${tag}-v2c`;
  const devRes = await request.post("/api/v1/devices", { headers: H, data: { name: `${tag}-sw`, type: "switch" } });
  expect(devRes.ok(), await devRes.text()).toBeTruthy();
  const dev = await devRes.json();
  const bareRes = await request.post("/api/v1/devices", { headers: H, data: { name: `${tag}-bare`, type: "server" } });
  const bare = await bareRes.json();
  try {
    await login(page);

    // ── 1. 新增 v2c 憑證：要看到明文警告，存檔後出現在清單 ──
    await page.goto("/snmp?tab=credentials");
    await page.getByTestId("snmp-cred-create").click();
    const credForm = page.getByTestId("snmp-credential-form");
    await expect(credForm).toBeVisible();
    await page.getByTestId("snmp-cred-name").locator("input").fill(credName);
    await credForm.getByText("SNMPv2c", { exact: true }).click();
    await expect(page.getByTestId("snmp-v2c-warning")).toContainText("明文");
    await page.getByTestId("snmp-cred-community").locator("input").fill("e2e-public-ro");
    const credSaved = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/v1/snmp/credentials"));
    await page.getByTestId("snmp-cred-save").click();
    const credBody = await (await credSaved).json();
    expect(credBody.has_community).toBe(true);
    expect(JSON.stringify(credBody)).not.toContain("e2e-public-ro");     // 機密只寫不讀
    await expect(page.getByTestId("snmp-creds-table").locator(".n-data-table-tr", { hasText: credName })).toBeVisible();

    // ── 2. 替交換器新增收集目標：預設 MAC 表＋鄰居＋介面、不讀 ARP ──
    await page.goto("/snmp");
    await page.getByTestId("snmp-target-create").click();
    await expect(page.getByTestId("snmp-target-form")).toBeVisible();
    await pickOption(page, "snmp-target-device", dev.name);
    await expect(page.getByTestId("snmp-flag-fdb")).toHaveClass(/n-checkbox--checked/);
    await expect(page.getByTestId("snmp-flag-neighbors")).toHaveClass(/n-checkbox--checked/);
    await expect(page.getByTestId("snmp-flag-arp")).not.toHaveClass(/n-checkbox--checked/);
    await pickOption(page, "snmp-target-credential", credName);
    // 這台沒有主要 IP：收集者判定不出來時要講原因（不是空白）
    await expect(page.getByTestId("snmp-collector-preview")).toContainText("主要 IP", { timeout: 10_000 });
    const targetSaved = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/v1/snmp/targets"));
    await page.getByTestId("snmp-target-save").click();
    const tgRes = await targetSaved;
    expect(tgRes.status()).toBe(201);
    const posted = tgRes.request().postDataJSON();
    expect(posted).toMatchObject({ device_id: dev.id, collect_fdb: true, collect_arp: false, collector: "auto" });
    const target = await tgRes.json();
    const row = page.getByTestId("snmp-targets-table").locator(".n-data-table-tr", { hasText: dev.name });
    await expect(row).toBeVisible({ timeout: 10_000 });

    // ── 3. 測試：真的 POST（帶選的憑證），結果畫出 sysName 與逐區段列數 ──
    const jobId = "00000000-0000-4000-8000-00000000e2e1";
    await page.route(`**/api/v1/snmp/targets/${target.id}/test`, (route) => route.fulfill({
      status: 202, json: { job_id: jobId, run_id: jobId, agent_id: jobId, agent_name: "e2e-agent", ip: "192.0.2.10",
                           expires_at: new Date(Date.now() + 180_000).toISOString() } }));
    let polls = 0;
    await page.route(`**/api/v1/snmp/jobs/${jobId}`, (route) => {
      polls += 1;
      const done = polls >= 2;
      return route.fulfill({ json: {
        job_id: jobId, kind: "snmp_test", status: done ? "done" : "pending", error: null, run: null,
        created_at: null, claimed_at: null, finished_at: null,
        result: done ? { items: [{
          credential_id: credBody.id, credential_name: credName, ok: true, elapsed_ms: 321, error: null,
          system: { sys_name: "e2e-core-sw", sys_descr: "Cisco IOS Software, C2960", sys_object_id: "1.3.6.1.4.1.9.1.1208",
                    sys_uptime: 100 * 90061 },
          sections: { fdb: { status: "ok", rows: 42 }, neighbors: { status: "ok", rows: 3 },
                      interfaces: { status: "ok", rows: 28 }, arp: { status: "absent" } },
        }] } : null,
      } });
    });
    await row.getByTestId("snmp-row-test").click();
    const dialog = page.getByTestId("snmp-test-dialog");
    await expect(dialog).toBeVisible();
    const testPosted = page.waitForRequest((r) => r.method() === "POST" && r.url().includes(`/snmp/targets/${target.id}/test`));
    await page.getByTestId("snmp-test-run").click();
    expect((await testPosted).postDataJSON()).toEqual({ credential_ids: [credBody.id] });
    await expect(page.getByTestId("snmp-sysname")).toHaveText("e2e-core-sw", { timeout: 15_000 });
    await expect(page.getByTestId("snmp-sec-fdb")).toHaveText("42 列");
    await expect(page.getByTestId("snmp-sec-arp")).toContainText("沒有這個表");
    await expect(page.getByTestId("snmp-test-ok")).toContainText("成功");
    await page.keyboard.press("Escape");

    // ── 4. 使用中的憑證：刪除被拒（409），列出用它的裝置 ──
    await page.goto("/snmp?tab=credentials");
    const credRow = page.getByTestId("snmp-creds-table").locator(".n-data-table-tr", { hasText: credName });
    await credRow.getByTestId("snmp-cred-delete").click();
    const del = page.waitForResponse((r) => r.request().method() === "DELETE" && r.url().includes("/snmp/credentials/"));
    await page.locator(".n-popconfirm__action button").last().dispatchEvent("click");
    expect((await del).status()).toBe(409);
    await expect(page.locator(".n-message").last()).toContainText("收集目標在用", { timeout: 10_000 });
    const inUse = page.getByTestId("snmp-cred-in-use");
    await expect(inUse).toBeVisible();
    await expect(inUse).toContainText(dev.name);

    // ── 5. 裝置詳情：有收集目標的顯示狀態與按鈕；沒有的顯示「啟用 SNMP 收集」 ──
    await page.goto(`/devices/${dev.id}`);
    const card = page.getByTestId("device-snmp-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText(credName);
    await expect(page.getByTestId("device-snmp-test")).toBeVisible();
    await expect(page.getByTestId("device-snmp-collect")).toBeVisible();
    // 沒有收集目標的裝置不顯示 SNMP 卡片（新增到 SNMP 頁，使用者 2026-10-16）
    await page.goto(`/devices/${bare.id}`);
    await expect(page.getByText(bare.name).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("device-snmp-card")).toHaveCount(0);
  } finally {
    await cleanup(request, H, { deviceIds: [dev.id, bare.id], credName });
  }
});

test("子網路的閘道：沒登記先講要登記，登記後一鍵帶入閘道 IP", async ({ page, request }) => {
  const H = await token(request);
  const sections = await (await request.get("/api/v1/sections?page_size=500", { headers: H })).json();
  const sectionId = (sections.items ?? sections)[0].id;
  const third = Date.now() % 250;
  const cidr = `198.18.${third}.0/28`;
  const gw = `198.18.${third}.1`;
  const subRes = await request.post("/api/v1/subnets", { headers: H, data: {
    cidr, section_id: sectionId, gateway: gw, description: "e2e-snmp-gateway", allow_overlap: true } });
  expect(subRes.ok(), await subRes.text()).toBeTruthy();
  const sub = await subRes.json();
  const addressIds: string[] = [];
  try {
    await login(page);
    await page.goto(`/subnets/${sub.id}`);
    const btn = page.getByTestId("subnet-gateway-snmp");
    await expect(btn).toContainText("對閘道啟用 SNMP 收集", { timeout: 15_000 });
    await btn.click();
    await expect(page.locator(".n-message").last()).toContainText("還沒有登記", { timeout: 10_000 });

    const ipRes = await request.post("/api/v1/addresses", { headers: H, data: {
      subnet_id: sub.id, ip: gw, hostname: "e2e-gw", state: "active" } });
    expect(ipRes.ok(), await ipRes.text()).toBeTruthy();
    addressIds.push((await ipRes.json()).id);
    await page.reload();
    await expect(btn).toBeEnabled({ timeout: 15_000 });
    await btn.click();
    await expect(page.getByTestId("snmp-target-form")).toBeVisible();
    await expect(page.getByTestId("snmp-target-device-fixed").locator("input")).toHaveValue(gw);
    await expect(page.getByTestId("snmp-target-form")).toContainText("還沒有連結裝置");
    // 沒有裝置時後端會建一台路由器 → 預設讀 ARP＋介面
    await expect(page.getByTestId("snmp-flag-arp")).toHaveClass(/n-checkbox--checked/);
    await page.keyboard.press("Escape");
  } finally {
    await cleanup(request, H, { addressIds, subnetId: sub.id });
  }
});

test("裝置清單批次指派：勾兩台、套用 → 收集項目依裝置類型", async ({ page, request }) => {
  const H = await token(request);
  const tag = `snbk${Date.now().toString(36)}`;
  const credName = `${tag}-v2c`;
  const cred = await (await request.post("/api/v1/snmp/credentials", { headers: H, data: {
    name: credName, version: "v2c", community: "bulk-e2e-ro" } })).json();
  const rtr = await (await request.post("/api/v1/devices", { headers: H, data: { name: `${tag}-rtr`, type: "router" } })).json();
  const sw = await (await request.post("/api/v1/devices", { headers: H, data: { name: `${tag}-sw`, type: "switch" } })).json();
  try {
    await login(page);
    await page.goto("/devices");
    await page.getByPlaceholder("搜尋名稱 / IP / 型號 / 序號").fill(tag);
    for (const name of [rtr.name, sw.name]) {
      const row = page.locator(".n-data-table-tr", { hasText: name });
      await expect(row).toBeVisible({ timeout: 15_000 });
      await row.locator(".n-checkbox").click();
    }
    await page.getByTestId("device-snmp-bulk").click();
    const dlg = page.getByTestId("snmp-bulk-dialog");
    await expect(dlg).toContainText("2 台裝置");
    await pickOption(page, "snmp-bulk-credential", credName);
    const posted = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/api/v1/snmp/targets/bulk"));
    await page.getByTestId("snmp-bulk-save").click();
    const body = (await posted).postDataJSON();
    expect(new Set(body.device_ids)).toEqual(new Set([rtr.id, sw.id]));
    expect(body.credential_id).toBe(cred.id);
    expect(body).not.toHaveProperty("collect_fdb");               // 依裝置類型：交給後端照類型給
    await expect(page.locator(".n-message").last()).toContainText("已新建 2 個", { timeout: 10_000 });
    const tg = async (id: string) => (await (await request.get(`/api/v1/snmp/targets?device_id=${id}`, { headers: H })).json()).items[0];
    const r = await tg(rtr.id);
    const s = await tg(sw.id);
    expect([r.collect_arp, r.collect_fdb]).toEqual([true, false]);
    expect([s.collect_arp, s.collect_fdb, s.collect_neighbors]).toEqual([false, true, true]);
  } finally {
    await cleanup(request, H, { deviceIds: [rtr.id, sw.id], credName });
  }
});
