/**
 * 儀表板「IP 可用性」的編輯追蹤清單（客戶 11-03，把字放大後遇到三個狀況）：
 * 1. 選了一堆 IP 之後，標籤把每一行塞滿，輸入框沒有位置 → 打不了字
 * 2. 打 198.51.100.1 搜尋，.1、.10、.100… 一長串清單超出畫面 → 選不到
 * 3. 標籤一個一行把對話框撐高 → 儲存 / 取消被擠出畫面
 * 860×500 約等於 1280×720 的螢幕把字放大到 150%。資料是這支測試建的，結束時刪掉、追蹤清單還原。
 */
import { test, expect } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.use({ viewport: { width: 860, height: 500 } });

test("放大字型：輸入框打得到字、完全符合的位址排第一且清單不超出畫面、儲存與取消看得到", async ({ page, request }) => {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  const H = { Authorization: `Bearer ${(await r.json()).access_token}` };
  const tag = Date.now().toString(36);
  const sec = await (await request.post("/api/v1/sections", { headers: H, data: { name: `upt-${tag}` } })).json();
  const sub = await (await request.post("/api/v1/subnets", { headers: H,
    data: { section_id: sec.id, cidr: "198.51.100.0/24", allow_overlap: true } })).json();
  const octets = [1, ...Array.from({ length: 10 }, (_, i) => 10 + i), ...Array.from({ length: 10 }, (_, i) => 100 + i)];
  const ids: Record<number, string> = {};
  for (const o of octets) {
    const a = await (await request.post("/api/v1/addresses", { headers: H,
      data: { subnet_id: sub.id, ip: `198.51.100.${o}`, hostname: `server-${o}-${tag}.branch-office.example.local` } })).json();
    ids[o] = a.id;
  }
  const prefs = await (await request.get("/api/v1/me/preferences", { headers: H })).json();
  const pinned = [10, 11, 12, 13, 14, 15, 16, 17].map((o) => ids[o]);
  await request.patch("/api/v1/me/preferences", { headers: H, data: { pinned: { ...(prefs.pinned ?? {}), uptime_ips: pinned } } });
  try {
    await page.goto("/login");
    await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
    await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
    await page.getByRole("button", { name: "登入", exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
    await page.goto("/");
    const card = page.locator(".dash-uptime");
    await card.scrollIntoViewIfNeeded();
    await card.getByRole("button", { name: /編輯/ }).first().click();
    const modal = page.locator(".n-modal").last();
    const picker = modal.getByTestId("uptime-picker");
    await expect(picker).toBeVisible();

    // 3. 已選 8 個：標籤只佔一行（其餘收成 +N），儲存 / 取消都在畫面內
    expect((await picker.boundingBox())!.height).toBeLessThan(70);
    for (const id of ["uptime-save", "uptime-cancel"]) {
      const b = (await modal.getByTestId(id).boundingBox())!;
      expect(b.y + b.height, id).toBeLessThanOrEqual(500);
    }
    await page.screenshot({ path: "test-results/uptime-picker-selected.png" });

    // 1. 輸入框打得到字
    await picker.click();
    await page.keyboard.type("198.51.100.1");
    const menu = page.locator(".n-base-select-menu").last();
    await expect(menu.locator(".n-base-select-option").first()).toContainText(`198.51.100.1 — server-1-${tag}`, { timeout: 10_000 });

    // 2. 清單不超出畫面
    const mb = (await menu.boundingBox())!;
    expect(mb.y).toBeGreaterThanOrEqual(0);
    expect(mb.y + mb.height).toBeLessThanOrEqual(500);
    await page.screenshot({ path: "test-results/uptime-picker-search.png" });
    await menu.locator(".n-base-select-option").first().click();
    await modal.locator(".n-card-header").click();      // 收起清單（Esc 會連對話框一起關）

    const save = modal.getByTestId("uptime-save");
    const sb = (await save.boundingBox())!;
    expect(sb.y + sb.height).toBeLessThanOrEqual(500);
    await save.click();
    await expect(modal).toBeHidden();
    const after = await (await request.get("/api/v1/me/preferences", { headers: H })).json();
    expect(after.pinned.uptime_ips).toContain(ids[1]);
    expect(after.pinned.uptime_ips).toHaveLength(9);
  } finally {
    await request.patch("/api/v1/me/preferences", { headers: H, data: { pinned: prefs.pinned ?? {} } });
    await request.delete(`/api/v1/subnets/${sub.id}`, { headers: H });
    await request.delete(`/api/v1/sections/${sec.id}`, { headers: H });
  }
});
