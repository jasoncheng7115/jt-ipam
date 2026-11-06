import { test, expect, type Page } from "@playwright/test";

/**
 * 下一版待辦（使用者 2026-10-15）：
 * 1. 系統設定「IP 變更評估」卡片：以前放在兩欄格子裡，說明被擠成一行四五個字、右欄對不齊、整張卡大片空白。
 *    改成跟資安卡片一樣一列一個設定（左名稱與說明、右控制項）；維護排程卡片同樣問題一起改。
 * 2. 系統匯出 / 匯入：剛進頁面各類筆數還在統計，「產生匯出檔」反灰卻沒說為什麼。現在統計中有提示，
 *    按鈕旁寫出不能按的原因（還在統計、沒勾資料、密碼太短、兩次不一樣）；匯入的「分析」也一樣。
 */
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("IP 變更評估與維護排程卡片：一列一個設定，說明不被擠成窄欄", async ({ page }) => {
  await login(page);
  await page.goto("/system-settings");
  for (const id of ["ss-change-impact", "ss-maintenance"]) {
    const card = page.getByTestId(id);
    await card.scrollIntoViewIfNeeded();
    await expect(card.locator(".ss-rows")).toHaveCount(1);
    await expect(card.locator(".ss-grid")).toHaveCount(0);
    // 每一列的說明寬度要佔卡片一大半（以前兩欄格子裡只剩不到一半，中文一行四五個字）
    const widths = await card.locator(".ss-r__text").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().width));
    const cardW = (await card.boundingBox())!.width;
    expect(widths.length).toBeGreaterThan(1);
    for (const w of widths) expect(w, `${id}：說明寬 ${w} / 卡片 ${cardW}`).toBeGreaterThan(cardW * 0.45);
  }
  await page.getByTestId("ss-change-impact").screenshot({ path: "test-results/ss-change-impact.png" });
});

test("系統匯出：統計中有提示；按鈕不能按時寫出原因", async ({ page }) => {
  await login(page);
  // 把統計的回應拖慢，才看得到「統計中」
  await page.route("**/api/v1/system/transfer/schema*", async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  await page.goto("/system-transfer");
  await expect(page.getByTestId("st-counting")).toBeVisible();
  await expect(page.getByTestId("st-export-why")).toContainText("還在統計");
  await expect(page.getByTestId("st-counting")).toBeHidden({ timeout: 20_000 });
  const why = page.getByTestId("st-export-why");
  await expect(why).toContainText("密碼至少要 8 個字");
  const pw = page.locator("input[type=password]");
  await pw.nth(0).fill("passphrase-1");
  await pw.nth(1).fill("passphrase-2");
  await expect(why).toContainText("兩次輸入的密碼不一樣");
  await pw.nth(1).fill("passphrase-1");
  await expect(why).toBeHidden();
  await expect(page.getByRole("button", { name: "產生匯出檔" })).toBeEnabled();
  await expect(page.getByTestId("st-import-why")).toContainText("請先選擇匯出檔");
});
