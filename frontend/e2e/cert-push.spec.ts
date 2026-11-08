import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

/**
 * 憑證推送到防火牆（0227）：憑證頁的「推送到設備」頁籤。
 * 建一個目標（FortiGate）→ 清單顯示、帳密不出現在畫面與回應 → 編輯時帳密留空沿用 → 刪除。
 * 不會真的連設備（「立即推送」由後端測試用模擬設備驗）。
 */
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

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

test("推送到設備：新增、帳密不外露、刪除", async ({ page, request }) => {
  const H = await token(request);
  const tag = `cp${Date.now().toString(36)}`;
  const cert = await (await request.post("/api/v1/certificates", { headers: H, data: { name: `${tag}-cert`, description: "e2e" } })).json();
  await request.post(`/api/v1/certificates/${cert.id}/self-signed`, { headers: H, data: { common_name: "fw.example.com", sans: ["fw.example.com"], days: 30 } });

  await login(page);
  await page.goto("/certificates");
  // 使用者 11-03：推送到設備先標示 Beta
  await expect(page.locator(".n-tabs-tab", { hasText: "推送到設備" }).getByTestId("certpush-beta")).toHaveText("Beta");
  await page.locator(".n-tabs-tab", { hasText: "推送到設備" }).click();
  await page.getByTestId("certpush-new").click();
  await page.getByTestId("certpush-name").locator("input").fill(`${tag}-fg`);
  await page.getByTestId("certpush-cert").click();
  await page.locator(".n-base-select-option", { hasText: `${tag}-cert` }).click();
  await page.getByTestId("certpush-url").locator("input").fill("https://192.0.2.1:8443");
  await page.getByTestId("certpush-key").locator("input").fill("secret-token-should-not-show");
  const created = page.waitForResponse((r) => r.url().endsWith("/api/v1/cert-push-targets") && r.request().method() === "POST");
  await page.getByTestId("certpush-save").click();
  const resp = await created;
  expect(resp.status()).toBe(201);
  expect(await resp.text()).not.toContain("secret-token-should-not-show");

  const row = page.getByTestId("certpush-table").locator("tr", { hasText: `${tag}-fg` });
  await expect(row).toBeVisible();
  await expect(row).toContainText("FortiGate");
  await expect(row).toContainText("尚未推送");
  await expect(page.locator("body")).not.toContainText("secret-token-should-not-show");

  // 編輯：帳密欄位留空＝沿用（提示文字）
  await row.getByRole("button", { name: "編輯" }).click();
  await expect(page.getByTestId("certpush-key").locator("input")).toHaveAttribute("placeholder", /沿用/);
  await page.getByRole("button", { name: "取消" }).click();

  // 刪除
  await row.getByRole("button", { name: "刪除" }).click();
  await page.mouse.move(10, 10);                   // 滑鼠離開按鈕，「刪除」提示框才不會蓋住確認鈕（實際操作也是這樣）
  await page.locator(".n-popconfirm").getByRole("button", { name: /確認|確定|OK/ }).click();
  await expect(page.getByTestId("certpush-table").locator("tr", { hasText: `${tag}-fg` })).toHaveCount(0);
  await request.delete(`/api/v1/certificates/${cert.id}`, { headers: H });
});

