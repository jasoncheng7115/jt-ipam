<script setup lang="ts">
import { h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NIcon,
  NInput,
  NCheckbox,
  NSelect,
  NButton,
  NTag,
  NPopconfirm,
  NTooltip,
  NProgress,
  useMessage,
  type DataTableColumns,
  type SelectOption,
  type DataTableRowKey,
} from "naive-ui";
import { listAddresses, bulkDeleteAddresses } from "@/api/addresses";
import { listSubnets, getSubnetUsage } from "@/api/subnets";
import type { SubnetUsage } from "@/types";
import { listSections } from "@/api/sections";
import { useCustomers } from "@/composables/useCustomers";
import type { IPAddress, Subnet } from "@/types";
import { AddressesIcon, RefreshIcon, DeleteIcon } from "@/icons";
import IPAddressEditModal from "@/components/IPAddressEditModal.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { computed } from "vue";

const { t } = useI18n();
const msg = useMessage();
const route = useRoute();
const router = useRouter();
const rows = ref<IPAddress[]>([]);
const total = ref(0);
const loading = ref(false);
const q = ref<string>(typeof route.query.q === "string" ? route.query.q : "");
const exactMatch = ref<boolean>(route.query.exact === "1" || route.query.exact === "true");
const subnetId = ref<string | null>(
  typeof route.query.subnet_id === "string" ? route.query.subnet_id : null,
);
const sectionId = ref<string | null>(
  typeof route.query.section_id === "string" ? route.query.section_id : null,
);
const customerId = ref<string | null>(
  typeof route.query.customer_id === "string" ? route.query.customer_id : null,
);
const subnets = ref<Subnet[]>([]);
const subnetOptions = ref<SelectOption[]>([]);
const subnetUsage = ref<SubnetUsage | null>(null);

async function loadSubnetUsage() {
  if (!subnetId.value) { subnetUsage.value = null; return; }
  try { subnetUsage.value = await getSubnetUsage(subnetId.value); }
  catch { subnetUsage.value = null; }
}
const usageStatus = computed<"success" | "warning" | "error">(() => {
  const p = subnetUsage.value?.used_pct ?? 0;
  return p >= 90 ? "error" : p >= 70 ? "warning" : "success";
});
const usageSubnetLabel = computed(() => {
  const s = subnets.value.find((x) => x.id === subnetId.value);
  if (!s) return subnetUsage.value?.cidr ?? "";
  return s.description ? `${s.cidr} — ${s.description}` : s.cidr;
});
const sectionOptions = ref<SelectOption[]>([]);
const { options: customerOptions, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const page = ref(1);
const pageSize = ref(100);

async function loadSubnets() {
  try {
    const res = await listSubnets({ page: 1, pageSize: 500 });
    subnets.value = res.items;
    subnetOptions.value = res.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr,
      value: s.id,
    }));
  } catch {}
}

async function loadSections() {
  try {
    const res = await listSections(1, 500);
    sectionOptions.value = res.items.map((s) => ({ label: s.name, value: s.id }));
  } catch {}
}

const selected = ref<IPAddress | null>(null);
const modalShow = ref(false);

const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

function openRow(row: IPAddress) {
  // 點既有 IP → 進獨立詳情頁（不再彈 modal）
  void router.push({ name: "address-detail", params: { id: row.id } });
}

function onSaved(updated: IPAddress) {
  selected.value = updated;
  const idx = rows.value.findIndex((r) => r.id === updated.id);
  if (idx >= 0) rows.value[idx] = updated;
}

function onDeleted(id: string) {
  rows.value = rows.value.filter((r) => r.id !== id);
  total.value = Math.max(0, total.value - 1);
  checkedKeys.value = checkedKeys.value.filter((k) => k !== id);
}

