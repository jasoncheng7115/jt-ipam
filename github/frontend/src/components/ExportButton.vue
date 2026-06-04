<script setup lang="ts">
/**
 * 通用表格匯出按鈕：丟進任何 n-data-table 的 columns 與資料即可。
 * 支援 CSV / Markdown / PDF / ODS / ODT，全部前端產生、零相依。
 */
import { computed, ref } from "vue";
import { NButton, NDropdown, NIcon, useMessage } from "naive-ui";
import { useI18n } from "vue-i18n";
import { ExportIcon } from "@/icons";
import { columnsForExport, exportTable, type ExportColumn, type ExportFormat } from "@/utils/tableExport";

const props = defineProps<{
  /** 直接給 n-data-table 的 columns（會自動萃取 key/label，略過勾選與操作欄），或自備 {key,label}[] */
  columns: any[];
  rows: Record<string, any>[];
  /** 檔名（不含副檔名） */
  filename: string;
  /** PDF/ODT 標題，預設用 filename */
  title?: string;
  size?: "tiny" | "small" | "medium";
  /**
   * remote 分頁清單用：提供此 callback 時，匯出會呼叫它抓「全資料集」再匯出
   * （而非只匯出畫面當頁的 rows）。未提供則用 rows。
   */
  fetchAll?: () => Promise<Record<string, any>[]>;
}>();

const { t } = useI18n();
const msg = useMessage();
const loading = ref(false);

const exportCols = computed<ExportColumn[]>(() => {
  // 已是 {key,label} 形狀就直接用，否則當作 DataTable columns 萃取
  if (props.columns.length && "label" in props.columns[0] && "key" in props.columns[0]
      && !("title" in props.columns[0])) {
    return props.columns as ExportColumn[];
  }
  return columnsForExport(props.columns);
});

const options = [
  { label: "CSV", key: "csv" },
  { label: "Markdown (.md)", key: "md" },
  { label: "PDF", key: "pdf" },
  { label: "OpenDocument 試算表 (.ods)", key: "ods" },
  { label: "OpenDocument 文件 (.odt)", key: "odt" },
];

async function onSelect(key: ExportFormat) {
  if (loading.value) return;
  try {
    let data = props.rows;
    if (props.fetchAll) {
      loading.value = true;
      data = await props.fetchAll();   // 抓全資料集（remote 分頁）
    }
    if (!data.length) {
      msg.warning(t("export.empty"));
      return;
    }
    exportTable(key, props.filename, exportCols.value, data, props.title);
    if (key === "pdf") msg.info(t("export.pdf_hint"));
  } catch (e: any) {
    msg.error(e?.message === "popup blocked" ? t("export.popup_blocked") : t("export.failed"));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <n-dropdown trigger="click" :options="options" :disabled="loading" @select="onSelect">
    <n-button :size="size" quaternary :loading="loading">
      <template #icon><n-icon><ExportIcon /></n-icon></template>
      {{ t("export.label") }}
    </n-button>
  </n-dropdown>
</template>