test("新增 OPNsense 目標：範例跟著設備換、存檔前就能讀設備上的憑證、欄位名稱不換行（10-22）", async ({ page }) => {
  const soon = new Date(Date.now() + 10.5 * 86400000).toISOString();
  await page.route("**/api/v1/cert-push-targets/device-certs/preview", (route) =>
    route.fulfill({ json: { items: [
      { id: "u-web", name: "Web GUI", subject: "fw.example.net", in_use: true, issuer: "Local CA", valid_to: soon },
      { id: "u-old", name: "old-test", subject: "old", in_use: false, valid_to: "2020-01-01T00:00:00Z" },
    ] } }));
  await login(page);
  await page.goto("/certificates");
  await page.locator(".n-tabs-tab", { hasText: "推送到設備" }).click();
  await page.getByTestId("certpush-new").click();
  const modal = page.locator(".n-modal");
  await expect(modal).toContainText("範例：https://fw.example.net:443");
  await page.getByTestId("certpush-kind").click();
  await page.locator(".n-base-select-option", { hasText: "OPNsense" }).click();
  await expect(modal).toContainText("範例：https://opnsense.example.net");
  await expect(page.getByTestId("certpush-url").locator("input")).toHaveValue("");

  const load = page.getByTestId("certpush-opn-load");
  await expect(load).toBeDisabled();
  await page.getByTestId("certpush-url").locator("input").fill("https://192.0.2.10");
  await page.getByTestId("certpush-key").locator("input").fill("k");
  await modal.locator("input[type=password]").nth(1).fill("s");
  await expect(load).toBeEnabled();
  // 下拉選單與按鈕同高
  const sel = await page.getByTestId("certpush-opn-cert").locator(".n-base-selection").boundingBox();   // 量輸入框本身，不是外層
  const btn = await load.boundingBox();
  expect(Math.abs(sel!.height - btn!.height)).toBeLessThanOrEqual(1);
  await load.click();
  await page.getByTestId("certpush-opn-cert").click();
  // 選單每一項有狀態、簽發者、有效日（10-23），而且兩行都在自己那一格裡、不會疊到下一項
  const web = page.locator(".n-base-select-option", { hasText: "Web GUI" });
  await expect(web).toContainText("使用中");
  await expect(web).toContainText("簽發者 Local CA");
  await expect(web).toContainText("剩 10 天");
  await expect(page.locator(".n-base-select-option", { hasText: "old-test" })).toContainText("已過期");
  const boxes = await page.locator(".n-base-select-option").evaluateAll((els) => els.map((e) => {
    const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, scroll: e.scrollHeight, h: e.clientHeight };
  }));
  for (const b of boxes) expect(b.scroll).toBeLessThanOrEqual(b.h + 1);
  for (let i = 1; i < boxes.length; i++) expect(boxes[i].top).toBeGreaterThanOrEqual(boxes[i - 1].bottom - 1);
  await web.click();
  // 輸入框裡只有一行名稱，高度不變
  const picked = page.getByTestId("certpush-opn-cert").locator(".n-base-selection");
  await expect(picked).toHaveText("Web GUI（fw.example.net）");
  expect((await picked.boundingBox())!.height).toBeLessThanOrEqual(sel!.height + 1);

  // 說明文字貼著它說明的那一項，跟下一項之間才有距離（使用者 2026-11-07）
  const gaps = await modal.locator(".cp-hint").evaluateAll((els) => els.map((e) => {
    const prev = e.previousElementSibling!.getBoundingClientRect();
    const next = e.nextElementSibling?.getBoundingClientRect();
    const me = e.getBoundingClientRect();
    return { above: me.top - prev.bottom, below: next ? next.top - me.bottom : 99 };
  }));
  expect(gaps.length).toBeGreaterThan(1);
  for (const g of gaps) {
    expect(g.above, `說明離上面那一項 ${g.above}px`).toBeLessThan(10);
    expect(g.below, `說明離下一項 ${g.below}px`).toBeGreaterThan(g.above + 6);
  }
  await modal.screenshot({ path: "test-results/certpush-form-hints.png" });

  // 欄位名稱都是一行
  const tall = await modal.locator(".n-form-item-label").evaluateAll((els) =>
    els.filter((e) => e.getBoundingClientRect().height > 30).map((e) => e.textContent));
  expect(tall).toEqual([]);
});

