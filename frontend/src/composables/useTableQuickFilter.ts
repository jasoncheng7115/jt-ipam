/**
 * 表格即時篩選：把已載入的 rows 依關鍵字做跨欄位子字串比對（不分大小寫）。
 * 用法：
 *   const { query, filtered } = useTableQuickFilter(rows);
 *   <n-input v-model:value="query" ... />
 *   <n-data-table :data="filtered" ... />
 */
import { computed, ref, type Ref } from "vue";

export function useTableQuickFilter<T extends Record<string, any>>(rows: Ref<T[]>) {
  const query = ref("");
  const filtered = computed<T[]>(() => {
    const q = query.value.trim().toLowerCase();
    if (!q) return rows.value;
    return rows.value.filter((r) => {
      for (const v of Object.values(r)) {
        if (v == null) continue;
        if (typeof v === "object") continue;   // 跳過巢狀物件，只比對純值
        if (String(v).toLowerCase().includes(q)) return true;
      }
      return false;
    });
  });
  return { query, filtered };
}
