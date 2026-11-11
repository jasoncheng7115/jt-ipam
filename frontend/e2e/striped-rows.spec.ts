/**
 * 表格隔行底色（客戶 2026-11-11：「白色主題一整排白色閱讀會有點痛苦，能不能一行白一行有顏色」）。
 * 右上角佈景選單裡切換，存在這台瀏覽器（localStorage），預設關。
 */
import { test, expect, type Page } from "@playwright/test";
import { createTempAdmin, deleteTempUser, type TempUser } from "./helpers/tempAdmin";

const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.use({ viewport: { width: 1500, height: 950 } });
// 要切成淺色：用自己的帳號，不動共用 admin 的佈景偏好（平行跑的其他 spec 會受影響）
let tmp: TempUser | null = null;
test.beforeAll(async ({ request }) => { tmp = await createTempAdmin(request, "striped"); });
test.afterAll(async ({ request }) => { await deleteTempUser(request, tmp); });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(tmp!.username);
  await page.getByPlaceholder(/密碼|Password/).fill(tmp!.password);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

const rowBg = (page: Page, n: number) => page.locator(".n-data-table-tbody .n-data-table-tr").nth(n)
  .locator(".n-data-table-td").first().evaluate((el) => getComputedStyle(el).backgroundColor);

test("佈景選單打開表格隔行底色：偶數列有底色、重新整理還在、關掉恢復", async ({ page }) => {
  await login(page);
  await page.goto("/devices");
  // 先切淺色（客戶說的就是淺色）
  await page.locator("button.topbar-ctl").nth(1).click();
  await page.locator(".n-dropdown-option", { hasText: "淺色" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator(".n-data-table-tbody .n-data-table-tr").nth(3)).toBeVisible({ timeout: 15_000 });
  await page.mouse.move(0, 0);
  expect(await rowBg(page, 0)).toBe(await rowBg(page, 1));      // 預設關：一整片同色

  await page.locator("button.topbar-ctl").nth(1).click();
  await page.getByTestId("theme-striped-rows").click();
  await expect(page.locator("html")).toHaveAttribute("data-striped", "1");
  await page.mouse.move(0, 0);
  const odd = await rowBg(page, 0), even = await rowBg(page, 1);
  expect(even).not.toBe(odd);
  expect(await rowBg(page, 2)).toBe(odd);
  expect(await rowBg(page, 3)).toBe(even);
  expect(even).toBe("rgb(244, 247, 251)");                       // 不透明：固定欄位不會透出底下的欄
  await page.locator(".n-data-table").first().screenshot({ path: "test-results/striped-rows-light.png" });

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-striped", "1");
  expect(await page.evaluate(() => localStorage.getItem("table_striped"))).toBe("1");

  await page.locator("button.topbar-ctl").nth(1).click();
  await page.getByTestId("theme-striped-rows").click();
  await expect(page.locator("html")).not.toHaveAttribute("data-striped", "1");
  await page.mouse.move(0, 0);
  expect(await rowBg(page, 1)).toBe(await rowBg(page, 0));
});