test("pfSense 目標可以讀出設備上的憑證、標出網頁管理介面用的那張（10-23）", async ({ page }) => {
  await page.route("**/api/v1/cert-push-targets/device-certs/preview", (route) =>
    route.fulfill({ json: { items: [
      { id: "p1", name: "webConfigurator default", subject: "pfSense-abc", in_use: true, valid_to: "2030-01-01T00:00:00Z" },
      { id: "p2", name: "old-push", subject: "vpn.example.net", in_use: false, issuer: "Let's Encrypt R10", valid_to: "2020-01-01T00:00:00Z" },
    ] } }));
  await login(page);
  await page.goto("/certificates");
  await page.locator(".n-tabs-tab", { hasText: "推送到設備" }).click();
  await page.getByTestId("certpush-new").click();
  await page.getByTestId("certpush-kind").click();
  await page.locator(".n-base-select-option", { hasText: "pfSense" }).click();
  const load = page.getByTestId("certpush-pf-load");
  await expect(load).toBeDisabled();
  await page.getByTestId("certpush-url").locator("input").fill("https://192.0.2.11");
  await page.getByTestId("certpush-key").locator("input").fill("k");
  await expect(load).toBeEnabled();
  await load.click();
  const list = page.getByTestId("certpush-pf-certs");
  await expect(list).toContainText("webConfigurator default");
  await expect(list).toContainText("網頁管理介面使用中");
  await expect(list).toContainText("未使用");
  await expect(list).toContainText("已過期");
  await expect(list).toContainText("簽發者 Let's Encrypt R10");
});

test("Palo Alto 目標：commit 的說明跟著開關換、可填 partial commit 的管理員帳號（11-03 外部審查）", async ({ page }) => {
  await login(page);
  await page.goto("/certificates");
  await page.locator(".n-tabs-tab", { hasText: "推送到設備" }).click();
  await page.getByTestId("certpush-new").click();
  const modal = page.locator(".n-modal");
  await page.getByTestId("certpush-kind").click();
  await page.locator(".n-base-select-option", { hasText: "Palo Alto" }).click();
  await expect(modal).toContainText("推之前會先查有沒有別人還沒提交的變更");
  await expect(page.getByTestId("certpush-pa-commit-admin")).toBeVisible();
  await page.getByTestId("certpush-pa-commit-admin").locator("input").fill("jtipam-api");
  // 沒填確認位址只標未確認、不換回（GlobalProtect 用的憑證，管理介面本來就不會換）
  await expect(page.getByTestId("cp-verify-explicit-hint")).toContainText("不會自動換回");
  await page.mouse.move(4, 4);
  await modal.screenshot({ path: "test-results/certpush-pa-commit.png" });
  // 關掉自動 commit：說明換成「只寫進候選設定、待 commit」，管理員欄位收起來
  await modal.locator(".n-form-item", { hasText: "推完自動 commit" }).locator(".n-switch").click();
  await expect(modal).toContainText("只寫進候選設定，不會套用到線上");
  await expect(page.getByTestId("certpush-pa-commit-admin")).toHaveCount(0);
  const tall = await modal.locator(".n-form-item-label").evaluateAll((els) =>
    els.filter((e) => e.getBoundingClientRect().height > 30).map((e) => e.textContent));
  expect(tall).toEqual([]);
});

test("推送明細：沒確認到的步驟顯示警示和原因，不打勾（使用者 2026-11-07）", async ({ page }) => {
  await page.route("**/api/v1/cert-push-targets", (route) => route.request().method() !== "GET" ? route.continue()
    : route.fulfill({ json: { items: [{
      id: "t-1", name: "fw-lab-01", enabled: true, kind: "opnsense", certificate_id: "c-1", certificate_name: "example.net",
      api_url: "https://192.0.2.13", verify_tls: false, options: { cert_uuid: "u-1", restart_webgui: false },
      verify_endpoint: null, auto_push: true, has_secret: true, last_push_at: new Date().toISOString(),
      last_status: "unverified", last_error: null, last_fingerprint: "a".repeat(64), current_fingerprint: "a".repeat(64),
      up_to_date: true, last_steps: [
        { step: "read", ok: true, detail: "Web GUI TLS certificate" },
        { step: "replace", ok: true, detail: "Web GUI TLS certificate" },
        { step: "verify", ok: true, warn: true, code: "certpush_verify_no_address", detail: "service: no verify address; not verified" },
      ] }] } }));
  await login(page);
  await page.goto("/certificates");
  await page.locator(".n-tabs-tab", { hasText: "推送到設備" }).click();
  await page.locator("tr", { hasText: "fw-lab-01" }).getByText("未確認").hover();
  const steps = page.getByTestId("certpush-steps");
  await expect(steps).toBeVisible();
  const warn = steps.locator("[data-step-state=warn]");
  await expect(warn).toHaveCount(1);
  await expect(warn).toContainText("⚠");
  await expect(warn).toContainText("沒有確認位址");
  await expect(steps.locator("[data-step-state=ok]")).toHaveCount(2);
  await steps.screenshot({ path: "test-results/certpush-steps-warn.png" });
  // 表格上方的工具跟「派送代理」頁籤一樣：篩選、匯出、欄位（使用者 2026-11-07）
  const pane = page.locator(".n-tab-pane").filter({ has: page.getByTestId("certpush-table") });
  await expect(pane.getByRole("button", { name: "匯出" })).toBeVisible();
  await expect(pane.getByRole("button", { name: "欄位" })).toBeVisible();
  await page.getByTestId("certpush-filter").locator("input").fill("沒有這台");
  await expect(page.getByTestId("certpush-table").locator("tbody tr", { hasText: "fw-lab-01" })).toHaveCount(0);
  await page.getByTestId("certpush-filter").locator("input").fill("fw-lab");
  await expect(page.getByTestId("certpush-table").locator("tbody tr", { hasText: "fw-lab-01" })).toHaveCount(1);
});


