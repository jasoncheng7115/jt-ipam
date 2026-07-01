/**
 * 表格即時篩選：把已載入的 rows 依關鍵字做跨欄位子字串比對（不分大小寫）。
 * 用法：
 *   const { query, filtered } = useTableQuickFilter(rows);
 *   <n-input v-model:value="query" ... />
 *   <n-data-table :data="filtered" ... />
 */
import { computed, ref, type Ref } from "vue";

/**
 * @param getFields 選用：只比對這些欄位（通常傳「目前顯示的欄位」），避免子字串誤中內部數值欄位
 *                  （例如查 "102" 不該命中 memory_mb=1024）。不傳則沿用跨所有純值欄位的行為。
 */
export function useTableQuickFilter<T extends Record<string, any>>(
  rows: Ref<T[]>, getFields?: () => string[],
) {
  const query = ref("");
  const filtered = computed<T[]>(() => {
    const q = query.value.trim().toLowerCase();
    if (!q) return rows.value;
    const fields = getFields?.();
    return rows.value.filter((r) => {
      const values = fields && fields.length ? fields.map((k) => r[k]) : Object.values(r);
      for (const v of values) {
        if (v == null) continue;
        if (Array.isArray(v)) {   // 純值陣列（如 IP / MAC 多筆）：任一元素命中即可
          if (v.some((x) => x != null && typeof x !== "object" && String(x).toLowerCase().includes(q))) return true;
          continue;
        }
        if (typeof v === "object") continue;   // 跳過巢狀物件，只比對純值
        if (String(v).toLowerCase().includes(q)) return true;
      }
      return false;
    });
  });
  return { query, filtered };
}
