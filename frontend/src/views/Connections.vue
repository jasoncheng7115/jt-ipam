<script setup lang="ts">
/** 進階 → 連線管理：列出所有已啟用 SSH 且本人可連線的目標，可排序/篩選/選欄位/匯出。 */
import { computed, h, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NInput, NButton, NIcon, NDataTable, NButtonGroup, NDropdown,
  NSelect, useMessage, type DataTableColumns,
} from "naive-ui";
import { listSshTargets } from "@/api/ssh";
import { TerminalIcon, ChevronDownIcon, OpenNewWindowIcon, RefreshIcon, SearchIcon } from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTablePagination } from "@/composables/useTablePagination";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useCustomers } from "@/composables/useCustomers";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import OsIcon from "@/components/OsIcon.vue";
import { renderIcon } from "@/icons";
import type { IPAddress } from "@/types";

const { t } = useI18n();
const router = useRouter();
const msg = useMessage();
const links = useEntityLinks(router);
const { labelFor, ensureLoaded: ensureCustomers } = useCustomers();
const pg = useTablePagination();

const rows = ref<IPAddress[]>([]);
const loading = ref(false);
const { query, filtered } = useTableQuickFilter(rows);

// 工具列篩選：連線類型（目前只有 SSH）＋ OS
const typeFilter = ref<string | null>(null);
const osFilter = ref<string | null>(null);
const typeOptions = [{ label: "SSH", value: "ssh" }];
const osOptions = computed(() => {
  const seen = new Map<string, string>();
  for (const r of rows.value) {
    const v = r.os_guess || r.os_family;
    if (v && !seen.has(v)) seen.set(v, v);
  }
  return [...seen.keys()].sort().map((v) => ({ label: v, value: v }));
});
const displayRows = computed(() =>
  filtered.value.filter((r) => {
    if (osFilter.value && (r.os_guess || r.os_family) !== osFilter.value) return false;
    // 目前所有目標都是 SSH；type 下拉為前瞻（之後加 RDP 等）
    if (typeFilter.value && typeFilter.value !== "ssh") return false;
    return true;
  }));

// 寬度不夠時操作欄按鈕只顯示 icon（量測卡片容器寬度）
const rootRef = ref<any>(null);
const compact = ref(false);
let ro: ResizeObserver | null = null;

function sshHref(row: IPAddress) {
  return router.resolve({ name: "ssh-console", params: { id: row.id } }).href;
}
function openTab(row: IPAddress) { window.open(sshHref(row), "_blank"); }
function openWin(row: IPAddress) { window.open(sshHref(row), `ssh-${row.id}`, "width=960,height=640"); }
const sshRowMenu = [{ label: t("ssh.open_popout"), key: "popout", icon: renderIcon(OpenNewWindowIcon) }];
function onRowMenu(key: string, row: IPAddress) { if (key === "popout") openWin(row); }