test("推送中右下角顯示已完成的步驟與目前階段（11-07）", async ({ page, request }) => {
  const H = await token(request);
  const tag = `cpp${Date.now().toString(36)}`;
  const cert = await (await request.post("/api/v1/certificates", { headers: H, data: { name: `${tag}-cert`, description: "e2e" } })).json();
  await request.post(`/api/v1/certificates/${cert.id}/self-signed`, { headers: H, data: { common_name: "fw.example.com", sans: ["fw.example.com"], days: 30 } });
  const tgt = await (await request.post("/api/v1/cert-push-targets", { headers: H, data: {
    name: `${tag}-fg`, certificate_id: cert.id, kind: "fortigate", api_url: "https://192.0.2.1:8443",
    options: { bind: ["admin"] }, secret: { api_key: "k" } } })).json();
  const taskId = "00000000-0000-0000-0000-00000000c0de";
  const steps = [
    { step: "probe_before", ok: true, warn: true, code: "certpush_probe_unreachable",
      detail: "service fw.example.com:443 unreachable (DNS resolution failed for fw.example.com)" },
    { step: "import", ok: true, detail: "" },
  ];
  let polls = 0;
  await page.route(`**/api/v1/cert-push-targets/${tgt.id}/push`, (r) => r.fulfill({ json: { task_id: taskId, status: "pending", queued_at: new Date().toISOString() } }));
  await page.route(`**/api/v1/tasks/${taskId}`, (r) => {
    polls += 1;
    const base = { id: taskId, kind: "cert.push", target_label: `${tag}-fg`, trigger: "manual",
      queued_at: new Date().toISOString(), started_at: new Date().toISOString() };
    return r.fulfill({ json: polls < 4
      ? { ...base, status: "running", progress: 0, summary: { target: `${tag}-fg`, steps, stage: "push" } }
      : { ...base, status: "succeeded", progress: 100, finished_at: new Date().toISOString(),
          summary: { target: `${tag}-fg`, status: "ok", error: null, steps } } });
  });
  await login(page);
  await page.goto("/certificates");
  await page.locator(".n-tabs-tab", { hasText: "推送到設備" }).click();
  const row = page.getByTestId("certpush-table").locator("tr", { hasText: `${tag}-fg` });
  await row.getByRole("button", { name: "立即推送" }).click();
  const tracker = page.getByTestId("task-tracker");
  await expect(tracker.getByTestId("task-tracker-stage")).toContainText("目前：推送到設備");
  const lines = tracker.getByTestId("task-tracker-steps");
  await expect(lines).toContainText("⚠ 推之前先連一次");
  await expect(lines).toContainText("DNS resolution failed");
  await expect(lines).toContainText("✓ 匯入憑證");
  await tracker.screenshot({ path: "test-results/certpush-progress.png" });
  await expect(tracker.getByTestId("task-tracker-stage")).toHaveCount(0, { timeout: 15_000 });   // 做完就沒有「目前」
  await request.delete(`/api/v1/cert-push-targets/${tgt.id}`, { headers: H });
});
