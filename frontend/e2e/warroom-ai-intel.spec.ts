/**
 * 戰情室 12 AI 情報（2026-10-26，設計稿 v1.2）：
 * - 三區與專注兩種版面不溢出（1920×1080、3840×2160，中英日），長 IPv6 / CIDR 不被拆開或截斷
 * - 逐字播放：字一個一個出來；整個戰情室暫停時打字也停並標「播放已暫停」
 * - 局部 WARNING：只在右上那一塊（專注模式是標題下那一列），正文照播
 * - 設定在原本的戰情室設定頁（使用者指示）：預覽用示範資料、自訂配色對比不足會提示並可一鍵修正、存檔會送出 intel、
 *   「立即產生」冷卻中會講明要等幾秒
 * 資料用攔截的固定內容（形狀與 backend/app/services/warroom/intel.py 回的一致）；產生、權限、驗證在 backend/tests/test_warroom_intel.py。
 */
import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
const SHOTS = process.env.E2E_SHOT_DIR || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.use({ viewport: { width: 1920, height: 1080 } });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

const M = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
const DISPLAY = {
  interval_minutes: 5, dwell_seconds: 300, layout: "three_panel", playback: "rotate", item_limit: 8, domains: [],
  speed: "fast", rhythm: true, cursor: true, effects: "standard", scanlines: "low", noise: "off", palette: "phosphor",
  colors: { bg: "#0a1012", text: "#dce8e4", secondary: "#a8a3ce", accent: "#88ebc1", warn: "#e9b861", critical: "#f07866" },
  mask_ips: false, warning_enabled: true, warning_breathe: true,
};

async function pinConfig(page: Page, language: string, over: Record<string, unknown> = {}, reduce = true) {
  await page.route(/\/api\/v1\/warroom\/config$/, async (route) => {
    const cfg = await (await route.fetch()).json();
    await route.fulfill({ json: { ...cfg, pages: ["overview", "ai_intel"], language, reduce_motion: reduce,
                                  dwell_seconds: 600, intel: { ...DISPLAY, ...over } } });
  });
}

/** 模型寫的內容（有 text）＋系統摘要（code＋params）混在一批，最長的那則故意塞長 IPv6 與長名稱 */
function batch(lang: string) {
  const zh = lang === "zh-TW" || lang === "ja-JP";
  const items = [
    { id: "i1", fact_ids: ["F1"], category: "overview", state: "info", code: "overview",
      params: { at: M(1), registered_ips: 128_734, open_anomalies: 47 } },
    { id: "i2", fact_ids: ["E1"], category: "events", state: "new",
      text: zh ? "自 08:15 以來新增 2 項異常：2001:db8:abcd:12:0:0:0:1a2b 與 198.51.100.80 出現 IP 衝突，finance-application-server-02.corp.tenant-a.example.com 的 MAC 與上次不同，建議優先確認交換器連接埠。"
               : "Since 08:15, 2 anomalies opened: IP conflicts on 2001:db8:abcd:12:0:0:0:1a2b and 198.51.100.80, and finance-application-server-02.corp.tenant-a.example.com changed its MAC; check the switch ports first." },
    { id: "i3", fact_ids: ["G1"], category: "capacity", state: "watch", code: "capacity",
      params: { high_util: 3, warn: 80, top_label: "203.0.113.0/24", top_pct: 97, pools_alert: 1 } },
    { id: "i4", fact_ids: ["G2"], category: "integrity", state: "watch", code: "integrity",
      params: { total: 9, stale: 2, failed: 1, never: 0, examples: [
        { name: "Kea DHCP", state: "stale", last_success_at: M(140) }, { name: "Zabbix", state: "failed", last_success_at: null }] } },
  ];
  return {
    id: "b1", status: "ok", origin: "model", model: "gemma4:26b", error_code: null, baseline: false,
    snapshot_at: M(1), generated_at: M(1), checked_at: M(1), count: items.length, items, stats: {},
    pinned: [
      { id: "p1", kind: "anomaly", category: "ip_conflicts", label: "", ip: "2001:db8:abcd:12:0:0:0:1a2b", state: "open" },
      { id: "p2", kind: "source", label: "Kea DHCP", ip: null, state: "stale" },
      { id: "p3", kind: "source", label: "Zabbix", ip: null, state: "failed" },
    ],
    sources: [
      ["scan", "Scanner", 2, "fresh"], ["kea", "Kea DHCP", 140, "stale"], ["zbx", "Zabbix", null, "failed"],
      ["lnms", "LibreNMS (core-datacenter-taipei-north)", 5, "fresh"], ["pve", "Proxmox VE", 9, "fresh"],
      ["dns", "Technitium DNS", 4, "fresh"], ["ocs", "OCS Inventory NG", 30, "partial"],
    ].map(([id, name, m, state]) => ({ id, name, kind: id, label: name, last_success_at: m == null ? null : M(m as number), state })),
  };
}

