/**
 * 「進階」各資源的新增對話框標題用動態組的鍵 `advanced.create_${kind}`，型別檢查與 i18n 守門都看不到。
 * 2026-11-21 加無線連線時標題直接顯示成鍵名「advanced.create_wireless_link」，截圖才發現。
 * 這裡把 Resource 型別裡的每一種都拿去三語檢查。
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(__dirname, "..");

describe("進階的新增對話框標題", () => {
  it("每一種資源在三語都有 advanced.create_<kind>", () => {
    const src = readFileSync(resolve(root, "views/Advanced.vue"), "utf8");
    const m = src.match(/type Resource\s*=([^;]+);/);
    expect(m).not.toBeNull();
    const kinds = [...m![1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
    expect(kinds.length).toBeGreaterThan(5);
    const missing: string[] = [];
    for (const loc of ["zh-TW", "en-US", "ja-JP"]) {
      const adv = JSON.parse(readFileSync(resolve(root, `i18n/${loc}.json`), "utf8")).advanced ?? {};
      for (const k of kinds) if (!adv[`create_${k}`]) missing.push(`${loc}: advanced.create_${k}`);
    }
    expect(missing).toEqual([]);
  });
});
