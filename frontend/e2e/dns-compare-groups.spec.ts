import { test, expect, type Page } from "@playwright/test";

/**
 * DNS 比對群組（2026-10-10）：內容應該一致的 DNS 伺服器放在同一組（jt-ipam 不同步，只比對拉回來的資料）。
 * 樣本（tests/seed_e2e.py）：e2e-dns-group 有 e2e-ns1、e2e-ns2 兩台，corp.example 裡
 * www／mail 兩台都有（ns2 的 www 是大寫，正規化後一樣），legacy 只在 ns1；ns2 另外有自己的 zone extra.example。
 * 寬限 0 分鐘、不通知。
 *   1. 比對群組卡片：立即檢查 → 不一致、差異 1 筆；差異清單寫出哪一台有、哪一台沒有
 *   2. DNS 紀錄頁：預設合併（3 筆，www 標出兩台與群組），取消合併就逐台列出（5 筆）
 *   3. 異常偵測：「DNS 同步不一致」頁籤列出 legacy
 *   4. 新增、編輯、刪除群組（不動樣本那一組）
 *   5. 群組表格與差異清單：篩選、挑選欄位、匯出都在（使用者 2026-10-10：表格要有排序、挑選欄位、篩選）
 *   6. 「不比對這個 zone」：只在一台的 zone 一鍵排除、馬上重新比對；在群組設定拿掉就又列出來
 *      （2026-10-11 真實環境驗證：Technitium 另外放了沒複寫的 zone，整組永遠不一致）
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("比對群組：立即檢查、差異清單", async ({ page }) => {
  await login(page);
  await page.goto("/dns");
  const card = page.getByTestId("dns-compare-groups");
  const row = card.locator("tr", { hasText: "e2e-dns-group" });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row).toContainText("e2e-ns1");
  await expect(row).toContainText("e2e-ns2");
  await row.getByRole("button", { name: "立即檢查" }).click();
  await expect(row).toContainText("不一致", { timeout: 10_000 });

  await row.getByRole("button", { name: "差異清單" }).click();
  const modal = page.getByTestId("dns-cg-diffs");
  await expect(modal).toBeVisible();
  const d = modal.locator("tbody tr", { hasText: "legacy.corp.example" });
  await expect(d).toContainText("192.0.2.82");
  await expect(d).toContainText("e2e-ns1");
  await expect(d).toContainText("e2e-ns2");
  await expect(d).toContainText("已確認");
  const z = modal.locator("tbody tr", { hasText: "extra.example" });
  await expect(z).toContainText("整個 zone 只在部分伺服器上");
  await expect(modal.locator("tbody tr")).toHaveCount(2);
  // 伺服器表的「比對群組」欄
  await modal.getByRole("button", { name: "關閉" }).click();
  await expect(page.locator("tr", { hasText: "e2e-ns2" }).first()).toContainText("e2e-dns-group");
});

test("DNS 紀錄頁：同一組相同的紀錄合併，取消合併逐台列出", async ({ page }) => {
  await login(page);
  await page.goto("/advanced/dns-records?q=corp.example");
  const rows = page.locator(".n-data-table-tbody tr");
  await expect(rows).toHaveCount(3, { timeout: 15_000 });
  const www = rows.filter({ hasText: /www\.corp\.example/i });
  await expect(www).toContainText("e2e-ns1");
  await expect(www).toContainText("e2e-ns2");
  await expect(www).toContainText("群組 e2e-dns-group");
  await page.getByTestId("dns-records-merge").click();
  await expect(rows).toHaveCount(5, { timeout: 10_000 });
});

test("異常偵測：DNS 比對不一致", async ({ page }) => {
  test.setTimeout(90_000);
  await login(page);
  await page.goto("/dns");
  await page.getByTestId("dns-compare-groups").locator("tr", { hasText: "e2e-dns-group" })
    .getByRole("button", { name: "立即檢查" }).click();
  await page.goto("/anomaly?tab=dns_compare_mismatch");
  await page.getByRole("button", { name: "執行偵測" }).click();
  const r = page.locator("tr", { hasText: "legacy.corp.example" }).first();
  await expect(r).toBeVisible({ timeout: 30_000 });
  await expect(r).toContainText("e2e-dns-group");
  await expect(r).toContainText("e2e-ns2");
});

test("新增、編輯、刪除比對群組", async ({ page }) => {
  await login(page);
  await page.goto("/dns");
  const name = `e2e-tmp-${Date.now().toString(36)}`;
  await page.getByTestId("dns-cg-create").click();
  const m = page.getByTestId("dns-cg-modal");
  await m.getByTestId("dns-cg-name").locator("input").fill(name);
  await m.getByTestId("dns-cg-save").click();
  const row = page.getByTestId("dns-compare-groups").locator("tr", { hasText: name });
  await expect(row).toBeVisible({ timeout: 10_000 });
  await expect(row).toContainText("尚未比對");

  await row.getByRole("button", { name: "編輯" }).click();
  await m.getByTestId("dns-cg-name").locator("input").fill(`${name}-x`);
  await m.getByTestId("dns-cg-save").click();
  const renamed = page.getByTestId("dns-compare-groups").locator("tr", { hasText: `${name}-x` });
  await expect(renamed).toBeVisible({ timeout: 10_000 });

  await renamed.getByRole("button", { name: "刪除" }).click();
  // 確認鈕旁邊會疊著刪除按鈕的提示框（滑鼠還停在垃圾桶上），比照其他 spec 直接送 click 事件
  const confirmBtn = page.locator(".n-popconfirm__action button").last();
  await expect(confirmBtn).toBeVisible();
  await confirmBtn.dispatchEvent("click");
  await expect(renamed).toHaveCount(0, { timeout: 10_000 });
});

test("比對群組表格與差異清單可以篩選、排序、挑選欄位、匯出", async ({ page }) => {
  await login(page);
  await page.goto("/dns");
  const card = page.getByTestId("dns-compare-groups");
  await expect(card.locator("tr", { hasText: "e2e-dns-group" })).toBeVisible({ timeout: 15_000 });
  await expect(card.getByRole("button", { name: /欄位/ })).toBeVisible();
  await expect(card.getByRole("button", { name: /匯出/ })).toBeVisible();
  // 排序：資料欄的標題可以點（有排序圖示）
  await expect(card.locator("th", { hasText: "名稱" }).locator(".n-data-table-sorter")).toHaveCount(1);
  const filter = card.getByTestId("dns-cg-filter").locator("input");
  await filter.fill("e2e-ns2");   // 成員名稱也篩得到
  await expect(card.locator("tbody tr", { hasText: "e2e-dns-group" })).toBeVisible();
  await filter.fill("no-such-group-zzz");
  await expect(card.locator("tbody tr", { hasText: "e2e-dns-group" })).toHaveCount(0);
  await filter.fill("");

  const row = card.locator("tr", { hasText: "e2e-dns-group" });
  await row.getByRole("button", { name: "立即檢查" }).click();
  await row.getByRole("button", { name: "差異清單" }).click();
  const modal = page.getByTestId("dns-cg-diffs");
  await expect(modal.locator("tbody tr", { hasText: "legacy.corp.example" })).toBeVisible();
  await expect(modal.getByRole("button", { name: /欄位/ })).toBeVisible();
  await expect(modal.getByRole("button", { name: /匯出/ })).toBeVisible();
  const df = modal.getByTestId("dns-cg-diff-filter").locator("input");
  await df.fill("no-such-record-zzz");
  await expect(modal.locator("tbody tr", { hasText: "legacy.corp.example" })).toHaveCount(0);
  await df.fill("192.0.2.82");
  await expect(modal.locator("tbody tr", { hasText: "legacy.corp.example" })).toBeVisible();
});

test("不比對這個 zone：一鍵排除、馬上重新比對，群組設定拿掉就又列出來", async ({ page }) => {
  await login(page);
  await page.goto("/dns");
  const card = page.getByTestId("dns-compare-groups");
  const row = card.locator("tr", { hasText: "e2e-dns-group" });
  await row.getByRole("button", { name: "立即檢查" }).click();
  await row.getByRole("button", { name: "差異清單" }).click();
  const modal = page.getByTestId("dns-cg-diffs");
  const zoneRow = modal.locator("tbody tr", { hasText: "extra.example" });
  await expect(zoneRow).toBeVisible();
  // 只有「整個 zone」那列有按鈕，單筆紀錄的差異沒有（避免一筆差異就把整個 zone 排除）
  await expect(modal.locator("tbody tr", { hasText: "legacy.corp.example" }).getByRole("button", { name: "不比對這個 zone" })).toHaveCount(0);
  await zoneRow.getByRole("button", { name: "不比對這個 zone" }).click();
  const ok = page.locator(".n-popconfirm__action button").last();
  await expect(ok).toBeVisible();
  await ok.dispatchEvent("click");
  await expect(modal.locator("tbody tr", { hasText: "extra.example" })).toHaveCount(0, { timeout: 10_000 });
  await expect(modal.getByTestId("dns-cg-excluded-note")).toContainText("extra.example");
  await expect(modal.locator("tbody tr")).toHaveCount(1);
  await modal.getByRole("button", { name: "關閉" }).click();
  await expect(row).toContainText("extra.example");   // 表格的「不比對的 zone」欄

  // 在群組設定拿掉 → 存檔後馬上重新比對，zone 的差異又出現
  await row.getByRole("button", { name: "編輯" }).click();
  const m = page.getByTestId("dns-cg-modal");
  await m.getByTestId("dns-cg-excluded").locator(".n-base-close").first().click();
  await m.getByTestId("dns-cg-save").click();
  await expect(row).not.toContainText("extra.example", { timeout: 10_000 });
  await row.getByRole("button", { name: "差異清單" }).click();
  await expect(page.getByTestId("dns-cg-diffs").locator("tbody tr", { hasText: "extra.example" })).toBeVisible();
});
