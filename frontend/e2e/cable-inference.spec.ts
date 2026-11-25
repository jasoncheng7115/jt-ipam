/**
 * 佈線自動推斷（使用者 11-25：「有明確可以判斷的就自動 不要全都人工設」）：
 * - 佈線清單多一欄「來源」：自動的標出依據（提示框）、可以只看自動或人工
 * - 「佈線自動推斷」側欄：開關（真的後端）、立即執行、建議清單可以忽略
 * 清單、建議與執行結果攔截成固定內容（推斷規則本身由 backend/tests/test_cable_inference.py 驗）。
 */
import { test, expect } from "@playwright/test";

const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

const DEV_SW = "00000000-0000-4000-8000-0000000000e1";
const DEV_SRV = "00000000-0000-4000-8000-0000000000e2";
const CABLES = [
  { id: "00000000-0000-4000-8000-0000000000c1", label: null, type: null, color: null, length_m: null, description: null,
    status: "connected", source: "auto", verified_at: "2026-11-25T01:00:00Z",
    evidence: { kind: "fdb_single_mac", switch_port: "Gi1/0/7", macs: ["02:00:00:00:01:07"], mac_count: 1 },
    a_end: "e2e-infer-sw@Gi1/0/7", b_end: "e2e-infer-srv@eno1", a_device_id: DEV_SW, b_device_id: DEV_SRV,
    a_port_id: "00000000-0000-4000-8000-0000000000a1", b_port_id: "00000000-0000-4000-8000-0000000000a2" },
  { id: "00000000-0000-4000-8000-0000000000c2", label: "R5-01", type: "cat6", color: null, length_m: null, description: null,
    status: "connected", source: "manual", verified_at: null, evidence: null,
    a_end: "e2e-infer-sw@Gi1/0/8", b_end: "e2e-infer-nas@eth0", a_device_id: DEV_SW, b_device_id: null,
    a_port_id: "00000000-0000-4000-8000-0000000000a3", b_port_id: "00000000-0000-4000-8000-0000000000a4" },
];
const SUG = {
  id: "00000000-0000-4000-8000-0000000000f1", kind: "fdb_single_mac", reason: "conflict_manual", status: "open",
  evidence: { kind: "fdb_single_mac", switch_port: "Gi1/0/8", macs: ["02:00:00:00:01:08"], mac_count: 1 },
  a_port_id: "00000000-0000-4000-8000-0000000000a3", b_port_id: "00000000-0000-4000-8000-0000000000a5",
  a_device_id: DEV_SW, b_device_id: DEV_SRV, a_end: "e2e-infer-sw@Gi1/0/8", b_end: "e2e-infer-srv@eno2",
  first_seen_at: "2026-11-25T00:00:00Z", last_seen_at: "2026-11-25T01:00:00Z", dismissed_at: null,
};

test("佈線清單標出自動推斷的線，側欄可以立即執行與忽略建議", async ({ page }) => {
  let dismissed = false;
  await page.route(/\/api\/v1\/cables(\?.*)?$/, (route) => route.request().method() !== "GET" ? route.fallback()
    : route.fulfill({ json: { items: CABLES, total: CABLES.length, page: 1, page_size: 500 } }));
  await page.route(/\/api\/v1\/cables\/suggestions\?/, (route) => {
    const status = new URL(route.request().url()).searchParams.get("status");
    const items = status === "dismissed" ? (dismissed ? [{ ...SUG, status: "dismissed", dismissed_at: "2026-11-25T02:00:00Z" }] : [])
                                         : (dismissed ? [] : [SUG]);
    return route.fulfill({ json: { items, total: items.length, page: 1, page_size: 500 } });
  });
  await page.route(/\/api\/v1\/cables\/suggestions\/[^/]+\/dismiss$/, (route) => {
    dismissed = true;
    return route.fulfill({ json: { ...SUG, status: "dismissed" } });
  });
  await page.route(/\/api\/v1\/cables\/inference\/run$/, (route) => route.fulfill({ json: {
    created: 1, refreshed: 2, retired: 0, conflicts: 1, suggested: 0, ports_created: 0, ports_removed: 0, confirmed_manual: 1 } }));

  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill("admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: /登入/ }).click();
  await page.waitForURL((u: URL) => !u.pathname.includes("/login"));
  await page.goto("/cabling");
  await expect(page.getByText("e2e-infer-srv").first()).toBeVisible({ timeout: 20_000 });

  // 來源欄：自動標籤的提示框講出依據
  const autoTag = page.getByTestId(`cable-source-auto-${CABLES[0].id}`);
  await expect(autoTag).toHaveText("自動");
  await autoTag.hover();
  await expect(page.getByText("交換器埠 Gi1/0/7 只看到這台設備的 MAC")).toBeVisible();
  await expect(page.locator("tr", { hasText: "R5-01" })).toContainText("人工");

  await page.screenshot({ path: "test-results/cable-inference-list.png" });
  // 只看自動
  await page.getByTestId("cable-filter-source").click();
  await page.locator(".n-base-select-option", { hasText: /^自動$/ }).click();
  await expect(page.locator("tr", { hasText: "R5-01" })).toHaveCount(0);
  await expect(page).toHaveURL(/source=auto/);

  // 側欄：開關是真的後端設定；立即執行；忽略一筆建議
  await page.getByTestId("cable-infer-open").click();
  const sw = page.getByTestId("cable-infer-enabled");
  await expect(sw).toBeVisible();
  await expect(sw).toHaveClass(/n-switch--active/);
  await expect(page.getByTestId("cable-sug-open")).toContainText("跟人工的線不一致");
  await expect(page.getByTestId("cable-sug-open")).toContainText("e2e-infer-srv");
  await page.waitForTimeout(500);   // 等側欄滑入動畫結束
  await page.screenshot({ path: "test-results/cable-inference-drawer.png" });
  await page.getByTestId("cable-infer-run").click();
  await expect(page.getByText(/完成：新接 1 條/)).toBeVisible();
  // 衝突的建議：接受按鈕是「接受並取代」，會先問
  await page.getByTestId(`cable-sug-accept-${SUG.id}`).click();
  await expect(page.locator(".n-popconfirm")).toContainText("會移除");
  await page.locator(".n-popconfirm").getByRole("button", { name: /取消/ }).click();
  await page.getByTestId(`cable-sug-dismiss-${SUG.id}`).click();
  await expect(page.getByText("已忽略", { exact: true }).first()).toBeVisible();
  await page.getByText("已忽略（1）").click();
  await expect(page.getByTestId("cable-sug-dismissed")).toContainText("e2e-infer-sw");
});
