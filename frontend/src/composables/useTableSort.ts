/**
 * 給 NDataTable columns 自動補 default sorter — 任何 column 沒寫 sorter 都會被加上。
 *
 * 規則：
 *   - selection / expand 欄不動
 *   - 沒 key 的欄不動
 *   - 已有 sorter 的欄不動 (包括 'default')
 *   - 數字 → 數字比；其它 → String localeCompare
 *   - null / undefined 排到最後
 *
 * 用法：
 *   const cols = autoSort<MyRow>([
 *     { type: "selection" },
 *     { title: "name", key: "name" },          // 自動補 sorter
 *     { title: "age", key: "age", sorter: (a,b)=>a.age-b.age },  // 維持自訂
 *   ]);
 */
import type { DataTableColumns } from "naive-ui";
import { cmpNatural } from "@/utils/sort";

export function autoSort<T = any>(cols: DataTableColumns<T>): DataTableColumns<T> {
  return cols.map((c: any) => {
    if (!c || typeof c !== "object") return c;
    if (c.type === "selection" || c.type === "expand") return c;
    if (c.sorter !== undefined && c.sorter !== null) return c;
    if (!c.key) return c;
    // 操作欄不排序：加 sorter 會多出排序箭頭，擠到標題（如「操作」）換行
    if (c.key === "actions" || (typeof c.className === "string" && c.className.includes("col-actions"))) {
      return c;
    }
    const key = c.key as string;
    return {
      ...c,
      sorter: (a: any, b: any) => {
        const av = a?.[key];
        const bv = b?.[key];
        if (av == null && bv == null) return 0;
        if (av == null) return 1;   // null 排到後面
        if (bv == null) return -1;
        if (typeof av === "number" && typeof bv === "number") return av - bv;
        if (av instanceof Date && bv instanceof Date) return av.getTime() - bv.getTime();
        // 字串：自然序（IP / 含數字字串依數值排，而非逐字元）
        return cmpNatural(av, bv);
      },
    };
  });
}