async function doBulkDelete() {
  if (!checkedKeys.value.length) return;
  bulkBusy.value = true;
  try {
    const ids = checkedKeys.value.map((k) => String(k));
    const res = await bulkDeleteAddresses(ids);
    if (res.failed > 0) {
      msg.warning(t("common.deleted_failed_items", { deleted: res.deleted, failed: res.failed }));
    } else {
      msg.success(t("common.deleted_n_items", { n: res.deleted }));
    }
    checkedKeys.value = [];
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    bulkBusy.value = false;
  }
}

let debounceTimer: ReturnType<typeof setTimeout> | null = null;
watch(q, (v) => {
  router.replace({ query: { ...route.query, q: v || undefined } }).catch(() => {});
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => { page.value = 1; void refresh(); }, 300);
});

watch(subnetId, (v) => {
  router.replace({ query: { ...route.query, subnet_id: v || undefined } }).catch(() => {});
  page.value = 1;
  void loadSubnetUsage();
  void refresh();
});

watch(sectionId, (v) => {
  router.replace({ query: { ...route.query, section_id: v || undefined } }).catch(() => {});
  page.value = 1;
  void refresh();
});

watch(customerId, (v) => {
  router.replace({ query: { ...route.query, customer_id: v || undefined } }).catch(() => {});
  page.value = 1;
  void refresh();
});

function stateLabel(state: string): string {
  const key = `addresses.state_${state}`;
  const out = t(key);
  return out === key ? state : out;
}
function statusTag(state: string) {
  const map: Record<string, "success" | "warning" | "error" | "default" | "info"> = {
    active: "success",
    reserved: "info",
    offline: "error",
    dhcp: "warning",
    used: "default",
  };
  return h(NTag, { type: map[state] ?? "default", size: "small" }, () => stateLabel(state));
}

function liveDot(r: IPAddress) {
  return h(LiveStatusDot, { address: r });
}

// 交換器位置：用 SwitchPortLabel 呈現 switch@port；非高信心 (uplink/trunk 學到) 用 dim 橘色斜體 + tooltip 說明
function switchPortCell(r: IPAddress) {
  if (!r.switch_port) return "";
  return h(NTooltip, null, {
    trigger: () => h(SwitchPortLabel, { value: r.switch_port, dim: r.switch_port_confident === false }),
    default: () => r.switch_port_confident === false
      ? t("addresses.switch_port_uncertain")
      : r.switch_port,
  });
}

// 來源代碼 → 翻譯 (manual / librenms / dns / opnsense / scanner …)；無對應則原樣
function labelSource(v: string | null | undefined): string {
  if (!v) return "";
  const key = `addresses.source_${v}`;
  const out = t(key);
  return out === key ? v : out;
}

const allColumns: DataTableColumns<IPAddress> = [
  { type: "selection" },
  { title: "", key: "live", width: 28, render: (r) => liveDot(r) },
  {
    title: () => t("addresses.ip"), key: "ip", width: 150,
    sorter: true,
  },
  {
    title: () => t("addresses.hostname"), key: "hostname", minWidth: 180, ellipsis: { tooltip: true },
    render: (r) => r.hostname ?? "—",
    sorter: true,
  },
  {
    title: () => t("addresses.mac"), key: "mac", width: 150,
    render: (r) => r.mac ?? "—",
    sorter: true,
  },
  {
    title: () => t("common.status"), key: "state", width: 110,
    render: (r) => statusTag(r.state),
    sorter: true,
  },
  {
    title: () => t("addresses.owner"), key: "owner", width: 150, ellipsis: { tooltip: true },
    render: (r) => r.owner ?? "—",
    sorter: true,
  },
  {
    title: () => t("addresses.switch_port"), key: "switch_port", width: 140, ellipsis: { tooltip: false },
    render: (r) => switchPortCell(r), sorter: true,
  },
  {
    title: () => t("addresses.note"), key: "note", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => r.note ?? "—", sorter: true,
  },
  {
    title: () => t("addresses.source"), key: "discovery_source", width: 120,
    render: (r) => labelSource(r.discovery_source), sorter: true,
  },
];

