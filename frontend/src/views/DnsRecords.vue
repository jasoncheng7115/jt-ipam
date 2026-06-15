<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NInput, NButton, NIcon, NTag, NDataTable, NCheckbox, NAlert, NSelect,
  useMessage, type DataTableColumns, type SelectOption,
} from "naive-ui";
import { DnsIcon, SearchIcon, RefreshIcon } from "@/icons";
import { listDnsRecords, listDNSServers, listDnsRecordTypeCounts, type DnsRecord } from "@/api/integrations";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTablePagination } from "@/composables/useTablePagination";
import ColumnPicker from "@/components/ColumnPicker.vue";

const { t } = useI18n();
const msg = useMessage();
const router = useRouter();
const route = useRoute();
const pg = useTablePagination();

const rows = ref<DnsRecord[]>([]);
const total = ref(0);
const loading = ref(false);
const q = ref("");
const ipLookup = ref("");
const missingOnly = ref(false);
const serverId = ref<string | null>(null);
const serverOptions = ref<SelectOption[]>([]);
const rtype = ref<string | null>(null);
const typeOptions = ref<SelectOption[]>([{ label: t("dns_records.all_types"), value: "" }]);

// 型別下拉帶各型別筆數，例如 A (12)；筆數套用除「型別」外的相同篩選
async function loadTypeCounts() {
  try {
    const counts = await listDnsRecordTypeCounts({
      q: q.value.trim() || undefined,
      ip: ipLookup.value.trim() || undefined,
      missing_ip: missingOnly.value || undefined,
      server_id: serverId.value || undefined,
    });
    const total = counts.reduce((s, c) => s + c.count, 0);
    typeOptions.value = [
      { label: `${t("dns_records.all_types")} (${total})`, value: "" },
      ...counts.map((c) => ({ label: `${c.type} (${c.count})`, value: c.type })),
    ];
  } catch {
    typeOptions.value = [{ label: t("dns_records.all_types"), value: "" }];
  }
}

const ALL_KEYS = ["name", "type", "value", "matched_ip_id", "consistency_state", "ttl", "source"];
const { visibleKeys, setVisible, reset, isVisible } = useColumnPrefs(
  "dns_records", ALL_KEYS, ALL_KEYS,
);
const pickerCols = computed(() => [
  { key: "name", label: t("dns_records.col_name") },
  { key: "type", label: t("dns_records.col_type") },
  { key: "value", label: t("dns_records.col_value") },
  { key: "matched_ip_id", label: t("dns_records.col_has_ip") },
  { key: "consistency_state", label: t("dns_records.col_consistency") },
  { key: "ttl", label: "TTL" },
  { key: "source", label: t("dns_records.col_source") },
]);

async function loadServers() {
  try {
    const res = await listDNSServers();
    serverOptions.value = [
      { label: t("dns_records.all_servers"), value: "" },
      ...res.items.map((s) => ({ label: s.name, value: s.id })),
    ];
  } catch {
    serverOptions.value = [{ label: t("dns_records.all_servers"), value: "" }];
  }
}