async function refresh() {
  loading.value = true;
  try { rows.value = await listSshTargets(); }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
onMounted(() => {
  void refresh();
  void ensureCustomers();   // 確保「單位」名稱可解析（否則 labelFor 退回顯示 UUID 片段）
  const el = rootRef.value?.$el as HTMLElement | undefined;
  if (el) {
    ro = new ResizeObserver(() => { compact.value = el.clientWidth < 860; });
    ro.observe(el);
  }
});
onBeforeUnmount(() => { ro?.disconnect(); ro = null; });

const { visibleKeys, setVisible, reset, isVisible } = useColumnPrefs(
  "connections",
  ["ip", "hostname", "unit", "device", "os", "status", "actions"],
  ["ip", "hostname", "unit", "device", "os", "status", "actions"],
);
const pickerCols = [
  { key: "ip", label: t("connections.col_ip") },
  { key: "hostname", label: t("connections.col_hostname") },
  { key: "unit", label: t("connections.col_unit") },
  { key: "device", label: t("connections.col_device") },
  { key: "os", label: t("connections.col_os") },
  { key: "status", label: t("connections.col_status") },
];

const allColumns = computed<DataTableColumns<IPAddress>>(() => {
  const cz = compact.value;   // 讓此 computed 隨 compact 變動重算 → 表格重繪
  return [
    { title: "", key: "status", width: 44, align: "center", sorter: false,
      render: (r) => h(LiveStatusDot, { address: r }) },
    {
      title: t("connections.col_ip"), key: "ip", sorter: "default", width: 160,
      render: (r) => h("a", {
        style: "color:var(--n-color-target,#2080f0);cursor:pointer",
        onClick: () => router.push({ name: "address-detail", params: { id: r.id } }),
      }, r.ip),
    },
    { title: t("connections.col_hostname"), key: "hostname", sorter: "default",
      render: (r) => r.hostname || "—" },
    { title: t("connections.col_unit"), key: "unit", sorter: "default",
      render: (r) => labelFor(r.customer_id) || "—" },
    { title: t("connections.col_device"), key: "device", sorter: "default",
      render: (r) => (r.device_id ? links.device(r.device_id, r.device_name) : "—") },
    { title: t("connections.col_os"), key: "os", sorter: "default", minWidth: 150,
      render: (r) => h("span",
        { style: "display:inline-flex;align-items:center;gap:5px;white-space:nowrap" },
        [h(OsIcon, { family: r.os_family }), r.os_guess || "—"]) },
    {
      title: t("connections.col_actions"), key: "actions", width: cz ? 84 : 124,
      render: (r) => h(NButtonGroup, {}, () => [
        h(NButton, {
          type: "info", size: "small", title: t("ssh.connect"), onClick: () => openTab(r),
        }, cz
          ? { icon: () => h(NIcon, null, () => h(TerminalIcon)) }
          : { icon: () => h(NIcon, null, () => h(TerminalIcon)), default: () => "SSH" }),
        h(NDropdown, {
          trigger: "click", options: sshRowMenu,
          onSelect: (k: string) => onRowMenu(k, r),
        }, () => h(NButton, { type: "info", size: "small", style: "padding:0 2px" },
          { icon: () => h(NIcon, null, () => h(ChevronDownIcon)) })),
      ]),
    },
  ];
});
const columns = computed(() =>
  autoSort(allColumns.value.filter((c) => isVisible((c as any).key))));
</script>

<template>
  <n-card ref="rootRef" :bordered="false">
    <template #header>
      <span style="display:flex;align-items:center;gap:8px">
        <n-icon :component="TerminalIcon" :size="20" />
        <span>{{ t("nav.connections") }}</span>
      </span>
    </template>

    <p style="margin:0 0 12px;opacity:.7;font-size:13px">{{ t("connections.hint") }}</p>

    <!-- 工具列：放在表格上方（搜尋 / 選欄位 / 匯出 / 重新整理） -->
    <n-space align="center" :size="8" style="margin-bottom: 12px" :wrap="true">
      <n-input v-model:value="query" clearable :placeholder="t('common.search')" style="width: 220px">
        <template #prefix><n-icon :component="SearchIcon" /></template>
      </n-input>
      <n-select v-model:value="typeFilter" :options="typeOptions" clearable
                :placeholder="t('connections.filter_type')" style="width: 130px" />
      <n-select v-model:value="osFilter" :options="osOptions" clearable
                :placeholder="t('connections.filter_os')" style="width: 170px" />
      <ColumnPicker :all="pickerCols" :visible="visibleKeys"
                    @update:visible="setVisible" @reset="reset" />
      <ExportButton :columns="allColumns" :rows="displayRows" filename="ssh-connections"
                    :title="t('nav.connections')" />
      <n-button size="small" @click="refresh">
        <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("common.refresh") }}
      </n-button>
    </n-space>

    <n-data-table
      :columns="columns" :data="displayRows" :loading="loading"
      :pagination="pg" :row-key="(r: IPAddress) => r.id"
      size="small" :bordered="false" />
  </n-card>
</template>

<style scoped>
/* 卡片標題 icon+文字垂直置中（覆蓋主題預設，避免內容偏上） */
:deep(.n-card > .n-card-header) { display: flex; align-items: center; padding-top: 12px; padding-bottom: 12px; }
</style>
