<script setup lang="ts">
/**
 * 全域 IP 異動記錄 (feature B)：搜尋 / 篩選 / 分頁。
 * 後端 GET /api/v1/ip-changes，依 subnet 可見性過濾。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import {
  NButton, NCard, NDataTable, NIcon, NInput, NPagination, NSelect, NSpace, NTag, NText,
} from "naive-ui";
import { IPChangesIcon, RefreshIcon } from "@/icons";
import type { DataTableColumns } from "naive-ui";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  listIpChanges, IP_CHANGE_EVENT_TYPES, IP_CHANGE_SOURCES,
  type IPChangeLog,
} from "@/api/ip_history";
import { sourceLabel } from "@/utils/sourceLabel";
import { fmtDateTime } from "@/utils/datetime";
import { useChangeLogDim } from "@/composables/useChangeLogDim";
import ExportButton from "@/components/ExportButton.vue";
import { listSubnets } from "@/api/subnets";
import { listSections } from "@/api/sections";
import { useCustomers } from "@/composables/useCustomers";

const { t } = useI18n();
const router = useRouter();

/** 沒有對應翻譯的事件型別（舊資料、未來新增的）直接顯示原值，
 *  不要把 `ipChanges.event.xxx` 這種鍵名露在畫面上。與 IPAddressEditModal 同一套作法。 */
function eventLabel(e: string): string {
  const key = `ipChanges.event.${e}`;
  const out = t(key);
  return out === key ? e : out;
}
const { isOld: isOldLog } = useChangeLogDim();

const rows = ref<IPChangeLog[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(50);
const loading = ref(false);

// 欄位標題排序（使用者 2026-10-12）：伺服器端分頁，排序交給後端；沒選＝時間新的在前
const sortField = ref<string | null>(null);
const sortDir = ref<"asc" | "desc">("desc");
/** 表格欄位 key → 後端的排序鍵 */
const SORT_KEYS: Record<string, string> = {
  created_at: "created_at", ip_text: "ip", event_type: "event_type", field: "field", source: "source", actor: "actor",
};
function handleSorter(s: { columnKey?: string | number; order?: "ascend" | "descend" | false } | null) {
  const key = s && s.order ? SORT_KEYS[String(s.columnKey)] : undefined;
  sortField.value = key ?? null;
  sortDir.value = s?.order === "ascend" ? "asc" : "desc";
  page.value = 1;
  void load();
}
/** 欄位上顯示的排序狀態（受控）；沒選排序時時間欄顯示「新的在前」 */
function sortOrderOf(columnKey: string): "ascend" | "descend" | false {
  const key = SORT_KEYS[columnKey];
  if (!sortField.value) return columnKey === "created_at" ? "descend" : false;
  return sortField.value === key ? (sortDir.value === "asc" ? "ascend" : "descend") : false;
}

const q = ref("");
const eventType = ref<string | null>(null);
const source = ref<string | null>(null);
// 範圍篩選：區段 / 子網路 / 單位。三個各自獨立（後端是 AND），
// 因為現場的問法是「這個單位這週有什麼變動」也可能是「這個網段有什麼變動」。
const sectionId = ref<string | null>(null);
const subnetId = ref<string | null>(null);
const customerId = ref<string | null>(null);

const subnetOptions = ref<{ label: string; value: string; section_id: string }[]>([]);
const sectionOptions = ref<{ label: string; value: string }[]>([]);
const { options: customerOptions, ensureLoaded: loadCustomers } = useCustomers();

// 選了區段就只列該區段底下的子網路 —— 兩個下拉互相矛盾時，
// 使用者會以為篩選壞了（實際上是 AND 之後沒有交集）
const visibleSubnetOptions = computed(() => sectionId.value
  ? subnetOptions.value.filter((o) => o.section_id === sectionId.value)
  : subnetOptions.value);

async function loadFilterOptions() {
  try {
    // 一次抓完：清單頁只拿第一頁會讓下拉少東西，而且完全看不出來
    const [subs, secs] = await Promise.all([
      listSubnets({ page: 1, pageSize: 500 }),
      listSections(1, 500),
    ]);
    subnetOptions.value = subs.items.map((x) => ({
      label: x.description ? `${x.cidr} — ${x.description}` : x.cidr,
      value: x.id, section_id: x.section_id,
    }));
    sectionOptions.value = secs.items.map((x) => ({ label: x.name, value: x.id }));
  } catch { /* 下拉載不到不影響主要清單 */ }
  // 單位清單一般帳號也讀得到，但失敗就當作沒有這個篩選，不要讓整頁報錯
  try { await loadCustomers(); } catch { /* silent */ }
}

const eventOptions = computed(() => [
  { label: t("ipChanges.all_events"), value: "" },
  ...IP_CHANGE_EVENT_TYPES.map((e) => ({ label: eventLabel(e), value: e })),
]);
const sourceOptions = computed(() => [
  { label: t("ipChanges.all_sources"), value: "" },
  ...IP_CHANGE_SOURCES.map((s) => ({ label: sourceLabel(t, s), value: s })),
]);

// 事件 → tag 顏色
const EVENT_TYPE: Record<string, "default" | "info" | "success" | "warning" | "error"> = {
  created: "success", deleted: "error",
  online: "success", offline: "warning",
  hostname_changed: "info", mac_changed: "info", arp_changed: "info",
  os_changed: "warning", kind_changed: "warning",
  state_changed: "warning", edited: "default",
};

async function load() {
  loading.value = true;
  try {
    const r = await listIpChanges({
      q: q.value.trim() || undefined,
      event_type: eventType.value || undefined,
      source: source.value || undefined,
      section_id: sectionId.value || undefined,
      subnet_id: subnetId.value || undefined,
      customer_id: customerId.value || undefined,
      page: page.value,
      page_size: pageSize.value,
      sort: sortField.value || undefined,
      order: sortField.value ? sortDir.value : undefined,
    });
    rows.value = r.items;
    total.value = r.total;
  } finally {
    loading.value = false;
  }
}

// 篩選改變 → 回到第一頁重查
let timer: ReturnType<typeof setTimeout> | null = null;
watch(sectionId, () => {
  // 換區段時，原本選的子網路可能不屬於它 —— 留著會變成「查不到任何資料」的無聲陷阱
  if (subnetId.value && !visibleSubnetOptions.value.some((o) => o.value === subnetId.value)) {
    subnetId.value = null;
  }
});
watch([q, eventType, source, sectionId, subnetId, customerId], () => {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { page.value = 1; load(); }, 300);
});
watch([page, pageSize], load);
onMounted(() => { void load(); void loadFilterOptions(); });

