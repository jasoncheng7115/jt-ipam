/**
 * 子網路內的位址範圍（集區）—— GitHub issue #40。
 *
 * 回報者的 DHCP 集區是 .181～.250，沒辦法用一個 CIDR 表示；子網路維持 CIDR，範圍是它裡面的一段。
 * 這裡走一遍：新增 → 清單顯示大小／已用／下一個可用、位址圖標出範圍 → 重疊被擋（錯誤訊息要翻譯過）
 * → 修改 → 刪除。樣本：seed_e2e 的 10.20.0.0/24（做完會刪掉，不留下資料）。
 */
import { test, expect, type Page } from "@playwright/test";

const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill("admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: /登入/ }).click();
  await page.waitForURL((u: URL) => !u.pathname.includes("/login"));
}

async function fillRange(page: Page, start: string, end: string, name: string) {
  const m = page.locator(".n-modal").last();
  await m.waitFor();
  await m.getByPlaceholder("198.51.100.181").fill(start);
  await m.getByPlaceholder("198.51.100.250").fill(end);
  const nameItem = m.locator(".n-form-item").filter({ has: page.locator(".n-form-item-label", { hasText: /^名稱/ }) });
  await nameItem.locator("input").fill(name);
  await m.getByRole("button", { name: /儲存/ }).click();
}

test("位址範圍：新增、標在位址圖上、重疊被擋、修改、刪除", async ({ page }) => {
  await login(page);
  const subs = await page.evaluate(async () => (await fetch("/api/v1/subnets?page_size=500", {
    headers: { Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } })).json());
  const sn = subs.items.find((s: any) => s.cidr === "10.20.0.0/24");
  // 別的 spec 刪掉的位址留下的冷卻期會讓「下一個可用」不是 .100：先清掉這段範圍裡的（2026-11-04）
  await page.evaluate(async (sid) => {
    const h = { Authorization: `Bearer ${sessionStorage.getItem("access_token")}`, "Content-Type": "application/json" };
    const cd = await (await fetch(`/api/v1/addresses/cooldowns/${sid}`, { headers: h })).json();
    for (const c of cd.items ?? []) {
      const o = Number(String(c.ip).split(".")[3]);
      if (o >= 100 && o <= 150) {
        await fetch(`/api/v1/addresses/cooldowns/${sid}/${c.ip}/clear`, { method: "POST", headers: h,
          body: JSON.stringify({ reason: "e2e setup" }) });
      }
    }
  }, sn.id);
  await page.goto(`/subnets/${sn.id}`);
  const card = page.locator(".n-card").filter({ hasText: /位址範圍（集區）/ }).first();
  await card.waitFor({ timeout: 20_000 });

  await card.getByRole("button", { name: "新增範圍" }).click();
  await fillRange(page, "10.20.0.100", "10.20.0.150", "e2e-pool");
  const row = card.locator("tr", { hasText: "e2e-pool" });
  await expect(row).toContainText("10.20.0.100 – 10.20.0.150");
  await expect(row).toContainText("0 / 51");
  await expect(row).toContainText("DHCP 集區");
  await expect(row.getByRole("button", { name: "10.20.0.100" })).toBeVisible();   // 下一個可用
  // 位址圖：範圍內的格子標出來
  await expect(page.locator('.subnet-grid [data-range="dhcp"]')).toHaveCount(51);

  // 重疊：錯誤訊息要是翻譯過的中文，不是代碼
  await card.getByRole("button", { name: "新增範圍" }).click();
  await fillRange(page, "10.20.0.140", "10.20.0.160", "e2e-overlap");
  await expect(page.locator(".n-message").filter({ hasText: "與既有範圍「e2e-pool」重疊" })).toBeVisible();
  await page.locator(".n-modal").last().getByRole("button", { name: /取消/ }).click();

  // 修改：縮成 .100～.120
  await row.getByTitle("編輯").click();
  const m = page.locator(".n-modal").last();
  await m.getByPlaceholder("198.51.100.250").fill("10.20.0.120");
  await m.getByRole("button", { name: /儲存/ }).click();
  await expect(row).toContainText("0 / 21");
  await expect(page.locator('.subnet-grid [data-range="dhcp"]')).toHaveCount(21);

  // 刪除
  await row.getByTitle("刪除").click();
  await page.locator(".n-popconfirm").getByRole("button", { name: /確定|確認|OK/ }).click();
  await expect(card.locator("tr", { hasText: "e2e-pool" })).toHaveCount(0);
  await expect(page.locator('.subnet-grid [data-range="dhcp"]')).toHaveCount(0);
});
