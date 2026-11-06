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
import { AddressesIcon, RefreshIcon, DeleteIcon, DevicesIcon, CancelIcon } from "@/icons";
import { useAuthStore } from "@/stores/auth";
import { useBulkCreateDevices } from "@/composables/useBulkCreateDevices";
import IPAddressEditModal from "@/components/IPAddressEditModal.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
import IpRoleTags from "@/components/IpRoleTags.vue";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import OsIcon from "@/components/OsIcon.vue";
import { renderDeviceKind } from "@/utils/deviceKindCell";
import { useScanProbes, osDisplayLabel } from "@/api/scanProbes";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { computed } from "vue";
import { renderMacWithVendor } from "@/utils/macVendor";
import { listDhcpRanges } from "@/api/integrations";
import {
  dhcpInfoFor, lastSeenText, renderDeviceLink, renderDhcpCell, staleDaysLabel, toDhcpRanges, type DhcpRangeInfo,
} from "@/utils/ipColumns";

const { t, te, locale } = useI18n();
const { catalog } = useScanProbes();
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
// 頁碼與每頁筆數也記在網址上：點進一筆再按上一頁，回到原本那一頁（使用者 2026-10-05）
const PAGE_SIZES = [50, 100, 200, 500];
const page = ref(Math.max(1, Math.floor(Number(route.query.page) || 1)));
const pageSize = ref(PAGE_SIZES.includes(Number(route.query.ps)) ? Number(route.query.ps) : 100);
watch([page, pageSize], () => {
  // 篩選條件的 router.replace 可能還沒完成；排到下一輪再讀 route.query，才不會把剛寫進去的篩選蓋掉
  setTimeout(() => {
    const want = { page: page.value > 1 ? String(page.value) : undefined,
                   ps: pageSize.value !== 100 ? String(pageSize.value) : undefined };
    if (route.query.page === want.page && route.query.ps === want.ps) return;
    router.replace({ query: { ...route.query, ...want } }).catch(() => {});
  }, 0);
});

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
const authStore = useAuthStore();
const isAdmin = computed(() => !!authStore.me?.is_admin);
const { creatingDevices, createDevices } = useBulkCreateDevices();
async function doBulkCreateDevices() {
  const res = await createDevices(checkedKeys.value.map(String), refresh);
  if (res) checkedKeys.value = [];
}

function openRow(row: IPAddress) {
  // 點既有 IP → 進獨立詳細資料頁（不再彈 modal）
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
    // 彈出文字一律含完整 裝置@連接埠 全文；低信心時再附上說明
    default: () => r.switch_port_confident === false
      ? h("div", { style: "max-width:320px;line-height:1.5" }, [
          h("div", { style: "font-family:monospace;word-break:break-all" }, (r.switch_port ?? "").replace(" / ", "@")),
          h("div", { style: "margin-top:4px" }, t("addresses.switch_port_uncertain")),
        ])
      : h("span", { style: "font-family:monospace;word-break:break-all" }, (r.switch_port ?? "").replace(" / ", "@")),
  });
}

// 來源代碼 → 翻譯 (manual / librenms / dns / opnsense / scanner …)；無對應則原樣
function labelSource(v: string | null | undefined): string {
  if (!v) return "";
  const key = `addresses.source_${v}`;
  const out = t(key);
  return out === key ? v : out;
}

// DHCP 欄：後端判定「在 DHCP 範圍內」（照子網路歸屬算，重疊網段不會標到別的單位）才標；滑過去的伺服器與範圍從
// 發放範圍清單查。跟子網路詳細頁同一個儲存格（utils/ipColumns）
const dhcpRanges = ref<DhcpRangeInfo[]>([]);
async function loadDhcpRanges() {
  try {
    dhcpRanges.value = toDhcpRanges(await listDhcpRanges(), t("ranges.manual"));
  } catch { dhcpRanges.value = []; }
}

