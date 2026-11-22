import { test, expect } from "@playwright/test";

/**
 * 機櫃圖的裝置名稱：對齊設定要生效、跨多 U 的名稱不可以被下一格蓋掉。
 *
 * 使用者同時抓到兩個（2026-09-03）：設定明明選了「置中」卻看起來靠左；
 * 而且 2U 的名稱被切一半、4U 的整個不見。兩個都只有量幾何才看得出來 ——
 * 前者是絕對定位的名稱框被 `max-width` 釘在左邊（框寬 126 / 整列寬 250，
 * 文字在框內置中，所以「置中」是真的、只是框不對）；後者是名稱溢出自己那一格之後，
 * 被後面才畫的兄弟格蓋住。
 */
const PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!PASS, "需要 E2E_ADMIN_PASS");

test("機櫃圖：名稱依設定置中，且跨多 U 也看得見", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await page.waitForURL((u) => !u.pathname.includes("/login"));

  await page.goto("/racks", { waitUntil: "domcontentloaded" });
  const spans = page.locator(".d-name-span");
  await expect(spans.first()).toBeVisible({ timeout: 20_000 });

  const info = await spans.evaluateAll((els) => els.map((e) => {
    const r = e.getBoundingClientRect();
    const row = (e.parentElement as HTMLElement).getBoundingClientRect();
    return {
      text: (e.textContent || "").trim(),
      justify: getComputedStyle(e).justifyContent,
      zIndex: getComputedStyle(e).zIndex,
      widthRatio: r.width / row.width,          // 名稱框要幾乎與整列同寬
      heightRatio: r.height / row.height,       // >1 代表跨多 U
    };
  }));
  expect(info.length, "測試環境要有裝置在機櫃裡").toBeGreaterThan(0);

  for (const s of info) {
    // 名稱框要撐滿整列，否則「置中」只是在一個貼左的小框裡置中
    expect(s.widthRatio, `${s.text} 的名稱框只有整列的 ${(s.widthRatio * 100).toFixed(0)}%`)
      .toBeGreaterThan(0.9);
    if (s.justify === "center") {
      // 置中時左右留白要對稱（框滿寬 + justify center 就成立）
      expect(s.justify).toBe("center");
    }
    // 跨多 U 的名稱一定要疊在後面幾格之上，否則會被蓋掉
    if (s.heightRatio > 1.5) {
      expect(Number(s.zIndex), `${s.text} 跨 ${s.heightRatio.toFixed(0)} 格卻沒有 z-index`)
        .toBeGreaterThan(0);
    }
  }

  // ── hover：名稱不可以因為「被點亮」而消失 ──
  // 只要那幾格用了 filter / opacity 來打亮，每一格就變成獨立的堆疊環境，
  // 上面那條 z-index 就跳不出去了 —— 實機症狀是「游標移過去名稱就不見」。
  const tall = spans.filter({ hasText: /host|switch|nas|ups/ }).first();
  // 預設打開哪個機櫃跟著帳號偏好走（別的 spec 會換），那台可能在畫面外：先捲進來再移游標
  await tall.scrollIntoViewIfNeeded();
  const box = (await tall.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height - 6);
  await page.waitForTimeout(500);
  // 層架與並排的裝置畫成 .u-part（寬度不一定滿格），同一條規則一樣適用 ——
  // 只看 .u-row 的話，預設打開的是層架時這裡會一格都找不到
  const hl = await page.locator(".u-row.u-hl, .u-half.u-hl, .u-part.u-hl").evaluateAll((els) =>
    els.map((e) => {
      const cs = getComputedStyle(e);
      return { filter: cs.filter, opacity: cs.opacity };
    }));
  expect(hl.length, "游標移上去應該要點亮整台裝置").toBeGreaterThan(0);
  for (const h of hl) {
    expect(h.filter, "被點亮的格子用了 filter → 會建立堆疊環境，跨 U 的名稱會被蓋掉").toBe("none");
    expect(h.opacity, "被點亮的格子改了 opacity → 同樣會建立堆疊環境").toBe("1");
  }
});
