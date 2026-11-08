/**
 * IP 拓樸圖的縮圖總覽（使用者 2026-11-07：「關係圖有做左下縮圖總覽區功能，IP 拓樸圖也要，每一種模式都要」）。
 * 四種模式都要有、畫得出節點（外框不畫），點縮圖會把畫面移過去。
 */
import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.use({ viewport: { width: 1600, height: 1000 } });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

const n = (id: string, label: string, type: string, extra: Record<string, unknown> = {}) => ({ data: { id, label, type, ...extra } });
const l2 = (id: string, host: string, sw: string, port: string) =>
  ({ data: { id, source: host, target: sw, kind: "l2", via: "fdb", evidence: "learned", label: port, port, direct: true,
             port_mac_count: 1, mac: `aa:00:00:00:00:${id.slice(-2)}` } });

test("四種模式都有縮圖總覽，點縮圖會移動畫面", async ({ page }) => {
  await page.route(/\/api\/v1\/topology(\?.*)?$/, (route) => route.fulfill({ json: {
    nodes: [
      n("sn1", "198.51.100.0/24", "subnet"), n("swa", "switch-a", "switch", { ip: "198.51.100.2" }),
      n("fw", "fw-1", "firewall", { ip: "198.51.100.1" }),
      ...Array.from({ length: 12 }, (_, i) => n(`h${i}`, `host-${i}`, "server", { ip: `198.51.100.${10 + i}` })),
    ],
    edges: [
      { data: { id: "e-fw", source: "fw", target: "sn1", kind: "l3", evidence: "monitored" } },
      { data: { id: "e-sw", source: "swa", target: "sn1", kind: "l3", evidence: "monitored" } },
      ...Array.from({ length: 12 }, (_, i) => ({ data: { id: `l3-${i}`, source: `h${i}`, target: "sn1", kind: "l3", evidence: "monitored" } })),
      ...Array.from({ length: 6 }, (_, i) => l2(`l2-0${i}`, `h${i}`, "swa", `Gi1/0/${i + 1}`)),
    ],
  } }));
  await login(page);
  await page.goto("/topology");
  const mini = page.getByTestId("topology-minimap");
  for (const mode of ["自動（混合）", "以交換器為中心", "只看存取層（FDB）", "只看子網路"]) {
    await page.locator(".n-select").nth(1).click();
    await page.locator(".n-base-select-option", { hasText: mode }).click();
    await expect(mini, mode).toBeVisible({ timeout: 15_000 });
    await expect.poll(async () => mini.locator("rect").count(), { message: mode }).toBeGreaterThan(0);
  }
  // 畫面上隱藏的節點縮圖也不畫：只看存取層比只看子網路少（沒有 FDB 連線的都不顯示）
  const subnetOnly = await mini.locator("rect").count();
  await page.locator(".n-select").nth(1).click();
  await page.locator(".n-base-select-option", { hasText: "只看存取層（FDB）" }).click();
  await expect.poll(async () => mini.locator("rect").count()).toBeLessThan(subnetOnly);
  // 節點很少時不放大到塞滿縮圖
  const w = Number(await mini.locator("rect").first().getAttribute("width"));
  expect(w).toBeLessThan(60);
  await page.locator(".n-select").nth(1).click();
  await page.locator(".n-base-select-option", { hasText: "只看子網路" }).click();

  // 放大之後點縮圖的角落，畫面的範圍框跟著移過去
  for (let i = 0; i < 4; i++) await page.locator("button", { hasText: "＋" }).first().click();
  const view = page.getByTestId("graph-minimap-view").last();
  const before = await view.getAttribute("style");
  const box = (await mini.boundingBox())!;
  await page.mouse.click(box.x + 15, box.y + 15);
  await expect.poll(async () => view.getAttribute("style")).not.toBe(before);
  await mini.screenshot({ path: "test-results/topology-minimap.png" });
  await page.screenshot({ path: "test-results/topology-minimap-page.png" });
});
