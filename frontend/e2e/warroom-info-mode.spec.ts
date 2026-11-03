/**
 * 戰情室「資訊展示模式」（2026-11-03 使用者：有的單位不允許看到紅字或問題）。
 * 打開後：頁尾只剩 8 個主題（異常與曝露、採集健康、可用性、AI 巡檢連選單都沒有），每個主題畫面上看不到
 * 問題類的字眼，AI 情報只剩現況播報一塊；設定頁有開關、播放清單標出不播放的主題。
 * 會改全站的戰情室設定（列在 global-state-specs.txt），結束時還原。
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.use({ viewport: { width: 1920, height: 1080 } });

const ALL = ["overview", "capacity", "topology", "services", "anomalies", "assets", "collection",
             "stats", "relations", "availability", "ai_audit", "ai_intel"];
const HIDDEN = ["anomalies", "collection", "availability", "ai_audit"];
/** 畫面上不可以出現的字（主題名稱以外的地方） */
const BAD = /異常|離線|告警|警告|WARNING|失敗|過期|到期|待處理|不一致|未回應|缺口|風險|衝突/;

async function headers(request: APIRequestContext): Promise<Record<string, string>> {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  expect(r.ok()).toBeTruthy();
  return { Authorization: `Bearer ${(await r.json()).access_token}` };
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

function body(s: Record<string, unknown>): Record<string, unknown> {
  const { all_pages: _a, ready_pages: _r, token_set: _t, token_expires_at: _e, scope_labels: _l, ...rest } = s;
  return rest;
}

test("資訊展示模式：問題主題不出現、每頁沒有問題字眼、AI 情報只剩現況播報、設定頁可切換", async ({ page, request }) => {
  const H = await headers(request);
  const before = await (await request.get("/api/v1/system/warroom", { headers: H })).json();
  const put = await request.put("/api/v1/system/warroom", { headers: H,
    data: { ...body(before), pages: ALL, info_mode: true, language: "zh-TW", reduce_motion: true } });
  expect(put.status(), await put.text()).toBe(200);
  try {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await login(page);
    await page.goto("/warroom");
    await expect(page.locator(".wr-title h1")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".wr")).toHaveAttribute("data-info-mode", "1");
    const keys = await page.locator(".wr-nav__i").evaluateAll((els) => els.map((e) => e.getAttribute("data-key")));
    expect(keys).toEqual(ALL.filter((k) => !HIDDEN.includes(k)));
    for (let i = 1; i <= keys.length; i++) {
      await page.keyboard.press(String(i));
      await expect(page.locator(".wr-nav__i.on")).toHaveAttribute("data-pos", String(i));
      if (keys[i - 1] === "ai_intel") {
        // 等第一則播報出來（沒有模型時是系統摘要），內容是「共登記 N 個 IP、M 個子網路」這種現況
        await expect(page.getByTestId("wr-intel-current")).toContainText(/共登記/, { timeout: 45_000 });
        await page.waitForTimeout(4000);
      } else {
        await page.waitForTimeout(700);
      }
      const text = await page.locator(".wr-title, .wr-main, .wr-top").allInnerTexts();
      expect(text.join("\n"), `主題 ${keys[i - 1]}`).not.toMatch(BAD);
      await page.screenshot({ path: `test-results/wr-info-${String(i).padStart(2, "0")}-${keys[i - 1]}.png` });
    }
    await expect(page.getByTestId("wr-intel-watch")).toHaveCount(0);
    await expect(page.getByTestId("wr-intel-sources")).toHaveCount(0);
    expect(errors).toEqual([]);

    // 設定頁：開關是開的、播放清單標出不播放的四個主題、AI 情報區塊寫明不會出現 WARNING
    await page.goto("/warroom-settings");
    await expect(page.getByTestId("wrs-info-mode")).toHaveClass(/n-switch--active/, { timeout: 15_000 });
    await expect(page.getByTestId("wrs-info-hidden")).toHaveCount(HIDDEN.length);
    await expect(page.getByTestId("wrs-intel-info-note")).toBeVisible();
    await page.getByTestId("wrs-info-mode").scrollIntoViewIfNeeded();
    await page.locator(".wrs-playlist").screenshot({ path: "test-results/wr-info-settings.png" });
  } finally {
    await request.put("/api/v1/system/warroom", { headers: H, data: body(before) });
  }
});