// 欄位顯示偏好 (per-user，後端 user_preferences.table_columns)
const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "addresses",
  ["live", "ip", "hostname", "mac", "state", "owner", "switch_port", "note", "discovery_source"],
  ["live", "ip", "hostname", "mac", "state", "discovery_source"],
);

// selection column 永遠顯示；其他依 visibleKeys
const columns = computed<DataTableColumns<IPAddress>>(() => {
  return allColumns.filter((c: any) => {
    if (c.type === "selection") return true;
    if (!c.key) return true;
    return visibleKeys.value.includes(String(c.key));
  });
});

// scroll-x 依「目前顯示的欄位」動態算總寬，而非固定 1228；隱藏欄位後就不會
// 再硬撐出右側空白與不必要的水平捲軸（有 minWidth 的欄會自動撐滿剩餘寬度）。
const scrollX = computed(() => {
  let w = 36;  // selection 欄
  for (const c of columns.value as any[]) {
    if (c.type === "selection") continue;
    w += (c.width ?? c.minWidth ?? 120);
  }
  return w;
});

// 給 ColumnPicker 用的全選用欄位 label
const columnPickerItems = computed(() => [
  { key: "live", label: t("cols.live_dot") },
  { key: "ip", label: "IP" },
  { key: "hostname", label: t("cols.hostname") },
  { key: "mac", label: "MAC" },
  { key: "state", label: t("cols.status") },
  { key: "owner", label: t("cols.owner") },
  { key: "switch_port", label: t("cols.switch_port") },
  { key: "note", label: t("cols.note") },
  { key: "discovery_source", label: t("cols.source") },
]);

const sortField = ref<string | null>(null);
const sortDir = ref<"asc" | "desc">("asc");
function handleSorter(s: { columnKey?: string | number; order?: "ascend" | "descend" | false } | null) {
  if (!s || !s.order) { sortField.value = null; sortDir.value = "asc"; }
  else { sortField.value = String(s.columnKey); sortDir.value = s.order === "descend" ? "desc" : "asc"; }
  page.value = 1;
  void refresh();
}

// 匯出全部：用相同篩選條件分頁抓完整資料集（非只當頁）
async function fetchAllForExport(): Promise<IPAddress[]> {
  const all: IPAddress[] = [];
  const big = 500;   // 後端 page_size 上限
  let p = 1;
  for (;;) {
    const res = await listAddresses({
      q: q.value.trim() || undefined,
      exact: exactMatch.value || undefined,
      subnetId: subnetId.value || undefined,
      sectionId: sectionId.value || undefined,
      customerId: customerId.value || undefined,
      sort: sortField.value || undefined,
      order: sortDir.value,
      page: p,
      pageSize: big,
    });
    all.push(...res.items);
    if (res.items.length === 0 || all.length >= res.total) break;
    p++;
  }
  return all;
}