const columns = computed<DataTableColumns<IPChangeLog>>(() => [
  {
    title: t("ipChanges.col_time"), key: "created_at", width: 170,
    sorter: true, sortOrder: sortOrderOf("created_at"),
    render: (r) => fmtDateTime(r.created_at),
  },
  {
    // IP 點進去看詳細資料（使用者 2026-10-12）；IP 已刪除（ip_id 是空的）就只顯示文字
    title: t("ipChanges.col_ip"), key: "ip_text", width: 150, ellipsis: { tooltip: true },
    sorter: true, sortOrder: sortOrderOf("ip_text"),
    render: (r) => !r.ip_id ? r.ip_text : h("a", {
      href: router.resolve({ name: "address-detail", params: { id: r.ip_id } }).href,
      class: "ipchg-ip-link",
      onClick: (e: MouseEvent) => {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;   // 新分頁開啟照瀏覽器預設
        e.preventDefault();
        void router.push({ name: "address-detail", params: { id: r.ip_id as string } });
      },
    }, r.ip_text),
  },
  {
    title: t("ipChanges.col_event"), key: "event_type", width: 130,
    sorter: true, sortOrder: sortOrderOf("event_type"),
    render: (r) => h(NTag, { size: "small", type: EVENT_TYPE[r.event_type] ?? "default", bordered: false },
      { default: () => eventLabel(r.event_type) }),
  },
  { title: t("ipChanges.col_field"), key: "field", width: 110, sorter: true, sortOrder: sortOrderOf("field"),
    render: (r) => r.field ?? "—" },
  {
    title: t("ipChanges.col_change"), key: "change", minWidth: 220,
    render: (r) => {
      if (r.old_value == null && r.new_value == null) return "—";
      return h("span", {}, [
        h(NText, { depth: 3, delete: true }, { default: () => r.old_value ?? "∅" }),
        " → ",
        h(NText, { strong: true }, { default: () => r.new_value ?? "∅" }),
      ]);
    },
  },
  {
    title: t("ipChanges.col_source"), key: "source", width: 100,
    sorter: true, sortOrder: sortOrderOf("source"),
    render: (r) => h(NTag, { size: "small", bordered: false }, { default: () => sourceLabel(t, r.source) }),
  },
  {
    title: t("ipChanges.col_actor"), key: "actor", width: 120,
    sorter: true, sortOrder: sortOrderOf("actor"),
    render: (r) => r.actor_username ?? (r.source === "manual" ? "—" : sourceLabel(t, r.source)),
  },
]);
</script>

