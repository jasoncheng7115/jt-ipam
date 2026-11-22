/**
 * 層架（以「層」計）不能再說「U 位」。
 *
 * 客戶回報兩件事：機櫃圖空位的提示寫「點此挑裝置放入這個 U 位」、裝置表單的挑選器
 * 標題是「挑選 U 位」—— 這台是木架，畫面其它地方都講「層」。
 *
 * 同一個挑選器還漏了**開放頂**那一層：木架最上面那片板的上面放得下東西，機櫃圖也畫得出來，
 * 但清單只列到第 9 層，於是「看得到、選不到」。
 */
import { test, expect } from "@playwright/test";

const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
const RACK = process.env.E2E_SHELF_RACK_ID || "";
const DEV = process.env.E2E_SHELF_DEVICE_ID || "";
test.skip(!ADMIN_PASS || !RACK || !DEV,
  "需要 E2E_ADMIN_PASS / E2E_SHELF_RACK_ID / E2E_SHELF_DEVICE_ID");

async function login(page: any) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_PASS ? "admin" : "");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: /登入/ }).click();
  await page.waitForURL((u: URL) => !u.pathname.includes("/login"));
}

test("機櫃圖空位的提示：層架說「這一層」", async ({ page }) => {
  await login(page);
  await page.goto(`/racks?rack=${RACK}`);
  const gap = page.locator(".u-gap").first();
  await gap.waitFor({ timeout: 20_000 });
  await expect(gap).toHaveAttribute("title", "點此挑裝置放入這一層");
});

test("層位挑選器：標題講層位，且列得出「頂」那一層", async ({ page }) => {
  await login(page);
  await page.goto(`/devices/${DEV}`);
  // 裝置頁要載入埠、電源、IP…，冷啟動時「編輯」會晚好幾秒才出現
  const edit = page.getByRole("button", { name: /^編輯$/ }).first();
  await edit.waitFor({ timeout: 30_000 });
  await edit.click();
  await page.locator(".upick-rack, [class*='upick']").first().waitFor({ state: "attached" })
    .catch(() => undefined);   // 還沒開挑選器，忽略
  // 「挑層」按鈕就在層位欄位旁邊
  await page.locator("button[title='挑選層位']").first().click();
  await page.locator(".upick-rack").waitFor({ timeout: 10_000 });

  await expect(page.locator(".n-card-header__main", { hasText: "挑選層位" }).first())
    .toBeVisible();
  const labels = await page.locator(".upick-rack .upick-u").allInnerTexts();
  // 9 層的木架 + 開放頂 = 10 列，由上而下：頂、9、8…1
  expect(labels.map((s) => s.trim()))
    .toEqual(["頂", "9", "8", "7", "6", "5", "4", "3", "2", "1"]);
});

test("挑選器要講得出「該層第幾個位置」，不能只給名字", async ({ page }) => {
  await login(page);
  await page.goto(`/devices/${DEV}`);
  // 裝置頁要載入埠、電源、IP…，冷啟動時「編輯」會晚好幾秒才出現
  const edit = page.getByRole("button", { name: /^編輯$/ }).first();
  await edit.waitFor({ timeout: 30_000 });
  await edit.click();
  await page.locator("button[title='挑選層位']").first().waitFor({ timeout: 10_000 });
  await page.locator("button[title='挑選層位']").first().click();
  await page.locator(".upick-rack").waitFor({ timeout: 10_000 });

  const texts = await page.locator(".upick-row .upick-body").allInnerTexts();
  const byLabel = new Map<string, string>();
  const labels = await page.locator(".upick-row .upick-u").allInnerTexts();
  labels.forEach((l, i) => byLabel.set(l.trim(), texts[i].trim()));

  // 只檢查標示的寫法，不寫死設備名稱（這支跑在實際的層架上，名稱不放進原始碼）
  // 佔左半又只佔下半的那一台，兩個方向都要講
  expect(byLabel.get("頂")).toMatch(/^\S+（左半·下半）$/);
  // 只切上下的就只講上下
  expect(byLabel.get("9")).toMatch(/^\S+（下半）$/);
  // 同一層兩台並排：由左而右，各自標位置
  expect(byLabel.get("2")).toMatch(/^\S+（左半）、\S+（右半）$/);
  // 佔滿整層的不要畫蛇添足
  expect(byLabel.get("1")).toMatch(/^[^（、]+$/);
});
