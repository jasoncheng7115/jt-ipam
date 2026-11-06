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
  // 成員選項：伺服器名稱 · 類型（類型本身帶括號，以前寫成「名稱（類型）」會括號套括號）
  await m.getByTestId("dns-cg-members").click();
  const opt = page.locator(".n-base-select-option", { hasText: "e2e-ns1" }).first();
  await expect(opt).toContainText("e2e-ns1 · BIND 9 (rndc / TSIG)");
  await expect(opt).not.toContainText("（");
  await page.keyboard.press("Escape");
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

test("新增比對群組：選了成員，「不比對的 zone」馬上有得選（不必先存檔再編輯）", async ({ page }) => {
  // 使用者 2026-10-14：以前選單只讀存檔後群組的 zones，新增時選了成員還是「無資料」
  await login(page);
  await page.goto("/dns");
  await page.getByTestId("dns-cg-create").click();
  const m = page.getByTestId("dns-cg-modal");
  const zoneOpts = page.locator(".n-base-select-option");

  // 還沒選成員：沒有可選的 zone
  await m.getByTestId("dns-cg-excluded").click();
  await expect(zoneOpts).toHaveCount(0);
  await page.keyboard.press("Escape");

  const zonesReq = page.waitForResponse((r) => r.url().includes("/compare-groups/zone-options") && r.status() === 200);
  await m.getByTestId("dns-cg-members").click();
  await page.locator(".n-base-select-option", { hasText: "e2e-ns1" }).first().click();
  await page.locator(".n-base-select-option", { hasText: "e2e-ns2" }).first().click();
  await page.keyboard.press("Escape");
  await zonesReq;

  await m.getByTestId("dns-cg-excluded").click();
  await expect(zoneOpts.filter({ hasText: /^corp\.example$/ })).toHaveCount(1);
  await expect(zoneOpts.filter({ hasText: /^extra\.example$/ })).toHaveCount(1, { timeout: 10_000 });
  await page.keyboard.press("Escape");
  // 不存檔（不要把樣本的兩台搬到新群組）
  await m.getByRole("button", { name: "取消" }).click();
});

test("DNS 伺服器選 UCS：說明怎麼啟用 UDM REST API", async ({ page }) => {
  // 使用者 2026-10-14：Backup 沒裝 UDM REST API，同步只看到一整段 Apache 404 HTML
  await login(page);
  await page.goto("/dns");
  await page.getByTestId("dns-create").click();
  await page.getByTestId("dns-type").click();
  await page.locator(".n-base-select-option", { hasText: "Univention UCS" }).first().click();
  const help = page.getByTestId("dns-ucs-rest-help");
  await expect(help).toBeVisible();
  await expect(help).toContainText("啟用 UDM REST API");
  await expect(help).toContainText("univention-install univention-directory-manager-rest");
  await expect(help).toContainText("/univention/udm/");
  // 手動裝上後帳號不在授權群組會回 403：說明要寫出設定
  await expect(help).toContainText("directory/manager/rest/authorized-groups");
  await expect(help).toContainText("systemctl restart univention-directory-manager-rest");
});

test("比對群組：比對的紀錄類型預設 A、AAAA、PTR，可以另外勾 CNAME", async ({ page }) => {
  // 使用者 2026-10-14：「加入一個比對類型的複選，預設有 A AAAA PTR，其它有 CNAME MX TXT…」
  await login(page);
  await page.goto("/dns");
  const name = `e2e-types-${Date.now().toString(36)}`;
  await page.getByTestId("dns-cg-create").click();
  const m = page.getByTestId("dns-cg-modal");
  await m.getByTestId("dns-cg-name").locator("input").fill(name);
  const types = m.getByTestId("dns-cg-types");
  for (const ty of ["A", "AAAA", "PTR"]) await expect(types.getByRole("checkbox", { name: ty, exact: true })).toBeChecked();
  for (const ty of ["CNAME", "MX", "TXT", "NS", "SRV"]) await expect(types.getByRole("checkbox", { name: ty, exact: true })).not.toBeChecked();
  await types.getByRole("checkbox", { name: "CNAME", exact: true }).click();
  const req = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/dns/compare-groups"));
  await m.getByTestId("dns-cg-save").click();
  expect((await req).postDataJSON().compare_types).toEqual(["A", "AAAA", "PTR", "CNAME"]);
  const row = page.getByTestId("dns-compare-groups").locator("tr", { hasText: name });
  await expect(row).toContainText("A, AAAA, PTR, CNAME", { timeout: 10_000 });

  // 全部取消勾選不能存
  await row.getByRole("button", { name: "編輯" }).click();
  for (const ty of ["A", "AAAA", "PTR", "CNAME"]) await types.getByRole("checkbox", { name: ty, exact: true }).click();
  await m.getByTestId("dns-cg-save").click();
  await expect(page.locator(".n-message").filter({ hasText: "至少要勾一種紀錄類型" })).toBeVisible();
  await m.getByRole("button", { name: "取消" }).click();

  await row.getByRole("button", { name: "刪除" }).click();
  const confirmBtn = page.locator(".n-popconfirm__action button").last();
  await expect(confirmBtn).toBeVisible();
  await confirmBtn.dispatchEvent("click");
  await expect(row).toHaveCount(0, { timeout: 10_000 });
});