async function mockIntel(page: Page, lang: string, events: unknown[] = []) {
  await page.route(/\/api\/v1\/warroom\/intel(\?|$)/, (route) => route.fulfill({ json: {
    ai_enabled: true, generating: false, next_due_at: new Date(Date.now() + 4 * 60_000).toISOString(),
    last_error_code: null, last_attempt_at: M(1), batch: batch(lang), stale: false, outdated: false } }));
  await page.route(/\/api\/v1\/warroom\/intel\/events/, (route) => route.fulfill({ json: {
    state: "ok", items: events, checked_at: M(0), detection_stale: false } }));
  await page.route(/\/api\/v1\/warroom\/pages\/ai_intel/, (route) => route.fulfill({ json: {
    key: "ai_intel", data_at: M(1), page: 1, pages: 1, metrics: { open_anomalies: 47, watch: 3, stale_sources: 3 } } }));
}

const EVENT = { id: "e1", category: "ip_conflicts", label: "", ip: "2001:db8:abcd:12:0:0:0:1a2b", first_seen_at: M(2),
                baseline: false, resolved_at: null, state: "open",
                where: { kind: "port", switch: "core-sw-01", port: "Gi1/0/12", vlan: 20 } };

async function open(page: Page) {
  await page.goto("/warroom");
  await expect(page.locator(".wr-title h1")).toBeVisible({ timeout: 20_000 });
  await page.keyboard.press("2");
  await expect(page.getByTestId("wr-intel")).toBeVisible();
}

async function overflowing(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const w = document.querySelector(".wr") as HTMLElement;
    const r = w.getBoundingClientRect();
    return [...w.querySelectorAll<HTMLElement>("*")]
      .filter((e) => { const b = e.getBoundingClientRect(); return b.width && (b.right > r.right + 1 || b.bottom > r.bottom + 1); })
      .slice(0, 5).map((e) => `${e.tagName}.${e.className}`);
  });
}

/** 識別碼整塊：IP 的 span 沒有被裁掉、也沒有斷成兩行 */
async function brokenAtoms(page: Page): Promise<string[]> {
  return page.evaluate(() => [...document.querySelectorAll<HTMLElement>("[data-testid=wr-intel] .ai-atom")]
    .filter((e) => e.scrollWidth > e.clientWidth + 1 || e.getClientRects().length > 1).map((e) => e.textContent || ""));
}

