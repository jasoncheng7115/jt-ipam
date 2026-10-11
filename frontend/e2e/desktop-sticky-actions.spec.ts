/**
 * 桌面寬度全路由巡檢：會橫向捲動的表格，「操作」欄要固定在右側。
 *
 * 客戶 2026-10-10：「用平板還好，用滑鼠就變成一直往左滑看資料、往右滑去按操作」。
 * 固定操作欄是在 NDataTable 這一層統一套用的（utils/resizableColumns.ts），但操作欄沒寫寬度、
 * 或不在最後一欄的表格套不到 —— 這支 spec 用真的瀏覽器走過每一頁，把套不到的找出來。
 *
 * 判斷：表格內容比可見寬度寬（真的需要左右捲），最後一個欄位標頭是「操作」，卻沒有固定在右側 → 列出來。
 * 路由清單與 mobile-all-routes 一樣從 router 現場解析。
 */
import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";

test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS env 才能跑");
test.setTimeout(1_200_000);
test.use({ viewport: { width: 1280, height: 800 } });

function routePaths(): string[] {
  const here = dirname(fileURLToPath(import.meta.url));
  const src = readFileSync(resolve(here, "../src/router/index.ts"), "utf-8");
  const out: string[] = [];
  for (const m of src.matchAll(/path:\s*"([^"]*)"/g)) {
    const p = m[1];
    if (p === "" || p === "/") { out.push("/"); continue; }
    if (p.startsWith("/:") || p.includes("(") || p.includes(":")) continue;   // 帶 id 的詳細頁另有 spec
    out.push(p.startsWith("/") ? p : `/${p}`);
  }
  return [...new Set(out)].filter((p) => p !== "/login");
}

/** 回傳這一頁「會左右捲、最後一欄是操作、卻沒固定」的表格描述 */
async function offenders(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const table of Array.from(document.querySelectorAll<HTMLElement>(".n-data-table"))) {
      const r = table.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      const ths = Array.from(table.querySelectorAll<HTMLElement>("thead th"));
      const last = ths[ths.length - 1];
      if (!last) continue;
      const label = (last.innerText || "").trim();
      if (!/^(操作|Actions|操作する)$/.test(label) && !last.className.includes("col-actions")) continue;
      // 內容比可見寬度寬：標頭與內容都在可捲動的容器裡
      const scrollers = Array.from(table.querySelectorAll<HTMLElement>(".n-scrollbar-container, .n-data-table-base-table-body"));
      const overflow = scrollers.some((s) => s.scrollWidth > s.clientWidth + 2);
      if (!overflow) continue;
      if (last.classList.contains("n-data-table-th--fixed-right")) continue;
      const first = (ths.find((t) => (t.innerText || "").trim())?.innerText || "").trim();
      out.push(`表格（第一欄「${first}」，${ths.length} 欄）的操作欄沒有固定在右側`);
    }
    return out;
  });
}

test("桌面寬度：會左右捲的表格，操作欄都固定在右側", async ({ page }) => {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: /登入|Sign in/i }).click();
  await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 20_000 });

  const paths = routePaths();
  expect(paths.length, "從 router 解析不到路由（格式改了？）").toBeGreaterThan(50);
  const problems: string[] = [];
  let checkedTables = 0;
  for (const url of paths) {
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(400);
    checkedTables += await page.locator(".n-data-table").count();
    for (const p of await offenders(page)) problems.push(`${url}｜${p}`);
  }
  expect(checkedTables, "一張表格都沒量到（選擇器失效？）").toBeGreaterThan(20);
  expect(problems, problems.join("\n")).toEqual([]);
});