test("比對群組表格：短欄依內容縮小，剩下的空間給長欄，不用左右捲動", async ({ page }) => {
  // 使用者 2026-10-14：「欄位寬度沒利用好，名稱、狀態都還有空間可以縮」
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  await page.goto("/dns");
  const table = page.getByTestId("dns-cg-table");
  await expect(table.locator("tr", { hasText: "e2e-dns-group" })).toBeVisible({ timeout: 15_000 });
  const th = (name: string) => table.locator("th", { hasText: name }).first();
  const width = async (name: string) => (await th(name).boundingBox())!.width;
  expect(await width("名稱")).toBeLessThan(150);
  expect(await width("狀態")).toBeLessThan(110);
  expect(await width("差異")).toBeLessThan(100);
  // 長欄（不比對的 zone）拿到剩下的空間，比短欄寬
  expect(await width("不比對的 zone")).toBeGreaterThan(await width("名稱"));
  const scroll = await table.locator(".n-scrollbar-container").first().evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(scroll, "1440 寬的畫面不該需要左右捲動").toBeLessThanOrEqual(2);
  if (process.env.E2E_SHOTS_DIR) await page.getByTestId("dns-compare-groups").screenshot({ path: `${process.env.E2E_SHOTS_DIR}/cg-table.png` });
});

test("排除：DHCP 範圍勾選框、不比對的名稱與 IP（使用者 2026-11-05）", async ({ page, request }) => {
  await login(page);
  await page.goto("/dns");
  const name = `e2e-ex-${Date.now().toString(36)}`;
  await page.getByTestId("dns-cg-create").click();
  const m = page.getByTestId("dns-cg-modal");
  await m.getByTestId("dns-cg-name").locator("input").fill(name);
  await expect(m.getByTestId("dns-cg-ex-dhcp")).toHaveAttribute("aria-checked", "false");   // 預設不勾
  await m.getByTestId("dns-cg-ex-dhcp").click();
  // 多選標籤欄位：點一下才出現輸入框，打完按 Enter 加入
  await m.getByTestId("dns-cg-ex-names").click();
  await page.keyboard.type("*.lab.corp.example"); await page.keyboard.press("Enter");
  await m.getByTestId("dns-cg-ex-ips").click();
  await page.keyboard.type("198.51.100.100-198.51.100.199"); await page.keyboard.press("Enter");
  await page.keyboard.type("not-an-ip"); await page.keyboard.press("Enter");
  const bad = page.waitForResponse((r) => r.url().endsWith("/api/v1/dns/compare-groups") && r.request().method() === "POST");
  await m.getByTestId("dns-cg-save").click();
  expect((await bad).status()).toBe(422);
  await expect(page.locator(".n-message")).toContainText("看不懂的排除項目：not-an-ip");
  // 拿掉錯的那個再存
  await m.getByTestId("dns-cg-ex-ips").locator(".n-tag", { hasText: "not-an-ip" }).locator(".n-tag__close").click();
  const ok = page.waitForResponse((r) => r.url().endsWith("/api/v1/dns/compare-groups") && r.request().method() === "POST");
  await m.getByTestId("dns-cg-save").click();
  const body = (await ok).request().postDataJSON();
  expect(body.exclude_dhcp_ranges).toBe(true);
  expect(body.excluded_names).toEqual(["*.lab.corp.example"]);
  expect(body.excluded_ips).toEqual(["198.51.100.100-198.51.100.199"]);

  const row = page.getByTestId("dns-compare-groups").locator("tr", { hasText: name });
  await row.getByRole("button", { name: "編輯" }).click();
  await expect(m.getByTestId("dns-cg-ex-dhcp")).toHaveAttribute("aria-checked", "true");
  await expect(m.getByTestId("dns-cg-ex-names")).toContainText("*.lab.corp.example");
  await expect(m.getByTestId("dns-cg-ex-ips")).toContainText("198.51.100.100-198.51.100.199");
  await page.keyboard.press("Escape");

  // 實際效果：樣本群組裡只在 ns1 的 legacy，排除後不再是差異；測完還原
  const token = await page.evaluate(() => sessionStorage.getItem("access_token"));
  const H = { Authorization: `Bearer ${token}` };
  const groups = await (await request.get("/api/v1/dns/compare-groups", { headers: H })).json();
  const all = Array.isArray(groups) ? groups : groups.items;
  const sample = all.find((g: { name: string }) => g.name === "e2e-dns-group");
  const tmp = all.find((g: { name: string }) => g.name === name);
  try {
    await request.patch(`/api/v1/dns/compare-groups/${sample.id}`, { headers: H, data: { excluded_names: ["legacy.corp.example"] } });
    const diffs = await (await request.get(`/api/v1/dns/compare-groups/${sample.id}/diffs`, { headers: H })).json();
    expect(diffs.filter((d: { name: string }) => d.name.startsWith("legacy."))).toHaveLength(0);
  } finally {
    await request.patch(`/api/v1/dns/compare-groups/${sample.id}`, { headers: H, data: { excluded_names: [] } });
    await request.delete(`/api/v1/dns/compare-groups/${tmp.id}`, { headers: H });
  }
});
