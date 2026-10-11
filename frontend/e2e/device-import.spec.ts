/**
 * issue #46：裝置清單只能匯出、不能匯入。
 *
 * - 裝置頁有「匯入」（只有管理員）→ 選 CSV → 預覽每一列（新增／錯誤＋原因）→ 匯入 → 清單出現
 * - 清單匯出的中文檔頭原樣匯回來也認得；地點／機櫃寫名稱
 * - 匯出的地點／機櫃不再是內部 UUID
 */
import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

const TAG = Date.now().toString(36);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("匯入：選檔 → 預覽 → 匯入 → 清單出現；錯誤列講清楚原因、不會寫入", async ({ page, request }) => {
  test.setTimeout(90_000);
  await login(page);
  const token = await page.evaluate(() => sessionStorage.getItem("access_token") || "");
  const h = { Authorization: `Bearer ${token}` };

  // 清單匯出的中文檔頭（含推算出來的「虛實」欄）＋ 一列錯誤
  const csv = [
    "名稱,IP,類型,虛實,製造商,型號,地點,機櫃,單位",
    `e2e-imp-sw-${TAG},—,交換器,實體,Juniper,EX4300,,,`,
    `e2e-imp-srv-${TAG},—,伺服器,實體,Dell,R650,,,`,
    `e2e-imp-bad-${TAG},—,烤麵包機,實體,,,,,`,
  ].join("\n");
  const file = path.join(os.tmpdir(), `devices-${TAG}.csv`);
  fs.writeFileSync(file, "﻿" + csv);

  try {
    await page.goto("/devices");
    await page.getByTestId("device-import-open").click();
    await page.getByTestId("device-import-file").setInputFiles(file);
    const sum = page.getByTestId("device-import-summary");
    await expect(sum).toContainText("新增 2");
    await expect(sum).toContainText("錯誤 1");
    const rows = page.getByTestId("device-import-rows");
    await expect(rows).toContainText("不認得的類型「烤麵包機」");
    await expect(page.getByText("這些欄位不認得")).toHaveCount(0);   // 「虛實」認得、略過

    await page.getByTestId("device-import-run").click();
    // 背景作業面板直接顯示進度與結果（客戶 2026-10-10：不用再去作業頁看）
    const item = page.getByTestId("task-tracker-item").first();
    await expect(item).toBeVisible();
    await expect(item).toHaveAttribute("data-state", "succeeded", { timeout: 30_000 });
    // 背景作業：輪詢 API 直到兩台都出現
    await expect.poll(async () => {
      const r = await request.get(`/api/v1/devices?q=e2e-imp-&page_size=50`, { headers: h });
      const items = (await r.json()).items as { name: string; type: string }[];
      return items.filter((d) => d.name.endsWith(TAG)).map((d) => `${d.name}:${d.type}`).sort();
    }, { timeout: 20_000 }).toEqual([`e2e-imp-srv-${TAG}:server`, `e2e-imp-sw-${TAG}:switch`]);
  } finally {
    fs.rmSync(file, { force: true });
    const r = await request.get(`/api/v1/devices?q=e2e-imp-&page_size=50`, { headers: h });
    const ids = ((await r.json()).items as { id: string; name: string }[]).filter((d) => d.name.endsWith(TAG)).map((d) => d.id);
    if (ids.length) await request.post("/api/v1/devices/bulk-delete", { headers: h, data: { ids } });
  }
});

test("匯入範本可以下載（含現有裝置、標準欄名）", async ({ page }) => {
  await login(page);
  await page.goto("/devices");
  await page.getByTestId("device-import-open").click();
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByTestId("device-import-template-data").click()]);
  expect(dl.suggestedFilename()).toBe("devices-import.csv");
  const text = fs.readFileSync((await dl.path())!, "utf-8").replace(/^﻿/, "");
  expect(text.split(/\r?\n/)[0]).toBe("name,fqdn,ip,type,vendor,model,serial,location,rack,u_position,u_size,rack_face,unit,description");
});
