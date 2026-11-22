/**
 * 編輯表單的「說明」要能清空（使用者 10-22：掃描代理的說明刪光按儲存，進來又出現了）。
 * 原因是 `description: x.description || undefined`：空字串變 undefined，JSON 裡就沒這個欄位，後端當作沒改。
 * 一次改掉了 26 處；這裡守住不要再出現。要清空就送空字串（`?? ""`）。
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

function vueFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? vueFiles(join(dir, e.name)) : e.name.endsWith(".vue") ? [join(dir, e.name)] : []);
}

// 2026-11-21 掃描表單時又找到裝置的廠牌／型號／序號、地點地址、VRF 的 RD 同樣清不掉：
// 擴大成所有「欄位: form.value.欄位 || undefined」。只在新增時用的表單（沒有舊值要清）列為例外。
const CREATE_ONLY = ["views/IPRequests.vue"];

describe("編輯表單的欄位可以清空", () => {
  it("沒有 `x: form.value.x || undefined`（清空就送 null）", () => {
    const root = resolve(__dirname, "..");
    const bad: string[] = [];
    for (const f of vueFiles(root)) {
      const rel = f.slice(root.length + 1);
      if (CREATE_ONLY.includes(rel)) continue;
      for (const m of readFileSync(f, "utf8").matchAll(/^\s+(\w+):\s*form\.value\.(\w+)\s*\|\|\s*undefined/gm)) {
        if (m[1] === m[2]) bad.push(`${rel}: ${m[1]}`);
      }
    }
    expect(bad).toEqual([]);
  });
});

describe("說明欄位可以清空", () => {
  it("沒有 `description: x.description || undefined`", () => {
    const root = resolve(__dirname, "..");
    const bad = vueFiles(root).filter((f) => /description:\s*[\w.]+\.description\s*\|\|\s*undefined/.test(readFileSync(f, "utf8")));
    expect(bad.map((f) => f.slice(root.length + 1))).toEqual([]);
  });
});
