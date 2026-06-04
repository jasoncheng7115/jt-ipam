<script setup lang="ts">
/**
 * 全域 IP 異動記錄 (feature B)：搜尋 / 篩選 / 分頁。
 * 後端 GET /api/v1/ip-changes，依 subnet 可見性過濾。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import {
  NButton, NCard, NDataTable, NInput, NPagination, NSelect, NSpace, NTag, NText,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import { useI18n } from "vue-i18n";
import {
  listIpChanges, IP_CHANGE_EVENT_TYPES, IP_CHANGE_SOURCES,
  type IPChangeLog,
} from "@/api/ip_history";
import { fmtDateTime } from "@/utils/datetime";

const { t } = useI18n();

const rows = ref<IPChangeLog[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(50);
const loading = ref(false);

const q = ref("");
const eventType = ref<string | null>(null);
const source = ref<string | null>(null);

const eventOptions = computed(() => [
  { label: t("ipChanges.all_events"), value: "" },
  ...IP_CHANGE_EVENT_TYPES.map((e) => ({ label: t(`ipChanges.event.${e}`), value: e })),
]);
const sourceOptions = computed(() => [
  { label: t("ipChanges.all_sources"), value: "" },
  ...IP_CHANGE_SOURCES.map((s) => ({ label: s, value: s })),
]);

// 事件 → tag 顏色
const EVENT_TYPE: Record<string, "default" | "info" | "success" | "warning" | "error"> = {
  created: "success", deleted: "error",
  online: "success", offline: "warning",
  hostname_changed: "info", mac_changed: "info", arp_changed: "info",
  state_changed: "warning", edited: "default",
};

async function load() {
  loading.value = true;
  try {
    const r = await listIpChanges({
      q: q.value.trim() || undefined,
      event_type: eventType.value || undefined,
      source: source.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    });
    rows.value = r.items;
    total.value = r.total;
  } finally {
    loading.value = false;
  }
}

// 篩選改變 → 回到第一頁重查
let timer: ReturnType<typeof setTimeout> | null = null;
watch([q, eventType, source], () => {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { page.value = 1; load(); }, 300);
});
watch([page, pageSize], load);
onMounted(load);

const columns = computed<DataTableColumns<IPChangeLog>>(() => [
  {
    title: t("ipChanges.col_time"), key: "created_at", width: 170,
    render: (r) => fmtDateTime(r.created_at),
  },
  { title: t("ipChanges.col_ip"), key: "ip_text", width: 150, ellipsis: { tooltip: true } },
  {
    title: t("ipChanges.col_event"), key: "event_type", width: 130,
    render: (r) => h(NTag, { size: "small", type: EVENT_TYPE[r.event_type] ?? "default", bordered: false },
      { default: () => t(`ipChanges.event.${r.event_type}`) }),
  },
  { title: t("ipChanges.col_field"), key: "field", width: 110, render: (r) => r.field ?? "—" },
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
    render: (r) => h(NTag, { size: "small", bordered: false }, { default: () => r.source }),
  },
  {
    title: t("ipChanges.col_actor"), key: "actor", width: 120,
    render: (r) => r.actor_username ?? (r.source === "manual" ? "—" : r.source),
  },
]);
</script>

<template>
  <n-card :title="t('ipChanges.title')" :bordered="false">
    <template #header-extra>
      <n-text depth="3" style="font-size: 12px">{{ t("ipChanges.subtitle") }}</n-text>
    </template>

    <n-space align="center" style="margin-bottom: 12px; flex-wrap: wrap">
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
      <n-button @click="load" :loading="loading">{{ t("common.refresh") }}</n-button>
    </n-space>

    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      :bordered="false"
      size="small"
      :scroll-x="1000"
    />

    <div style="display: flex; justify-content: flex-end; margin-top: 12px">
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