for (const lang of ["zh-TW", "en-US", "ja-JP"]) {
  for (const layout of ["three_panel", "focus"]) {
    for (const [w, h] of (lang === "zh-TW" && layout === "three_panel" ? [[1920, 1080], [3840, 2160]] : [[1920, 1080]]) as [number, number][]) {
      test(`AI 情報版面不溢出、識別碼不拆（${lang} ${layout} ${w}×${h}）`, async ({ page }) => {
        const errors: string[] = [];
        page.on("pageerror", (e) => errors.push(e.message));
        await page.setViewportSize({ width: w, height: h });
        await pinConfig(page, lang, { layout });
        await mockIntel(page, lang, [EVENT]);
        await login(page);
        await open(page);
        // 減少動態：整段直接顯示
        await expect(page.getByTestId("wr-intel-current")).toContainText(lang === "en-US" ? "128,734 IPs are registered" : "128,734");
        if (layout === "three_panel") {
          await expect(page.getByTestId("wr-intel-warning")).toBeVisible();
          await expect(page.getByTestId("wr-intel-watch")).toContainText("WARNING");
          await expect(page.getByTestId("wr-intel-sources")).toContainText("7");
        } else {
          await expect(page.getByTestId("wr-intel-strip")).toContainText("WARNING");
          // 警示列高度不超過內容區 8%（設計稿第 14 節）
          const ratio = await page.evaluate(() => {
            const s = document.querySelector("[data-testid=wr-intel-strip]")!.getBoundingClientRect();
            const a = document.querySelector("[data-testid=wr-intel]")!.getBoundingClientRect();
            return s.height / a.height;
          });
          expect(ratio).toBeLessThanOrEqual(0.08);
        }
        expect(await overflowing(page)).toEqual([]);
        expect(await brokenAtoms(page)).toEqual([]);
        // 區塊內的列不被自己的框裁掉（來源一頁放幾列要放得下）
        const cut = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>("[data-testid=wr-intel] .ai-row, [data-testid=wr-intel-warning] > div")]
          .filter((e) => {
            const panel = e.closest(".ai-panel")!.getBoundingClientRect();
            return e.getBoundingClientRect().bottom > panel.bottom - 4;
          }).map((e) => e.textContent || ""));
        expect(cut).toEqual([]);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/ai-intel-${lang}-${layout}-${w}.png` });
        expect(errors).toEqual([]);
      });
    }
  }
}

test("逐字播放：字一個一個出來；空白鍵暫停時停住並標示", async ({ page }) => {
  await pinConfig(page, "zh-TW", { speed: "slow" }, false);
  await mockIntel(page, "zh-TW");
  await login(page);
  await open(page);
  const cur = page.getByTestId("wr-intel-current").locator(".ai-tx");
  await expect.poll(async () => (await cur.innerText()).length, { timeout: 5000 }).toBeGreaterThan(3);
  const a = (await cur.innerText()).length;
  await page.waitForTimeout(600);
  const b = (await cur.innerText()).length;
  expect(b).toBeGreaterThan(a);
  await page.keyboard.press(" ");
  await expect(page.getByTestId("wr-intel-meta")).toContainText("播放已暫停");
  const c = (await cur.innerText()).length;
  await page.waitForTimeout(800);
  expect((await cur.innerText()).length).toBe(c);
  await page.keyboard.press(" ");
  await page.waitForTimeout(800);
  expect((await cur.innerText()).length).toBeGreaterThan(c);
});

test("沒有重大事件時右上是持續關注；有事件時只有那一塊變 WARNING，正文照播", async ({ page }) => {
  await pinConfig(page, "zh-TW");
  await mockIntel(page, "zh-TW");
  await login(page);
  await open(page);
  await expect(page.getByTestId("wr-intel-watch")).toContainText("持續關注");
  await expect(page.getByTestId("wr-intel-watch")).not.toContainText("WARNING");
  await expect(page.getByTestId("wr-intel-sources")).toContainText("Kea DHCP");
  await expect(page.getByTestId("wr-intel-current")).toBeVisible();
});

test("10-26 回饋：底列拿掉、資訊移到標題列；WARNING 持續呼吸燈、停止閃動；按鍵說明蓋在最上面；效果蓋整個畫面", async ({ page }) => {
  await pinConfig(page, "zh-TW", { effects: "cinema", scanlines: "medium", noise: "low" }, false);
  await mockIntel(page, "zh-TW", [EVENT]);
  await login(page);
  await open(page);
  // 沒有底列；標題列也不寫序號、摘要時間、下次更新（時間在每一則左邊，使用者 10-26：多餘）
  await expect(page.locator("[data-testid=wr-intel] .ai-bar")).toHaveCount(0);
  await expect(page.getByTestId("wr-intel-meta")).not.toContainText("播報");
  await expect(page.getByTestId("wr-intel-meta")).not.toContainText("下次更新");
  // 正常的 AI 摘要不多寫狀態
  await expect(page.getByTestId("wr-intel-flag")).toHaveCount(0);
  // 呼吸燈：有動畫，而且不會 20 秒後停
  const watch = page.getByTestId("wr-intel-watch");
  await expect(watch).toHaveClass(/breathe/);
  expect(await watch.evaluate((e) => getComputedStyle(e).animationName)).toMatch(/^ai-breathe/);
  // 畫面上沒有「停止閃動」（10-26 使用者：拿掉；要停就到設定關呼吸燈）；也沒有 CH 編號
  await expect(page.getByTestId("wr-intel-stop")).toHaveCount(0);
  await expect(page.getByTestId("wr-intel")).not.toContainText("CH-0");
  // 效果層蓋整個畫布（頁首、頁尾也在裡面）
  const fx = page.getByTestId("wr-fx");
  await expect(fx).toHaveClass(/fx-cinema/);
  const [fb, wb] = await Promise.all([fx.boundingBox(), page.locator(".wr").boundingBox()]);
  expect(Math.abs(fb!.height - wb!.height)).toBeLessThan(2);
  expect(Math.abs(fb!.y - wb!.y)).toBeLessThan(2);
  // 按 H：說明在最上層，點說明中央拿到的是說明本身（以前被內容壓住、字疊在一起）
  await page.keyboard.press("h");
  const help = page.getByTestId("wr-help");
  await expect(help).toBeVisible();
  const onTop = await help.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!hit && el.contains(hit);
  });
  expect(onTop).toBe(true);
  await page.keyboard.press("Escape");
  // P 釘選：只有一頁的主題，底下進度條改成來回掃描
  // 輪播中的 AI 情報不倒數（等播完一輪），底線是掃描光點
  await expect(page.getByTestId("wr-progress")).toHaveAttribute("data-mode", "briefing");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/ai-intel-1026-fx.png` });
  await page.keyboard.press("p");
  await expect(page.getByTestId("wr-progress")).toHaveAttribute("data-mode", "sweep");
  await page.waitForTimeout(1500);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/ai-intel-1026.png` });
  await page.keyboard.press("p");
  await expect(page.getByTestId("wr-progress")).toHaveAttribute("data-mode", "briefing");
});

test("第一批還沒產生：指標寫沒有結果（不是無權限），序號不寫 01 / 00，來源區寫產生中", async ({ page }) => {
  await pinConfig(page, "zh-TW");
  await page.route(/\/api\/v1\/warroom\/intel(\?|$)/, (route) => route.fulfill({ json: {
    ai_enabled: true, generating: true, next_due_at: M(0), last_error_code: null, last_attempt_at: M(0),
    batch: null, stale: false, outdated: false } }));
  await page.route(/\/api\/v1\/warroom\/intel\/events/, (route) => route.fulfill({ json: { state: "ok", items: [], checked_at: M(0) } }));
  // 後端在還沒有批次時回「—」（backend/tests/test_warroom_intel.py 有測）；這裡固定那個回應，不受本機資料庫影響
  await page.route(/\/api\/v1\/warroom\/pages\/ai_intel/, (route) => route.fulfill({ json: {
    key: "ai_intel", data_at: M(0), page: 1, pages: 1, metrics: { open_anomalies: "—", watch: "—", stale_sources: "—" } } }));
  await login(page);
  await open(page);
  await expect(page.getByTestId("wr-metric-none")).toHaveCount(3);
  await expect(page.locator(".wr-metrics")).not.toContainText("無權限");
  await expect(page.getByTestId("wr-intel-meta")).not.toContainText("/ 00");
  await expect(page.getByTestId("wr-intel-flag")).toContainText("摘要產生中");
  await expect(page.getByTestId("wr-intel-sources")).toContainText("摘要產生中");
  await expect(page.getByTestId("wr-intel-sources")).not.toContainText("沒有可顯示的來源");
});

test("設定頁：AI 情報區塊、示範預覽、對比不足提示與一鍵修正、存檔送出 intel、立即產生冷卻", async ({ page }) => {
  let sent: Record<string, any> | null = null;
  let cur: Record<string, any> = {};
  await page.route(/\/api\/v1\/system\/warroom$/, async (route) => {
    if (route.request().method() === "GET") {
      cur = await (await route.fetch()).json();
      return route.fulfill({ json: cur });
    }
    if (route.request().method() !== "PUT") return route.fallback();
    sent = route.request().postDataJSON();
    await route.fulfill({ json: { ...cur, intel: sent?.intel } });
  });
  await page.route(/\/api\/v1\/system\/warroom\/intel\/generate/, (route) => route.fulfill({
    status: 429, headers: { "Retry-After": "42" },
    json: { detail: { code: "warroom_intel_cooldown", params: { seconds: 42 }, message: "please wait" } } }));
  await login(page);
  await page.goto("/warroom-settings");
  const card = page.getByTestId("wrs-intel");
  await expect(card).toBeVisible();
  // 預覽：同一個元件、示範資料（文件用位址），不打 /warroom/intel
  let polled = 0;
  page.on("request", (r) => { if (/\/api\/v1\/warroom\/intel/.test(r.url())) polled++; });
  const preview = page.getByTestId("wrs-intel-preview");
  await expect(preview.getByTestId("wr-intel")).toBeVisible();
  await expect(preview.getByTestId("wr-intel-warning")).toContainText("198.51.100.80");
  // 預覽縮放後沒有跑出卡片
  const fits = await page.evaluate(() => {
    const box = document.querySelector("[data-testid=wrs-intel-preview]")!.getBoundingClientRect();
    const inner = document.querySelector("[data-testid=wrs-intel-preview] [data-testid=wr-intel]")!.getBoundingClientRect();
    return inner.right <= box.right + 1 && inner.bottom <= box.bottom + 1;
  });
  expect(fits).toBe(true);

  // 專注版面：預覽跟著換
  await card.getByTestId("wrs-intel-layout").getByText("專注").click();
  await expect(preview.getByTestId("wr-intel-strip")).toBeVisible();

  // 自訂配色：把正文改成跟背景很接近 → 提示 → 一鍵修正後提示消失
  await card.getByTestId("wrs-intel-palette").getByText("自訂").click();
  await expect(card.getByTestId("wrs-intel-colors")).toBeVisible();
  const firstPicker = card.getByTestId("wrs-intel-colors").locator(".n-color-picker").nth(1);
  await firstPicker.click();
  const input = page.locator(".n-color-picker-panel input").first();
  await input.fill("#1a2022");
  await input.press("Enter");
  await page.keyboard.press("Escape");
  await expect(card.getByTestId("wrs-intel-contrast")).toBeVisible();
  await card.getByTestId("wrs-intel-fix").click();
  await expect(card.getByTestId("wrs-intel-contrast")).toHaveCount(0);

  // 存檔：送出 intel（版面、配色）
  await page.getByTestId("wrs-intel-save").click();
  await expect.poll(() => sent?.intel?.layout).toBe("focus");
  expect(sent!.intel.palette).toBe("custom");
  expect(sent!.intel.colors.text).toMatch(/^#[0-9a-f]{6}$/);

  // 立即產生：冷卻中講明秒數
  await card.getByTestId("wrs-intel-generate").click();
  await expect(page.locator(".n-message--error-type")).toContainText("42");
  expect(polled).toBe(0);
  if (SHOTS) {
    await preview.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${SHOTS}/ai-intel-settings-1.png` });
    await card.getByTestId("wrs-intel-colors").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${SHOTS}/ai-intel-settings-2.png` });
  }
});

test("設定頁：重大事件警示寫出目前異常偵測的排程，不是每 N 分鐘時建議改，並可直接前往異常偵測頁（使用者 10-26）", async ({ page }) => {
  let sched = { schedule_enabled: true, times: ["04:00"], frequency: "daily", weekdays: [1], month_day: 1,
                interval_minutes: 60, last_run_at: null as string | null };
  await page.route(/\/api\/v1\/anomalies\/schedule$/, (route) => route.fulfill({ json: sched }));
  await login(page);
  await page.goto("/warroom-settings");
  // 卡片標題有圖示、不寫 12；模型標籤可以直接過去 LLM 設定
  await expect(page.getByTestId("wrs-intel-title")).toHaveText("AI 情報");
  await expect(page.getByTestId("wrs-intel-model")).toContainText("AI 對話");
  await expect(page.getByTestId("wrs-intel-model-link")).toHaveAttribute("href", "/llm");
  // 怎麼讓電視停在這個主題、更新間隔越短越吃 LLM 伺服器（使用者 10-26）
  await expect(page.getByTestId("wrs-intel-pin-tip")).toContainText("按 P");
  await expect(page.getByTestId("wrs-intel-pin-tip")).toContainText("只啟用「AI 情報」");
  await expect(page.getByTestId("wrs-intel-llm-load")).toContainText("LLM 伺服器");
  await expect(page.getByTestId("wrs-intel-rhythm-hint")).toContainText("0.8 到 1.2 倍");
  const det = page.getByTestId("wrs-intel-detect");
  await expect(det).toContainText("每天 04:00");
  await expect(det).toContainText("每 N 分鐘");
  // 已經是每 N 分鐘：只寫目前的頻率，不再建議
  sched = { ...sched, frequency: "interval", interval_minutes: 15 };
  await page.reload();
  await expect(det).toContainText("每 15 分鐘");
  await expect(det).not.toContainText("每 N 分鐘");
  // 沒開排程：講明只有手動執行
  sched = { ...sched, schedule_enabled: false };
  await page.reload();
  await expect(det).toContainText("手動");
  await page.getByTestId("wrs-intel-detect-link").click();
  await expect(page).toHaveURL(/\/anomaly$/);
});

test("段落由上往下長、上面不留空；換下一批不清掉已播的段落（使用者 10-26）", async ({ page }) => {
  test.setTimeout(120_000);
  await pinConfig(page, "zh-TW", {}, true);
  let calls = 0;
  await page.route(/\/api\/v1\/warroom\/intel(\?|$)/, (route) => {
    calls += 1;
    const b = batch("zh-TW");
    // 第二次以後是新的一批（id 不同、第一則文字不同）
    if (calls > 1) { b.id = "b2"; (b.items[0] as { params: Record<string, unknown> }).params.registered_ips = 222; }
    return route.fulfill({ json: { ai_enabled: true, generating: false, next_due_at: M(-4), last_error_code: null,
                                   last_attempt_at: M(1), batch: b, stale: false, outdated: false } });
  });
  await page.route(/\/api\/v1\/warroom\/intel\/events/, (route) => route.fulfill({ json: { state: "ok", items: [], checked_at: M(0) } }));
  await login(page);
  await open(page);
  await page.keyboard.press("p");                     // 釘選：不然播完一輪就換主題了
  // 第一則緊接在標題下面（不是貼在最底下、上面一大片空白）
  const gap = await page.evaluate(() => {
    const h = document.querySelector("[data-testid=wr-intel-main] .ai-h")!.getBoundingClientRect();
    const p = document.querySelector("[data-testid=wr-intel-main] .ai-p")!.getBoundingClientRect();
    return p.top - h.bottom;
  });
  expect(gap).toBeLessThan(40);
  // 等新的一批換上：先前那一批的段落還在
  await expect(page.getByTestId("wr-intel-current")).toContainText("222", { timeout: 90_000 });
  await expect(page.locator("[data-testid=wr-intel-main] .ai-p.old")).not.toHaveCount(0);
  await expect(page.locator("[data-testid=wr-intel-main] .ai-p.old").first()).toContainText("128,734");
  // 沒有段落被切在區塊上緣之外
  const cut = await page.evaluate(() => {
    const box = document.querySelector("[data-testid=wr-intel-main] .ai-paras")!.getBoundingClientRect();
    return [...document.querySelectorAll("[data-testid=wr-intel-main] .ai-p")]
      .filter((p) => p.getBoundingClientRect().top < box.top - 1).length;
  });
  expect(cut).toBe(0);
});

test("WARNING 寫出在哪裡：交換器埠或 VM 節點（使用者 10-26）", async ({ page }) => {
  const vmEvent = { id: "e9", category: "unauthorized_ips", label: "", ip: "198.51.100.62", first_seen_at: M(1),
                    baseline: false, resolved_at: null, state: "open",
                    where: { kind: "vm", vm: "web-proxy", node: "pve-03", cluster: "cluster-a", platform: "proxmox" } };
  await pinConfig(page, "zh-TW", { layout: "three_panel" });
  await mockIntel(page, "zh-TW", [EVENT, vmEvent]);
  await login(page);
  await open(page);
  await expect(page.getByTestId("wr-intel-where")).toContainText(/core-sw-01 · Gi1\/0\/12 · VLAN 20|VM web-proxy · 節點 pve-03/);
  expect(await overflowing(page)).toEqual([]);
});

test("輪播時 AI 情報播完一輪才換下一個主題；還沒有內容時照停留秒數（使用者 10-26）", async ({ page }) => {
  test.setTimeout(120_000);
  await page.route(/\/api\/v1\/warroom\/config$/, async (route) => {
    const cfg = await (await route.fetch()).json();
    await route.fulfill({ json: { ...cfg, pages: ["ai_intel", "overview"], language: "zh-TW", reduce_motion: true,
                                  dwell_seconds: 600, intel: { ...DISPLAY, dwell_seconds: 10 } } });
  });
  await mockIntel(page, "zh-TW");
  await login(page);
  await page.goto("/warroom");
  await expect(page.getByTestId("wr-intel")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("wr-progress")).toHaveAttribute("data-mode", "briefing");
  // 停留設 10 秒，但 4 則還沒播完：12 秒時仍在 AI 情報
  await page.waitForTimeout(12_000);
  await expect(page.getByTestId("wr-intel")).toBeVisible();
  // 播完一輪就換到下一個主題
  await expect(page.getByTestId("wr-intel")).toHaveCount(0, { timeout: 60_000 });
  await expect(page.locator(".wr-title h1")).not.toContainText("AI 情報");
});

test("在 AI 情報按 P：改照停留秒數重抓資料（不再等播完一輪），再按一次恢復等播完", async ({ page }) => {
  test.setTimeout(90_000);
  await page.route(/\/api\/v1\/warroom\/config$/, async (route) => {
    const cfg = await (await route.fetch()).json();
    await route.fulfill({ json: { ...cfg, pages: ["ai_intel", "overview"], language: "zh-TW", reduce_motion: true,
                                  dwell_seconds: 600, intel: { ...DISPLAY, dwell_seconds: 10 } } });
  });
  await mockIntel(page, "zh-TW");
  let fetches = 0;
  page.on("request", (r) => { if (/\/api\/v1\/warroom\/pages\/ai_intel/.test(r.url())) fetches++; });
  await login(page);
  await page.goto("/warroom");
  await expect(page.getByTestId("wr-intel")).toBeVisible({ timeout: 20_000 });
  await page.keyboard.press("p");
  await expect(page.getByTestId("wr-progress")).toHaveAttribute("data-mode", "sweep");
  const before = fetches;
  await expect.poll(() => fetches, { timeout: 20_000 }).toBeGreaterThan(before);   // 10 秒停留到就重抓
  await expect(page.getByTestId("wr-intel")).toBeVisible();                        // 仍在 AI 情報
  await page.keyboard.press("p");
  await expect(page.getByTestId("wr-progress")).toHaveAttribute("data-mode", "briefing");
});

test("暫停與繼續的閃光顏色不同；暫停時 WARNING 不輪替（使用者 10-26）", async ({ page }) => {
  test.setTimeout(90_000);
  const ev2 = { ...EVENT, id: "e2", ip: "198.51.100.90", where: undefined };
  await pinConfig(page, "zh-TW", {}, false);
  await mockIntel(page, "zh-TW", [EVENT, ev2]);
  await login(page);
  await open(page);
  await page.keyboard.press(" ");
  await expect(page.getByTestId("wr-flash")).toHaveAttribute("data-kind", "pause");
  const first = await page.getByTestId("wr-intel-warning").innerText();
  await page.waitForTimeout(14_000);                      // 平常 12 秒換下一項
  expect(await page.getByTestId("wr-intel-warning").innerText()).toBe(first);
  await page.keyboard.press(" ");
  await expect(page.getByTestId("wr-flash")).toHaveAttribute("data-kind", "resume");
});

test("很久沒人看：舊批次過期時寫「摘要產生中」，新的一批一出來就換上；本批寫到的 IP 不再插播（使用者 10-26）", async ({ page }) => {
  test.setTimeout(90_000);
  await pinConfig(page, "zh-TW");
  let calls = 0;
  await page.route(/\/api\/v1\/warroom\/intel(\?|$)/, (route) => {
    calls += 1;
    const b = batch("zh-TW");
    const old = new Date(Date.now() - 5 * 3600_000).toISOString();
    if (calls <= 2) Object.assign(b, { id: "old", generated_at: old, checked_at: old });
    else { b.id = "fresh"; (b.items[0] as { params: Record<string, unknown> }).params.registered_ips = 99_999; }
    return route.fulfill({ json: { ai_enabled: true, generating: calls <= 2, next_due_at: M(-4), last_error_code: null,
                                   last_attempt_at: M(0), batch: b, stale: calls <= 2, outdated: false } });
  });
  // 事件的 IP 已經寫在本批第二則（198.51.100.80 是正文裡的位址）
  const covered = { ...EVENT, ip: "198.51.100.80", where: undefined };
  await page.route(/\/api\/v1\/warroom\/intel\/events/, (route) => route.fulfill({ json: {
    state: "ok", items: [covered], checked_at: M(0) } }));
  await login(page);
  await open(page);
  await expect(page.getByTestId("wr-intel-flag")).toContainText("摘要產生中");
  await expect(page.getByTestId("wr-intel-flag")).not.toContainText("過期");
  // 產生中每 3 秒讀一次：不用等 15 秒、也不用等手上那則播完
  await expect(page.getByTestId("wr-intel-current")).toContainText("99,999", { timeout: 10_000 });
  await page.waitForTimeout(15_000);
  await expect(page.locator("[data-testid=wr-intel-main] .ai-p.ev")).toHaveCount(0);
});

test("段落放不下時整段往上滑出、最上面那則淡出（使用者 10-26：往上時要有效果）", async ({ page }) => {
  test.setTimeout(120_000);
  await pinConfig(page, "zh-TW", { speed: "fast" }, false);
  const long = "這是一段比較長的播報內容，用來把現況播報區塞滿，".repeat(6);
  await page.route(/\/api\/v1\/warroom\/intel(\?|$)/, (route) => {
    const b = batch("zh-TW");
    b.items = Array.from({ length: 8 }, (_, i) => ({ id: `L${i}`, fact_ids: ["F1"], category: "events", state: "info",
                                                     text: `${i + 1} 號：${long}` })) as typeof b.items;
    return route.fulfill({ json: { ai_enabled: true, generating: false, next_due_at: M(-4), last_error_code: null,
                                   last_attempt_at: M(0), batch: b, stale: false, outdated: false } });
  });
  await page.route(/\/api\/v1\/warroom\/intel\/events/, (route) => route.fulfill({ json: { state: "ok", items: [], checked_at: M(0) } }));
  await login(page);
  await open(page);
  await page.keyboard.press("p");
  await page.evaluate(() => {
    const el = document.querySelector("[data-testid=wr-intel-main] .ai-paras")!;
    new MutationObserver(() => {
      if (el.classList.contains("sliding") && el.querySelector(".ai-p.leaving")) (window as unknown as { __slid: boolean }).__slid = true;
    }).observe(el, { attributes: true, subtree: true, attributeFilter: ["class"] });
  });
  await expect.poll(() => page.evaluate(() => (window as unknown as { __slid?: boolean }).__slid === true),
                    { timeout: 100_000, intervals: [1000] }).toBe(true);
});