<template>
  <n-card :bordered="false">
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><IPChangesIcon /></n-icon>
        <span>{{ t("ipChanges.title") }}</span>
      </n-space>
    </template>
    <!-- 卡片標題列只放標題。說明文字與動作都屬於內容區：
         說明自成一行，動作靠右與篩選同一列 —— 動作不會自己佔掉一整條空帶，
         也不會因為多加幾個篩選就被擠到下一行單獨一個。 -->
    <n-text depth="3" class="ipchg-subtitle">{{ t("ipChanges.subtitle") }}</n-text>

    <n-space align="center" class="ipchg-toolbar">
      <n-input
        v-model:value="q" clearable
        :placeholder="t('ipChanges.search_placeholder')"
        style="width: 280px"
      />
      <n-select
        v-model:value="eventType" :options="eventOptions"
        clearable style="width: 160px"
        :placeholder="t('ipChanges.all_events')"
      />
      <n-select
        v-model:value="source" :options="sourceOptions"
        clearable style="width: 140px"
        :placeholder="t('ipChanges.all_sources')"
      />
      <n-select
        v-model:value="sectionId" :options="sectionOptions"
        clearable filterable style="width: 160px"
        :placeholder="t('ipChanges.all_sections')"
      />
      <n-select
        v-model:value="subnetId" :options="visibleSubnetOptions"
        clearable filterable style="width: 200px"
        :placeholder="t('ipChanges.all_subnets')"
      />
      <n-select
        v-if="customerOptions.length"
        v-model:value="customerId" :options="customerOptions"
        clearable filterable style="width: 170px"
        :placeholder="t('ipChanges.all_customers')"
      />
      <!-- 兩顆動作綁成同一個 flex 子項：換行時要一起換，
           不然會出現「重新整理留在上一行、匯出自己掉到下一行」的破碎排法 -->
      <n-space align="center" :size="8" :wrap-item="false">
        <n-button @click="load" :loading="loading"><template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}</n-button>
        <ExportButton :columns="columns" :rows="rows" filename="ip-changes"
                      :title="t('nav.ip_changes')" />
      </n-space>
    </n-space>

    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      :bordered="false"
      size="small"
      :scroll-x="1000"
      remote
      :row-class-name="(r:any) => isOldLog(r.created_at) ? 'log-dim' : ''"
      @update:sorter="handleSorter"
    />

    <!-- 手機上放不下時分頁換到下一行，「共 N 筆」不被擠成直排 -->
    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 12px; flex-wrap: wrap; gap: 8px">
      <span style="font-size: 13px; opacity: 0.7; white-space: nowrap">{{ t("common.total_rows", { n: total }) }}</span>
      <n-pagination
        v-model:page="page"
        v-model:page-size="pageSize"
        :item-count="total"
        :page-sizes="[20, 50, 100, 200]"
        show-size-picker
      />
    </div>
  </n-card>
</template>

<style scoped>
.ipchg-subtitle { display: block; font-size: 12px; margin-bottom: 10px; }
/* 篩選與動作在同一個會換行的群組裡，依序排下去。
   刻意不做「動作靠右」：靠右在寬度不夠時會自己換到一整列去，
   畫面上就多一條只有兩顆按鈕的空帶（拓樸圖原本就是這樣，使用者回報過）。 */
.ipchg-toolbar { flex-wrap: wrap; row-gap: 8px; margin-bottom: 12px; }

/* IP 連到詳細資料：render 出來的儲存格吃不到 scoped 樣式，用 :deep */
:deep(a.ipchg-ip-link) { color: var(--primary-color, #18a058); text-decoration: none; }
:deep(a.ipchg-ip-link:hover) { text-decoration: underline; }

/* 異動記錄超過 N 天（系統設定）的列以淡色顯示 */
:deep(tr.log-dim td) { opacity: .45; }
</style>