async function refresh() {
  loading.value = true;
  try {
    const res = await listAddresses({
      q: q.value.trim() || undefined,
      exact: exactMatch.value || undefined,
      subnetId: subnetId.value || undefined,
      sectionId: sectionId.value || undefined,
      customerId: customerId.value || undefined,
      sort: sortField.value || undefined,
      order: sortDir.value,
      page: page.value,
      pageSize: pageSize.value,
    });
    rows.value = res.items;
    total.value = res.total;
    void loadSubnetUsage();
    // 從全域搜尋帶 open=<id> 進來 → 直接打開該筆位址明細
    if (pendingOpenId) {
      const target = rows.value.find((r) => r.id === pendingOpenId);
      pendingOpenId = null;
      if (target) openRow(target);
    }
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

let pendingOpenId: string | null =
  typeof route.query.open === "string" ? route.query.open : null;

onMounted(() => {
  void loadSubnets();
  void loadSections();
  void ensureCustomersLoaded();
  void refresh();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><AddressesIcon /></n-icon>
        <span>{{ t("nav.addresses") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px" align="center">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-select
        v-model:value="sectionId"
        :options="sectionOptions"
        :placeholder="t('addresses.filter_section_placeholder')"
        clearable filterable
        style="width: 200px"
      />
      <n-select
        v-model:value="subnetId"
        :options="subnetOptions"
        :placeholder="t('addresses.filter_subnet_placeholder')"
        clearable filterable
        style="width: 280px"
      />
      <n-select
        v-model:value="customerId"
        :options="customerOptions"
        :placeholder="t('addresses.filter_customer_placeholder')"
        clearable filterable
        style="width: 200px"
      />
      <n-input v-model:value="q" clearable
               :placeholder="t('addresses.search_placeholder')"
               style="width: 260px" />
      <n-tooltip>
        <template #trigger>
          <n-checkbox :checked="exactMatch" :disabled="!q.trim()"
                      @update:checked="(v: boolean) => { exactMatch = v; page = 1; refresh(); }">
            {{ t("addresses.exact_match") }}
          </n-checkbox>
        </template>
        {{ t("addresses.exact_match_hint") }}
      </n-tooltip>
      <ColumnPicker
        :all="columnPickerItems"
        :visible="visibleKeys"
        @update:visible="setVisible"
        @reset="reset"
      />
      <ExportButton :columns="columns" :rows="rows" :fetch-all="fetchAllForExport"
                    filename="ip-addresses" :title="t('nav.addresses')" />
    </n-space>
    <div v-if="subnetId && subnetUsage" style="margin-bottom: 12px; padding: 10px 14px; background: rgba(127,127,127,0.06); border-radius: 6px;">
      <n-space align="center" justify="space-between" :wrap-item="false" style="margin-bottom: 6px">
        <span style="font-weight: 600">{{ t("subnets.visualisation") }} · {{ usageSubnetLabel }}</span>
        <span style="font-size: 13px; opacity: .8">
          {{ t("subnets.used_summary", { used: subnetUsage.used, total: subnetUsage.total, pct: subnetUsage.used_pct }) }}
        </span>
      </n-space>
      <n-progress type="line" :percentage="subnetUsage.used_pct" :status="usageStatus"
                  :show-indicator="false" :height="10" />
    </div>
    <n-space v-if="checkedKeys.length" align="center" style="margin-bottom: 8px; padding: 8px 12px; background: rgba(127,127,127,0.08); border-radius: 6px;">
      <span>{{ t("common.selected_n", { n: checkedKeys.length }) }}</span>
      <n-popconfirm @positive-click="doBulkDelete">
        <template #trigger>
          <n-button type="error" size="small" :loading="bulkBusy">
            <template #icon><n-icon><DeleteIcon /></n-icon></template>
            {{ t("common.bulk_delete") }}
          </n-button>
        </template>
        {{ t("common.confirm_delete_n_irreversible", { n: checkedKeys.length }) }}
      </n-popconfirm>
      <n-button size="small" @click="checkedKeys = []">{{ t("common.clear_selection") }}</n-button>
    </n-space>
    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      remote
      :scroll-x="scrollX"
      :row-key="(row: IPAddress) => row.id"
      :checked-row-keys="checkedKeys"
      @update:checked-row-keys="(keys: DataTableRowKey[]) => checkedKeys = keys"
      @update:sorter="handleSorter"
      :pagination="{
        page: page,
        pageSize: pageSize,
        itemCount: total,
        showSizePicker: true,
        pageSizes: [50, 100, 200, 500],
        onUpdatePage: (p) => { page = p; void refresh(); },
        onUpdatePageSize: (ps) => { pageSize = ps; page = 1; void refresh(); },
      }"
      :bordered="false"
      :row-props="(row: IPAddress) => ({
        style: 'cursor: pointer',
        onClick: (e: MouseEvent) => {
          // 點 checkbox cell 不要開 modal
          const target = e.target as HTMLElement;
          if (target.closest('.n-checkbox')) return;
          openRow(row);
        },
      })"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>
  </n-card>
  <IPAddressEditModal
    v-model:show="modalShow"
    :address="selected"
    @saved="onSaved"
    @deleted="onDeleted"
  />
</template>
