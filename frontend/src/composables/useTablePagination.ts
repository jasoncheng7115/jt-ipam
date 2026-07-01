import { reactive } from "vue";
import { useI18n } from "vue-i18n";
import { useUiStore } from "@/stores/ui";

/**
 * 全站共用的表格分頁設定。每頁筆數綁定使用者偏好（ui store / 後端 user_preferences.page_size），
 * 透過 size picker 變更會即時套用並寫回後端、跨裝置同步。
 *
 * 只控制 pageSize；page 維持 NDataTable 內部 uncontrolled，
 * 因此同一物件可安全給多張表共用（各表各自記住自己的頁碼，但共用每頁筆數）。
 *
 * 用法（client-side 分頁的 NDataTable）：
 *   const pg = useTablePagination();
 *   <n-data-table :pagination="pg" ... />
 */
export function useTablePagination(
  extra: Record<string, unknown> = {},
) {
  const ui = useUiStore();
  const { t } = useI18n();
  const pg = reactive({
    pageSize: ui.pageSize,
    showSizePicker: true,
    pageSizes: [10, 20, 50, 100, 200, 500],
    // 分頁列最左顯示總筆數（client-side 分頁時 naive 自動帶入 itemCount）
    prefix: (info: { itemCount?: number }) => t("common.total_rows", { n: info.itemCount ?? 0 }),
    onUpdatePageSize: (ps: number) => {
      pg.pageSize = ps;
      ui.setPageSize(ps);
    },
    ...extra,
  });
  return pg;
}
