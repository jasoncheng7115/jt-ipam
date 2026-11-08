import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

/**
 * 派送代理的安裝指令不再預設關掉 TLS 驗證（2026-11-04 使用者問「有人偽造自己是 jt-ipam 憑證 server」）。
 * - 讀得到 jt-ipam 出示的憑證時預設釘選；讀不到（這裡的 e2e 是 http）預設用系統 CA 驗證、釘選反灰
 * - 「不驗證」才帶 -k / JT_IPAM_INSECURE，並有紅字警告
 * - 代理清單的「伺服器驗證」依代理回報顯示：釘選、未驗證（舊版）
 */
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function headers(request: APIRequestContext): Promise<Record<string, string>> {
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

test("安裝指令預設驗證伺服器、不驗證要明講；清單顯示每個代理怎麼驗證", async ({ page, request }) => {
  const H = await headers(request);
  const tag = `trust-${Date.now().toString(36)}`;
  const pinned = await (await request.post("/api/v1/cert-agents", { headers: H, data: { name: `${tag}-pinned` } })).json();
  const legacy = await (await request.post("/api/v1/cert-agents", { headers: H, data: { name: `${tag}-legacy` } })).json();
  try {
    await request.get("/api/v1/cert-agents/check", { headers: { "X-Agent-Key": pinned.enroll_key, "X-Agent-Tls": "pinned" } });
    await request.get("/api/v1/cert-agents/check", { headers: { "X-Agent-Key": legacy.enroll_key } });

    await login(page);
    await page.goto("/certificates");
    await page.locator(".n-tabs-tab", { hasText: "派送代理" }).click();
    await page.getByPlaceholder(/篩選名稱 \/ IP/).fill(tag);
    await expect(page.locator("tr", { hasText: `${tag}-pinned` }).getByTestId("cert-agent-tls")).toHaveText("釘選");
    await expect(page.locator("tr", { hasText: `${tag}-legacy` }).getByTestId("cert-agent-tls")).toHaveText("未驗證（舊版）");

    // 「未驗證」標籤本身就是入口：點了打開那台的安裝指令（使用者 2026-11-07：「未驗證，但沒地方啟用」）
    const tlsTag = page.locator("tr", { hasText: `${tag}-legacy` }).getByTestId("cert-agent-tls");
    await expect(tlsTag).toHaveAttribute("data-fixable", "1");
    await tlsTag.click();
    const dlg = page.locator(".n-modal").last();
    const cmd = dlg.getByTestId("cert-install-oneliner");
    // e2e 走 http：讀不到伺服器憑證 → 釘選反灰、預設系統 CA 驗證，指令不帶 -k
    await expect(dlg.getByTestId("cert-trust-hint")).toContainText("讀不到這台 jt-ipam 出示的憑證");
    await expect(dlg.locator(".n-radio-button", { hasText: "釘選這台的憑證" })).toHaveClass(/disabled/);
    await expect(cmd).toContainText("curl -fsSL ");
    await expect(cmd).not.toContainText("JT_IPAM_INSECURE");
    await expect(cmd).not.toContainText("-fsSLk");
    // 要不驗證得自己選，而且有警告
    await dlg.locator(".n-radio-button", { hasText: "不驗證" }).click();
    await expect(cmd).toContainText("-fsSLk");
    await expect(cmd).toContainText("JT_IPAM_INSECURE=1");
    await expect(dlg.getByTestId("cert-trust-hint")).toContainText("能攔截網路的人可以假冒 jt-ipam");
  } finally {
    for (const a of [pinned, legacy]) await request.delete(`/api/v1/cert-agents/${a.id}`, { headers: H });
  }
});

test("Windows 釘選指令用 C# 驗證（PowerShell 5.1 的腳本區塊回呼會失敗）、畫面上保有換行", async ({ page, request }) => {
  const H = await headers(request);
  const FP = "0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF";
  const agent = await (await request.post("/api/v1/cert-agents", { headers: H, data: { name: `trust-win-${Date.now().toString(36)}` } })).json();
  try {
    // e2e 是 http，假裝讀得到自簽憑證
    await page.route("**/api/v1/cert-agents/server-identity**", (r) => r.fulfill({ json: {
      available: true, ca_trusted: false, chain_valid: false, spki_pin: "sha256//dummy", cert_sha256: FP } }));
    await login(page);
    await page.goto("/certificates");
    await page.locator(".n-tabs-tab", { hasText: "派送代理" }).click();
    await page.getByRole("button", { name: "安裝說明" }).click();
    await page.locator(".n-modal").last().locator(".n-radio-button", { hasText: "Windows" }).first().click();
    await page.keyboard.press("Escape");
    await page.getByPlaceholder(/篩選名稱 \/ IP/).fill(agent.name);
    await page.locator("tr", { hasText: agent.name }).getByRole("button", { name: "檢視代理連線 Key 與安裝指令" }).click();
    const cmd = page.locator(".n-modal").last().getByTestId("cert-install-oneliner");
    await expect(cmd).toContainText(`$env:JT_IPAM_CERT_SHA256='${FP}'`);
    await expect(cmd).toContainText("Add-Type");
    await expect(cmd).toContainText("[Delegate]::CreateDelegate([Net.Security.RemoteCertificateValidationCallback],[JtPin].GetMethod('Check'))");
    await expect(cmd).not.toContainText("ServerCertificateValidationCallback={");
    // 手動反白複製也要是可執行的多行指令
    expect((await cmd.innerText()).split("\n").length).toBeGreaterThan(3);
  } finally {
    await request.delete(`/api/v1/cert-agents/${agent.id}`, { headers: H });
  }
});

test("掃描代理與 RustDesk 代理：同一套伺服器驗證選擇，清單標出怎麼驗證", async ({ page, request }) => {
  const H = await headers(request);
  const tag = `trust-${Date.now().toString(36)}`;
  const scan = await (await request.post("/api/v1/scan-agents", { headers: H, data: { name: `${tag}-scan` } })).json();
  const rd = await (await request.post("/api/v1/rustdesk/servers", { headers: H, data: {
    name: `${tag}-rd`, report_interval_seconds: 120, client_address: "rd.example.net" } })).json();
  try {
    await request.get("/api/v1/scan-agents/poll", { headers: { "X-Agent-Key": scan.enroll_key, "X-Agent-Tls": "pinned" } });
    await request.post("/api/v1/rustdesk/agent/poll", { headers: { "X-Agent-Key": rd.agent_key }, data: { version: "1.2.0" } });
    // e2e 是 http，假裝讀得到自簽憑證
    await page.route("**/api/v1/cert-agents/server-identity**", (r) => r.fulfill({ json: {
      available: true, ca_trusted: false, chain_valid: false, spki_pin: "sha256//dummyPin=", cert_sha256: "AB".repeat(32) } }));
    await login(page);

    await page.goto("/scan-agents");
    await page.getByPlaceholder("篩選").fill(tag);
    await expect(page.locator("tr", { hasText: `${tag}-scan` }).getByTestId("scan-agent-tls")).toHaveText("釘選");
    await page.getByRole("button", { name: "安裝說明" }).click();
    const help = page.locator(".n-modal").last();
    await expect(help.getByTestId("scan-trust-hint")).toContainText("只接受憑證指紋");
    await expect(help).toContainText("--pinnedpubkey 'sha256//dummyPin='");
    await expect(help).toContainText("JT_IPAM_PIN='sha256//dummyPin='");
    await expect(help).not.toContainText("JT_IPAM_INSECURE=1");
    // 已安裝的主機那行不帶金鑰（安裝程式沿用設定檔）
    await expect(help.getByTestId("scan-reinstall-oneliner")).not.toContainText("JT_IPAM_AGENT_KEY");
    // 改用系統 CA：明講 JT_IPAM_INSECURE=（空值），重跑時舊指紋會被清掉
    await help.locator(".n-radio-button", { hasText: "用系統 CA 驗證" }).click();
    await expect(help.getByTestId("scan-reinstall-oneliner")).toContainText("JT_IPAM_INSECURE= bash");
    await expect(help.getByTestId("scan-reinstall-oneliner")).not.toContainText("pinnedpubkey");
    await page.keyboard.press("Escape");

    await page.goto("/rustdesk");
    const row = page.getByTestId("rustdesk-servers").locator("tr", { hasText: `${tag}-rd` });
    await expect(row.getByTestId("rustdesk-agent-tls")).toHaveText("未驗證（舊版）");
    await row.getByTestId("rustdesk-install").click();
    const install = page.getByTestId("rustdesk-install-modal");
    await expect(install.getByTestId("rustdesk-install-cmd")).toContainText("--pinnedpubkey 'sha256//dummyPin='");
    await expect(install.getByTestId("rustdesk-install-cmd")).toContainText("JT_IPAM_PIN='sha256//dummyPin='");
    await expect(install.getByTestId("rustdesk-install-cmd")).not.toContainText("JT_IPAM_INSECURE=1");
  } finally {
    await request.delete(`/api/v1/scan-agents/${scan.id}`, { headers: H });
    await request.delete(`/api/v1/rustdesk/servers/${rd.id}`, { headers: H });
  }
});
