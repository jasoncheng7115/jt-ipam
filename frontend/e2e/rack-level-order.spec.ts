/**
 * 層架「每層高度」的編輯順序要跟機櫃圖看到的一樣。
 *
 * 客戶回報：機櫃圖 top-down（最上面是最高層）時，編輯清單卻從第 1 層往下列，
 * 兩邊順序顛倒 —— 要改畫面最上面那一層，得往清單最下面找。
 *
 * 這裡除了比順序，更要比「標籤與值有沒有綁在同一層」：只把顯示順序反過來很容易
 * 連資料順序一起反掉，那樣使用者改的是看起來那一層、存進去的卻是另一層 ——
 * 畫面完全正常，只有存檔後才看得出來，所以要走一次「改值 → 存 → 重開」。
 */
import { test, expect } from "@playwright/test";

const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

/** 種在 jt_ipam_e2e 的兩台 wood_shelf，層高由第 1 層（最底）起算。 */
const HEIGHTS = [210, 210, 110, 140, 140, 140, 140, 45, 110];

async function login(page: any) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill("admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: /登入/ }).click();
  await page.waitForURL((u: URL) => !u.pathname.includes("/login"));
}

/** 打開某台機櫃的編輯視窗，停在逐層高度那一段。 */
async function openEdit(page: any, name: string) {
  await page.goto("/racks");
  const row = page.locator("tr", { hasText: name }).first();
  await row.waitFor({ timeout: 20_000 });
  // 操作欄由左到右是：釘選、編輯、刪除
  await row.locator("td").last().locator("button").nth(1).click();
  await page.locator(".level-rows").waitFor({ timeout: 10_000 });
}

async function rows(page: any): Promise<{ label: string; value: string }[]> {
  return page.locator(".level-row").evaluateAll((els: Element[]) =>
    els.map((e) => ({
      label: (e.querySelector(".level-row__label") as HTMLElement).innerText.trim(),
      value: (e.querySelector("input") as HTMLInputElement).value.trim(),
    })));
}

test("top-down：編輯列由最高層排到第 1 層，且值仍綁在原本那一層", async ({ page }) => {
  await login(page);
  await openEdit(page, "SHELF-TD");
  const got = await rows(page);
  expect(got.map((r) => r.label)).toEqual(
    [9, 8, 7, 6, 5, 4, 3, 2, 1].map((n) => `第 ${n} 層`));
  // 由上而下＝第 9 層…第 1 層，值要跟著反過來（不是把陣列原順序貼上去）
  expect(got.map((r) => r.value)).toEqual([...HEIGHTS].reverse().map(String));
});

test("bottom-up：第 1 層畫在最上面，編輯列就正著排", async ({ page }) => {
  await login(page);
  await openEdit(page, "SHELF-BU");
  const got = await rows(page);
  expect(got.map((r) => r.label)).toEqual(
    [1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `第 ${n} 層`));
  expect(got.map((r) => r.value)).toEqual(HEIGHTS.map(String));
});

test("改最上面那一列 → 存進去的是第 9 層，不是第 1 層", async ({ page }) => {
  await login(page);
  await openEdit(page, "SHELF-TD");
  const first = page.locator(".level-row").first();
  await expect(first.locator(".level-row__label")).toHaveText("第 9 層");
  await first.locator("input").fill("333");
  await first.locator("input").blur();
  await page.getByRole("button", { name: /^儲存$|^確定$|^Save$/ }).first().click();
  await page.locator(".level-rows").waitFor({ state: "detached", timeout: 10_000 });

  await openEdit(page, "SHELF-TD");
  const got = await rows(page);
  expect(got[0]).toEqual({ label: "第 9 層", value: "333" });
  // 其餘各層一格都不能跟著位移
  expect(got.map((r) => r.value)).toEqual(
    [...HEIGHTS.slice(0, 8), 333].reverse().map(String));

  // 還原，讓這支測試可以重複跑
  await first.locator("input").fill(String(HEIGHTS[8]));
  await first.locator("input").blur();
  await page.getByRole("button", { name: /^儲存$|^確定$|^Save$/ }).first().click();
});

test("層編號方向排在每層高度上面 —— 先選方向，再照那個順序填高度", async ({ page }) => {
  await login(page);
  await openEdit(page, "SHELF-TD");
  // 量座標而不是比 DOM 順序：欄位可能被 v-if/排版搬動，實際看到的位置才算數
  const dir = page.locator(".n-form-item", { hasText: "層編號方向" }).first();
  const rows = page.locator(".level-rows").first();
  const a = (await dir.boundingBox())!;
  const b = (await rows.boundingBox())!;
  expect(a.y + a.height,
    `層編號方向（底 ${a.y + a.height}）要整個在每層高度清單（頂 ${b.y}）上面`)
    .toBeLessThanOrEqual(b.y);
});

test("種類排在所有取決於它的欄位上面（#35）", async ({ page }) => {
  // 層數／編號方向／層板厚度／每層高度的意義都取決於種類：先選種類，後面的欄位才對得上
  await login(page);
  await openEdit(page, "SHELF-TD");
  // 只比對欄位標籤：說明文字裡也有「層數」這些字，hasText 會抓到別的欄位
  const item = (label: string) => page.locator(".n-form-item").filter({
    has: page.locator(".n-form-item-label", { hasText: new RegExp(`^\\s*${label}\\s*$`) }) }).first();
  // 用 poll 量到穩定為止：對話框開啟動畫中是縮放的，量到的位置會互相重疊
  for (const label of ["層編號方向", "層數", "層板厚度"]) {
    await expect.poll(async () => {
      const kind = (await item("種類").boundingBox())!;
      const b = (await item(label).boundingBox())!;
      return b.y - (kind.y + kind.height);
    }, { message: `種類要在「${label}」上面` }).toBeGreaterThanOrEqual(0);
  }
});
