/**
 * 「管理 > 版本資訊」的「下載 SBOM」：下載到這個站台的 CycloneDX 1.5 清單（2026-11-16）。
 */
import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.setTimeout(120_000);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("版本資訊頁下載 SBOM：CycloneDX 1.5，含後端與畫面的套件", async ({ page }) => {
  await login(page);
  await page.goto("/version");
  const box = page.getByTestId("ver-sbom");
  await expect(box).toContainText("軟體物料清單（SBOM）", { timeout: 20_000 });
  await expect(box).toContainText("Dependency-Track");
  await box.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/sbom-version-page.png" });

  // 伺服器要掃一遍安裝的套件，冷啟動時要幾秒
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 60_000 }),
                                  page.getByTestId("ver-sbom-download").click()]);
  expect(dl.suggestedFilename()).toMatch(/^jt-ipam-\d+\.\d+\.\d+\.sbom\.cdx\.json$/);
  const bom = JSON.parse(await readFile(await dl.path(), "utf-8"));
  expect(bom.bomFormat).toBe("CycloneDX");
  expect(bom.specVersion).toBe("1.5");
  const purls: string[] = bom.components.map((c: { purl: string }) => c.purl);
  expect(purls.some((p) => p.startsWith("pkg:pypi/fastapi@"))).toBe(true);
  expect(purls.some((p) => p.startsWith("pkg:npm/vue@"))).toBe(true);
});
