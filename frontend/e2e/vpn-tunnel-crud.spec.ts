import { test, expect, type Page } from "@playwright/test";

/**
 * 站對站 VPN：以前只有清單，手動記的通道建了改不了、刪不掉（2026-11-21）。
 *   同步來的通道唯讀由後端測試 test_vpn_tunnel_crud.py 涵蓋（API 建不出同步來的通道）。
 *   1. VPN 頁：新增（兩端裝置遠端搜尋）、表格顯示類型與狀態的文字、編輯（清空 B 端、改狀態）、刪除
 *   2. 進階 → 電路 → 站對站 VPN 頁籤是同一份清單，一樣有新增按鈕
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.setTimeout(150_000);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const res = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: res.status, json: res.status === 204 ? null : await res.json().catch(() => null) };
  }, { method, path, body });
}

async function pickRemote(page: Page, testId: string, text: string) {
  const sel = page.locator(".n-modal").getByTestId(testId);
  await sel.click();
  await sel.locator("input").fill(text);
  await page.locator(".n-base-select-option", { hasText: text }).first().click({ timeout: 10_000 });
}

test("VPN 通道：新增、編輯、刪除；進階頁籤同一份", async ({ page }) => {
  const u = Date.now().toString(36);
  await login(page);
  const a = (await api(page, "POST", "/api/v1/devices", { name: `e2e-fw-a-${u}`, type: "firewall" })).json;
  const b = (await api(page, "POST", "/api/v1/devices", { name: `e2e-fw-b-${u}`, type: "firewall" })).json;
  try {
    await page.goto("/vpn");
    // 只有清單自己的一顆重新整理（頁面那顆以前按了沒反應）；電力頁也只剩頁籤裡那顆
    await expect(page.getByRole("button", { name: "重新整理" })).toHaveCount(1);
    await page.goto("/power");
    await expect(page.locator(".n-tabs-tab").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "重新整理" })).toHaveCount(1);
    await page.goto("/vpn");
    await page.getByTestId("vpn-create").click();
    const modal = page.locator(".n-modal");
    await modal.getByTestId("vpn-name").locator("input").fill(`e2e-vpn-${u}`);
    await modal.getByTestId("vpn-type").click();
    await page.locator(".n-base-select-option", { hasText: "WireGuard" }).click();
    await pickRemote(page, "vpn-a", `e2e-fw-a-${u}`);
    await pickRemote(page, "vpn-b", `e2e-fw-b-${u}`);
    await modal.getByTestId("vpn-b-ep").locator("input").fill("203.0.113.9");
    await modal.getByTestId("vpn-desc").locator("textarea").fill("備援");
    await page.screenshot({ path: "test-results/vpn-form.png" });
    await modal.getByTestId("vpn-save").click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    const row = page.getByTestId("vpn-tunnel-table").locator("tr", { hasText: `e2e-vpn-${u}` });
    await expect(row).toContainText("WireGuard", { timeout: 10_000 });
    await expect(row).toContainText("使用中");

    // 編輯：兩端裝置名稱要帶回來；清空 B 端裝置、狀態改離線
    await row.locator("td").last().locator("button").first().click();
    await expect(modal.getByTestId("vpn-a")).toContainText(`e2e-fw-a-${u}`);
    await expect(modal.getByTestId("vpn-desc").locator("textarea")).toHaveValue("備援");
    await modal.getByTestId("vpn-b").hover();
    await modal.getByTestId("vpn-b").locator(".n-base-clear").click();
    await modal.getByTestId("vpn-status").click();
    await page.locator(".n-base-select-option", { hasText: "離線" }).click();
    await modal.getByTestId("vpn-save").click();
    await expect(modal).toHaveCount(0, { timeout: 10_000 });
    await expect(row).toContainText("離線", { timeout: 10_000 });
    const items = (await api(page, "GET", "/api/v1/vpn-tunnels?page_size=500")).json.items;
    const mine = items.find((x: any) => x.name === `e2e-vpn-${u}`);
    expect(mine.b_device_id).toBeNull();
    expect(mine.status).toBe("offline");
    await page.screenshot({ path: "test-results/vpn-table.png" });

    // 刪除
    await row.locator("td").last().locator("button").nth(1).click();
    await page.mouse.move(0, 0);   // 圖示按鈕的提示框會蓋住確認視窗的按鈕
    await page.locator(".n-popconfirm").getByRole("button", { name: /確定|確認|OK/ }).click();
    await expect(row).toHaveCount(0, { timeout: 10_000 });

    // 進階 → 電路 → 站對站 VPN：同一份元件
    await page.goto("/advanced/circuits");
    await page.locator(".n-tabs-tab", { hasText: "站對站 VPN" }).click();
    await expect(page.getByTestId("vpn-create")).toBeVisible();
  } finally {
    const items = (await api(page, "GET", "/api/v1/vpn-tunnels?page_size=500")).json?.items ?? [];
    for (const x of items) if (String(x.name).includes(u)) await api(page, "DELETE", `/api/v1/vpn-tunnels/${x.id}`);
    for (const d of [a, b]) if (d?.id) await api(page, "DELETE", `/api/v1/devices/${d.id}`);
  }
});
