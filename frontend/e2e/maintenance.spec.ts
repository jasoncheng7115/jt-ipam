/**
 * 維護排程（2026-11-01）：裝置頁一鍵維護 → 按鈕變「維護中，到 HH:MM」、頂端列出現「維護中 N」→ 清單頁看到進行中
 * → 延長、提前結束；從清單新增每週例行（範圍搜尋、影響預覽）。資料都是這支測試建的，結束時刪掉。
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function token(request: APIRequestContext): Promise<string> {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  expect(r.ok()).toBeTruthy();
  return (await r.json()).access_token;
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("維護排程：裝置頁一鍵維護、頂端提示、延長與提前結束、新增每週例行", async ({ page, request }) => {
  const auth = { Authorization: `Bearer ${await token(request)}` };
  const tag = Date.now().toString(36);
  const dev = await (await request.post("/api/v1/devices", { headers: auth, data: { name: `maint-sw-${tag}-core-distribution-floor-12`, type: "switch" } })).json();
  try {
    await login(page);
    await page.goto(`/devices/${dev.id}`);
    await page.getByTestId("maint-quick").click();
    // 選項每個都有圖示，60 分鐘以上用小時
    await expect(page.locator(".n-dropdown-option", { hasText: "30 分鐘" }).locator(".n-dropdown-option-body__prefix svg")).toBeVisible();
    await page.locator(".n-dropdown-option", { hasText: "2 小時" }).click();
    const active = page.getByTestId("maint-quick-active");
    await expect(active).toContainText("維護中，到", { timeout: 15_000 });
    await expect(page.getByTestId("maint-indicator")).toContainText("維護中");
    await active.click();
    for (const label of ["延長 1 小時", "提前結束", "查看維護"]) {
      await expect(page.locator(".n-dropdown-option", { hasText: label }).locator(".n-dropdown-option-body__prefix svg")).toBeVisible();
    }
    await page.keyboard.press("Escape");

    await page.goto("/maintenance");
    const table = page.getByTestId("maint-table");
    const row = table.locator("tbody tr", { hasText: `maint-sw-${tag}` });
    await expect(row).toContainText("進行中");
    await row.getByRole("button", { name: "調整" }).click();
    await page.locator(".n-dropdown-option", { hasText: "延長 1 小時" }).click();
    await expect(row).toContainText("剩 2 小時");          // 原本 2 小時，延長後顯示「剩 2 小時 59 分」之類
    await row.getByRole("button", { name: "調整" }).click();
    await page.locator(".n-dropdown-option", { hasText: "提前結束" }).click();
    await page.locator(".n-tabs-tab", { hasText: "已結束" }).click();
    await expect(table.locator("tbody tr", { hasText: `maint-sw-${tag}` })).toBeVisible();

    // 新增每週例行：範圍搜尋這台裝置、看到影響預覽
    await page.getByTestId("maint-add").click();
    await page.getByTestId("maint-title").locator("input").fill(`例行維護 ${tag}`);
    const dlg = page.locator(".n-modal");
    // 立即開始的自訂時長可以選分鐘、小時、天
    await dlg.getByTestId("maint-duration").click();
    await page.locator(".n-base-select-option", { hasText: "自訂" }).click();
    await dlg.getByTestId("maint-custom-unit").click();
    await expect(page.locator(".n-base-select-menu").last().locator(".n-base-select-option")).toHaveText(["分鐘", "小時", "天"]);
    await page.locator(".n-base-select-menu").last().locator(".n-base-select-option", { hasText: "天" }).click();
    await expect(dlg.getByTestId("maint-custom-unit")).toContainText("天");
    await page.getByTestId("maint-mode").getByText("每週例行").click();
    // 星期日排第一個
    await expect(dlg.locator(".n-checkbox").first()).toContainText("日");
    await dlg.locator(".n-checkbox", { hasText: "二" }).click();
    const range = dlg.locator(".n-date-picker").last();
    await range.locator("input").first().click();
    // 面板右下的「清除」「確定」也有圖示
    await expect(page.getByTestId("date-confirm").locator("svg")).toBeVisible();
    await expect(page.locator(".n-date-panel-weekdays").first()).toContainText(/^日/);   // 月曆從星期日開始
    await page.locator(".n-date-panel").screenshot({ path: "test-results/maint-datepicker.png" });
    await range.locator("input").first().fill(new Date().toISOString().slice(0, 10));
    const until = new Date(Date.now() + 90 * 86400_000).toISOString().slice(0, 10);
    await range.locator("input").last().fill(until);
    await range.locator("input").last().press("Enter");
    const row0 = dlg.getByTestId("maint-scope-row").first();
    await row0.locator(".n-base-selection").first().click();
    await page.locator(".n-base-select-option", { hasText: "裝置" }).first().click();
    await row0.getByTestId("maint-scope-pick").click();
    await page.keyboard.type(`maint-sw-${tag}`);
    await page.locator(".n-base-select-option", { hasText: `maint-sw-${tag}` }).click();
    await expect(dlg.getByTestId("maint-preview")).toContainText("1 台裝置");
    await page.mouse.move(4, 4);
    await dlg.screenshot({ path: "test-results/maint-dialog.png" });
    await page.getByTestId("maint-save").click();
    await page.locator(".n-tabs-tab", { hasText: "即將開始" }).click();
    await expect(table.locator("tbody tr", { hasText: `例行維護 ${tag}` })).toContainText("每週二");
    await page.locator(".n-tabs-tab", { hasText: "全部" }).click();
    // 進行中的列按鈕最多（調整、編輯、刪除），用它量寬度
    await request.post("/api/v1/maintenance/quick", { headers: auth,
      data: { scope: { scope_type: "device", object_id: dev.id }, minutes: 60 } });
    await page.reload();
    await page.locator(".n-tabs-tab", { hasText: "全部" }).click();
    await expect(table.locator("tbody tr").first()).toContainText("進行中");
    await page.setViewportSize({ width: 1410, height: 900 });
    await table.screenshot({ path: "test-results/maint-list.png" });
    // 操作按鈕不可以超出表格右緣（表格沒有橫向捲軸時會捲不過去），範圍標籤不可以蓋到下一欄
    const tbox = (await table.boundingBox())!;
    for (const b of await table.locator("tbody tr").first().locator("td.col-actions button").all()) {
      const bb = (await b.boundingBox())!;
      expect(bb.x + bb.width).toBeLessThanOrEqual(tbox.x + tbox.width);
    }
    const scopeTh = (await table.locator("th", { hasText: "範圍" }).boundingBox())!;
    for (const tg of await table.locator(".scope-tag").all()) {
      const gb = (await tg.boundingBox())!;
      expect(gb.x + gb.width).toBeLessThanOrEqual(scopeTh.x + scopeTh.width);
    }
    // 除了操作欄，每個欄位標題都可以點來排序
    for (const col of ["狀態", "標題", "時間", "範圍", "處理方式", "已靜音", "變更單號"]) {
      await expect(table.locator("th", { hasText: col }).locator(".n-data-table-sorter")).toHaveCount(1);
    }
    await expect(table.locator("th", { hasText: "操作" }).locator(".n-data-table-sorter")).toHaveCount(0);

    // 稽核記錄：目標是標題、可點進詳細資料；明細寫站台時間與範圍名稱，不是 UUID 和 JSON
    await page.goto("/audit");
    const arow = page.locator("tbody tr", { hasText: `例行維護 ${tag}` }).first();
    await expect(arow).toBeVisible({ timeout: 15_000 });
    await arow.click();
    const detail = page.locator(".n-modal");
    await expect(detail).toContainText(`裝置 maint-sw-${tag}`);
    await expect(detail).toContainText("每週二 02:00 - 04:00");
    await expect(detail).not.toContainText("object_id");
    await page.mouse.move(4, 4);
    await detail.screenshot({ path: "test-results/maint-audit.png" });
  } finally {
    const list = await (await request.get("/api/v1/maintenance", { headers: auth })).json();
    for (const w of list.items ?? []) {
      if (String(w.title).includes(tag)) await request.delete(`/api/v1/maintenance/${w.id}`, { headers: auth });
    }
    await request.delete(`/api/v1/devices/${dev.id}`, { headers: auth });
  }
});