const allColumns: DataTableColumns<IPAddress> = [
  { type: "selection" },
  { title: "", key: "live", width: 28, render: (r) => liveDot(r) },
  {
    title: () => t("addresses.ip"), key: "ip", width: 200,
    sorter: true,
    render: (r) => h("span", { class: "ip-cell" }, [h("span", { class: "ip-cell-addr" }, String(r.ip)), h(IpRoleTags, { row: r })]),
  },
  {
    title: () => t("addresses.hostname"), key: "hostname", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => r.hostname ?? "—",
    sorter: true,
  },
  {
    title: "DHCP", key: "dhcp", width: 80,
    render: (r) => r.in_dhcp_range ? renderDhcpCell(t, dhcpInfoFor(dhcpRanges.value, r.ip)) : "",
  },
  {
    title: () => t("addresses.mac"), key: "mac", width: 150,
    render: (r) => renderMacWithVendor(r.mac, r.mac_vendor),
    sorter: true,
  },
  {
    title: () => t("cols.vendor"), key: "mac_vendor", width: 140, ellipsis: { tooltip: true },
    render: (r) => r.mac_vendor ?? "—", sorter: true,
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
    title: () => t("addresses.switch_port"), key: "switch_port", width: 210, ellipsis: { tooltip: false },
    render: (r) => switchPortCell(r), sorter: true,
  },
  {
    title: () => t("cols.device"), key: "device", width: 150, ellipsis: { tooltip: true }, sorter: true,
    render: (r) => renderDeviceLink(r, (id) => { void router.push({ name: "device-detail", params: { id } }); }, t),
  },
  {
    title: () => t("common.description"), key: "description", width: 200, ellipsis: { tooltip: true },
    render: (r) => r.description ?? "", sorter: true,
  },
  {
    title: () => t("addresses.last_seen"), key: "last_seen", width: 170, sorter: true,
    render: (r) => lastSeenText(r),
  },
  {
    title: () => t("stale.col_stale"), key: "stale_days", width: 110, sorter: true,
    render: (r) => staleDaysLabel(t, r),
  },
  {
    title: () => t("addresses.note"), key: "note", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => r.note ?? "—", sorter: true,
  },
  {
    title: () => t("addresses.source"), key: "discovery_source", width: 120,
    render: (r) => labelSource(r.discovery_source), sorter: true,
  },
  {
    title: () => t("cols.os"), key: "os", width: 150,
    render: (r) => {
      if (!r.os_family) return "—";
      const label = osDisplayLabel(catalog.value.os_families, r.os_family, r.os_guess, locale.value);
      // 一行顯示，icon 永遠不縮；空間不夠時 label 被裁掉、只剩 icon
      return h("div", {
        style: "display:flex;align-items:center;gap:4px;min-width:0;white-space:nowrap",
        title: r.os_guess ?? label ?? undefined,
      }, [
        h("span", { style: "flex:none;display:inline-flex" }, h(OsIcon, { family: r.os_family, size: 16 })),
        label ? h("span", { style: "overflow:hidden;text-overflow:ellipsis" }, label) : null,
      ]);
    },
  },
  {
    // 掃描代理定期偵測判讀出的設備類型（含 Recog 指紋庫）；滑過去看廠牌型號
    title: () => t("cols.device_kind"), key: "device_kind", width: 140, sorter: true,
    render: (r) => renderDeviceKind(r, t, te),
  },
];

// 欄位顯示偏好 (per-user，後端 user_preferences.table_columns)
const { visibleKeys, setVisible, reset, order, setOrder, orderColumns } = useColumnPrefs(
  "addresses",
  ["live", "ip", "hostname", "mac", "state", "owner", "switch_port", "note", "discovery_source", "os",
   "device_kind", "dhcp", "mac_vendor", "device", "description", "last_seen", "stale_days"],
  ["live", "ip", "hostname", "mac", "state", "discovery_source"],
);

// selection column 永遠顯示；其他依 visibleKeys，再照使用者拖拉的順序排
const columns = computed<DataTableColumns<IPAddress>>(() => {
  return orderColumns(allColumns.filter((c: any) => {
    if (c.type === "selection") return true;
    if (!c.key) return true;
    return visibleKeys.value.includes(String(c.key));
  }));
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
// 欄位跟子網路詳細頁的 IP 表一樣（使用者 2026-10-12：IP 位址頁缺了 DHCP、製造商、裝置、說明、最後出現、失聯），
// 再加上這頁才有的「來源」
const columnPickerItems = computed(() => [
  { key: "live", label: t("cols.live_dot") },
  { key: "ip", label: "IP" },
  { key: "hostname", label: t("cols.hostname") },
  { key: "dhcp", label: "DHCP" },
  { key: "mac", label: "MAC" },
  { key: "mac_vendor", label: t("cols.vendor") },
  { key: "state", label: t("cols.status") },
  { key: "os", label: t("cols.os") },
  { key: "device_kind", label: t("cols.device_kind") },
  { key: "owner", label: t("cols.owner") },
  { key: "switch_port", label: t("cols.switch_port") },
  { key: "device", label: t("cols.device") },
  { key: "description", label: t("cols.description") },
  { key: "last_seen", label: t("cols.last_seen") },
  { key: "stale_days", label: t("stale.col_stale") },
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
  void loadDhcpRanges();
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
        :order="order"
        @update:visible="setVisible"
        @update:order="setOrder"
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
      <n-popconfirm v-if="isAdmin" @positive-click="doBulkCreateDevices">
        <template #trigger>
          <n-button type="primary" size="small" :loading="creatingDevices" :disabled="creatingDevices"
                    data-testid="bulk-create-devices">
            <template #icon><n-icon><DevicesIcon /></n-icon></template>
            {{ t("addresses.bulk_devices") }}
          </n-button>
        </template>
        <div style="max-width: 360px">{{ t("addresses.bulk_devices_confirm", { n: checkedKeys.length }) }}</div>
      </n-popconfirm>
      <n-button size="small" @click="checkedKeys = []">
        <template #icon><n-icon><CancelIcon /></n-icon></template>
        {{ t("common.clear_selection") }}
      </n-button>
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
        pageSizes: PAGE_SIZES,
        prefix: ({ itemCount }) => t('common.total_rows', { n: itemCount ?? 0 }),
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