async function load() {
  loading.value = true;
  try {
    const res = await listDnsRecords({
      q: q.value.trim() || undefined,
      ip: ipLookup.value.trim() || undefined,
      missing_ip: missingOnly.value || undefined,
      server_id: serverId.value || undefined,
      rtype: rtype.value || undefined,
      page: 1, page_size: 500,
    });
    rows.value = res.items;
    total.value = res.total;
    void loadTypeCounts();
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

const consistencyTag = (s: string): { type: any; label: string } => {
  if (s === "consistent") return { type: "success", label: t("dns_records.c_consistent") };
  if (s === "dns_only") return { type: "warning", label: t("dns_records.c_dns_only") };
  if (s === "ipam_only") return { type: "info", label: t("dns_records.c_ipam_only") };
  if (s === "mismatch") return { type: "error", label: t("dns_records.c_mismatch") };
  return { type: "default", label: s };
};

const allColumns = computed<DataTableColumns<DnsRecord>>(() => [
  { title: t("dns_records.col_name"), key: "name", minWidth: 220, ellipsis: { tooltip: true } },
  { title: t("dns_records.col_type"), key: "type", width: 80,
    render: (r) => h(NTag, { size: "small", bordered: false }, () => r.type) },
  { title: t("dns_records.col_value"), key: "value", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => h("span", { style: "font-family: monospace" }, r.value) },
  { title: t("dns_records.col_has_ip"), key: "matched_ip_id", width: 150,
    render: (r) => {
      if (r.matched_ip_id) {
        return h("a", {
          style: "color: var(--ok,#36ad6a); cursor: pointer",
          onClick: () => router.push({ name: "address-detail", params: { id: r.matched_ip_id } }),
        }, "✓ " + t("dns_records.has_ip"));
      }
      // A/AAAA 沒對應 IP → 紅字提醒；其他型別顯示 —
      return (r.type === "A" || r.type === "AAAA")
        ? h("span", { style: "color: #d03050" }, "✗ " + t("dns_records.no_ip"))
        : "—";
    } },
  { title: t("dns_records.col_consistency"), key: "consistency_state", width: 120,
    render: (r) => { const c = consistencyTag(r.consistency_state); return h(NTag, { size: "small", type: c.type, bordered: false }, () => c.label); } },
  { title: "TTL", key: "ttl", width: 90 },
  { title: t("dns_records.col_source"), key: "source", width: 150, ellipsis: { tooltip: true },
    render: (r) => r.server_name || r.source || "—" },
]);

const columns = computed<DataTableColumns<DnsRecord>>(() =>
  autoSort(allColumns.value.filter((c) => isVisible((c as any).key))));

onMounted(() => {
  // 從全域搜尋點 DNS 記錄進來時，帶 ?q= 把該記錄名稱代入搜尋欄
  const qq = route.query.q;
  if (typeof qq === "string" && qq.trim()) q.value = qq.trim();
  loadServers();
  load();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><DnsIcon /></n-icon>
        <span>{{ t("dns_records.title") }} ({{ total }})</span>
      </n-space>
    </template>
    <n-space vertical :size="14">
      <n-alert type="info" :show-icon="true">{{ t("dns_records.intro") }}</n-alert>

      <n-space align="center" :wrap="true">
        <n-select v-model:value="serverId" :options="serverOptions" style="width: 150px"
                  :consistent-menu-width="false"
                  :placeholder="t('dns_records.all_servers')" @update:value="load" />
        <n-select v-model:value="rtype" :options="typeOptions" style="width: 110px"
                  :consistent-menu-width="false"
                  :placeholder="t('dns_records.all_types')" @update:value="load" />
        <n-input v-model:value="q" clearable style="width: 170px" :placeholder="t('dns_records.search_ph')"
                 @keyup.enter="load">
          <template #prefix><n-icon><SearchIcon /></n-icon></template>
        </n-input>
        <n-input v-model:value="ipLookup" clearable style="width: 170px"
                 :placeholder="t('dns_records.ip_lookup_ph')" @keyup.enter="load" />
        <n-checkbox v-model:checked="missingOnly" @update:checked="load">
          {{ t("dns_records.only_missing") }}
        </n-checkbox>
        <n-button type="primary" size="small" @click="load">
          <template #icon><n-icon><SearchIcon /></n-icon></template>
          {{ t("common.search") }}
        </n-button>
        <n-button size="small" :loading="loading" @click="load">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>
          {{ t("common.refresh") }}
        </n-button>
        <ColumnPicker :all="pickerCols" :visible="visibleKeys"
                      @update:visible="setVisible" @reset="reset" />
      </n-space>

      <n-data-table
        :columns="columns" :data="rows" :loading="loading" size="small"
        :pagination="pg" :row-key="(r: DnsRecord) => r.id"
      />
    </n-space>
  </n-card>
</template>
