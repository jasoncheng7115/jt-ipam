<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
import { h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  NCard,
  NDataTable,
  NSpace,
  NIcon,
  NButton,
  NPopconfirm,
  NTooltip,
  NProgress,
  NSwitch,
  NTag,
  useMessage,
  type DataTableColumns,
  type DataTableRowKey,
} from "naive-ui";
import {
  listSubnets, getSubnetUsage, bulkDeleteSubnets,
  deleteSubnet, archiveSubnet, unarchiveSubnet,
} from "@/api/subnets";
import type { Subnet, SubnetUsage } from "@/types";
import { SubnetsIcon, RefreshIcon, DeleteIcon, PlusIcon, EditIcon, ArchiveIcon, RestoreIcon, ScanAgentsIcon, PinIcon } from "@/icons";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import SubnetEditModal from "@/components/SubnetEditModal.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useCustomers } from "@/composables/useCustomers";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { usePinnedSubnets } from "@/composables/usePinnedSubnets";
import { useSubnetTree } from "@/composables/useSubnetTree";
import { computed } from "vue";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();

const { labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const { bump: bumpSubnetTree } = useSubnetTree();

const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

const router = useRouter();
const route = useRoute();
const links = useEntityLinks(router);

const { t, locale } = useI18n();
const msg = useMessage();
// 從某單位的「子網路數」點過來時，只顯示該單位的子網路（可清除）
const customerFilter = ref<string | null>(null);
const rows = ref<Subnet[]>([]);
const usageMap = ref<Record<string, SubnetUsage>>({});
const loading = ref(false);

// 編輯 modal 狀態（表單與送出邏輯移至共用元件 SubnetEditModal.vue）
const showEdit = ref(false);
const editing = ref<Subnet | null>(null);
// 從子網路詳情「新增下層子網路」帶 ?create=1&section= 過來時，預選此區段
const presetSectionId = ref<string | null>(null);

function openCreate() {
  editing.value = null;
  presetSectionId.value = null;
  showEdit.value = true;
}

function openEdit(r: Subnet) {
  editing.value = r;
  presetSectionId.value = null;
  showEdit.value = true;
}

async function del(r: Subnet) {
  try { await deleteSubnet(r.id); await refresh(); bumpSubnetTree(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("common.delete_failed")); }
}
async function archive(r: Subnet) {
  try { await archiveSubnet(r.id); msg.success(t("subnets.archived_ok")); await refresh(); bumpSubnetTree(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function unarchive(r: Subnet) {
  try { await unarchiveSubnet(r.id); msg.success(t("subnets.unarchived_ok")); await refresh(); bumpSubnetTree(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

function cidrSortNum(c: string): number {
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)/.exec(c);
  if (!m) return 0;
  return ((+m[1]) << 24 >>> 0) + ((+m[2]) << 16) + ((+m[3]) << 8) + (+m[4]);
}

const { isPinned: isSubnetPinned, toggle: toggleSubnetPin, ensureLoaded: ensurePinsLoaded } = usePinnedSubnets();
function pinToggle(id: string) { void toggleSubnetPin(id); }

const allColumns: DataTableColumns<Subnet> = [
  { type: "selection" },
  {
    // CIDR 放在最前（selection 之後）→ n-data-table 的展開箭頭會落在此欄，與階層縮排一致，
    // 不會出現在「釘選」欄
    title: () => t("subnets.cidr"), key: "cidr", minWidth: 180, ellipsis: { tooltip: true },
    render: (r) => links.subnet(r.id, r.cidr),
    sorter: (a, b) => cidrSortNum(a.cidr) - cidrSortNum(b.cidr) || a.cidr.localeCompare(b.cidr),
  },
  {
    title: () => t("cols.pinned"), key: "pinned", width: 64, align: "center",
    render: (r) => h(NTooltip, null, {
      trigger: () => h(NButton, {
        size: "small", quaternary: true, type: isSubnetPinned(r.id) ? "warning" : "default",
        onClick: (e: Event) => { e.stopPropagation(); pinToggle(r.id); },
      }, { icon: () => h(NIcon, { color: isSubnetPinned(r.id) ? "#f0a020" : undefined }, () => h(PinIcon)) }),
      default: () => t("common.pin"),
    }),
  },
  {
    title: () => t("common.description"),
    key: "description", minWidth: 220, ellipsis: { tooltip: true },
    render: (r) => r.description ?? "—",
    sorter: (a, b) => (a.description ?? "").localeCompare(b.description ?? ""),
  },
  {
    title: () => t("subnets.usage"),
    key: "usage", width: 200,
    sorter: (a, b) => (usageMap.value[a.id]?.used_pct ?? 0) - (usageMap.value[b.id]?.used_pct ?? 0),
    render: (r) => {
      const u = usageMap.value[r.id];
      if (!u) return "—";
      const status = u.used_pct >= 90 ? "error" : u.used_pct >= 75 ? "warning" : "success";
      return h(NProgress, {
        type: "line",
        percentage: u.used_pct,
        status,
        showIndicator: true,
      });
    },
  },
  {
    title: () => t("subnets.ip_total"),
    key: "ip_total", width: 110,
    sorter: (a, b) => (usageMap.value[a.id]?.total ?? 0) - (usageMap.value[b.id]?.total ?? 0),
    render: (r) => {
      const u = usageMap.value[r.id];
      return u ? u.total.toLocaleString() : "—";
    },
  },
  {
    title: () => t("subnets.gateway"),
    key: "gateway", width: 130,
    render: (r) => (r as any).gateway ?? "—",
  },
  {
    title: () => t("nav.customers"),
    key: "customer_id", width: 160,
    ellipsis: { tooltip: true },
    render: (r) => links.customer(r.customer_id, r.customer_name || customerLabelFor(r.customer_id)),
    sorter: (a, b) => (a.customer_name || customerLabelFor(a.customer_id))
      .localeCompare(b.customer_name || customerLabelFor(b.customer_id)),
  },
  {
    title: () => t("subnets.scan"), key: "scan_enabled", width: 70, align: "center",
    sorter: (a, b) => Number(a.scan_enabled) - Number(b.scan_enabled),
    render: (r) => r.scan_enabled
      ? h(NTooltip, null, {
          trigger: () => h(NIcon, { size: 18, color: "#18a058" }, () => h(ScanAgentsIcon)),
          default: () => t("subnets.scan_enabled"),
        })
      : h("span", { style: "opacity:.35" }, "—"),
  },
  {
    title: () => t("common.actions"), key: "actions", className: "col-actions", width: 136,
    render: (r) => h(NSpace, { size: 4, wrapItem: false, wrap: false }, () =>
      showArchived.value
        ? [
            // 歸檔區：還原
            h(NTooltip, null, {
              trigger: () => h(NButton, {
                size: "small", quaternary: true, type: "primary",
                onClick: (e: Event) => { e.stopPropagation(); void unarchive(r); },
              }, { icon: () => h(NIcon, null, () => h(RestoreIcon)) }),
              default: () => t("subnets.unarchive"),
            }),
            h(NPopconfirm, { onPositiveClick: () => del(r) }, {
              trigger: () => h(NTooltip, null, {
                trigger: () => h(NButton, { size: "small", quaternary: true, type: "error" },
                  { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
                default: () => t("common.delete"),
              }),
              default: () => t("common.confirm_delete"),
            }),
          ]
        : [
            h(NTooltip, null, {
              trigger: () => h(NButton, {
                size: "small", quaternary: true,
                onClick: (e: Event) => { e.stopPropagation(); openEdit(r); },
              }, { icon: () => h(NIcon, null, () => h(EditIcon)) }),
              default: () => t("common.edit"),
            }),
            h(NPopconfirm, { onPositiveClick: () => archive(r) }, {
              trigger: () => h(NTooltip, null, {
                trigger: () => h(NButton, { size: "small", quaternary: true },
                  { icon: () => h(NIcon, null, () => h(ArchiveIcon)) }),
                default: () => t("subnets.archive"),
              }),
              default: () => t("subnets.archive_confirm"),
            }),
            h(NPopconfirm, { onPositiveClick: () => del(r) }, {
              trigger: () => h(NTooltip, null, {
                trigger: () => h(NButton, { size: "small", quaternary: true, type: "error" },
                  { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
                default: () => t("common.delete"),
              }),
              default: () => t("common.confirm_delete"),
            }),
          ],
    ),
  },
];

const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "subnets",
  ["pinned", "cidr", "description", "usage", "ip_total", "gateway", "customer_id", "scan_enabled", "actions"],
  ["pinned", "cidr", "description", "usage", "ip_total", "gateway", "customer_id", "scan_enabled", "actions"],
);
const columns = computed<DataTableColumns<Subnet>>(() =>
  allColumns.filter((c: any) => c.type === "selection" || visibleKeys.value.includes(c.key)),
);
const columnPickerItems = computed(() => [
  { key: "pinned", label: t("cols.pinned") },
  { key: "cidr", label: "CIDR" },
  { key: "description", label: t("cols.description") },
  { key: "usage", label: t("cols.usage") },
  { key: "gateway", label: t("subnets.gateway") },
  { key: "customer_id", label: t("cols.unit") },
  { key: "scan_enabled", label: t("cols.scan") },
  { key: "actions", label: t("cols.actions") },
]);

async function doBulkDelete() {
  if (!checkedKeys.value.length) return;
  bulkBusy.value = true;
  try {
    const ids = checkedKeys.value.map((k) => String(k));
    const res = await bulkDeleteSubnets(ids);
    if (res.failed > 0) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    checkedKeys.value = [];
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally { bulkBusy.value = false; }
}

const treeMode = ref(true);
const showArchived = ref(false);
watch(showArchived, () => { void refresh(); });
const flatRows = ref<Subnet[]>([]);

function buildTree(items: Subnet[]): any[] {
  const byId: Record<string, any> = {};
  items.forEach((s) => { byId[s.id] = { ...s }; });
  const roots: any[] = [];
  items.forEach((s) => {
    const node = byId[s.id];
    const pid = (s as any).master_subnet_id;
    if (pid && byId[pid]) (byId[pid].children ??= []).push(node);
    else roots.push(node);
  });
  return roots;
}
function applyView() {
  if (customerFilter.value) {
    // 單位篩選時走平面清單（巢狀樹過濾會破壞父子關係）
    rows.value = flatRows.value.filter((s) => s.customer_id === customerFilter.value);
    return;
  }
  rows.value = treeMode.value ? buildTree(flatRows.value) : flatRows.value;
}

async function refresh() {
  loading.value = true;
  try {
    const res = await listSubnets({ page: 1, pageSize: 500, archived: showArchived.value });
    flatRows.value = res.items;
    applyView();
    const usages = await Promise.all(
      res.items.map(async (s) => {
        try {
          return await getSubnetUsage(s.id);
        } catch {
          return null;
        }
      }),
    );
    const map: Record<string, SubnetUsage> = {};
    usages.forEach((u) => {
      if (u) map[u.subnet_id] = u;
    });
    usageMap.value = map;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

watch(treeMode, applyView);

const customerFilterLabel = computed(() =>
  customerFilter.value ? customerLabelFor(customerFilter.value) : "");
function clearCustomerFilter() {
  customerFilter.value = null;
  void router.replace({ name: "subnets", query: {} });
  applyView();
}

onMounted(() => {
  const c = route.query.customer;
  if (typeof c === "string" && c) customerFilter.value = c;
  void refresh();
  void ensureCustomersLoaded();
  void ensurePinsLoaded();
  // 從子網路詳情「新增下層子網路」帶 ?create=1&section= 過來 → 開新增、預選區段
  if (route.query.create === "1") {
    editing.value = null;
    const sec = route.query.section;
    presetSectionId.value = (typeof sec === "string" && sec) ? sec : null;
    showEdit.value = true;
  }
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><SubnetsIcon /></n-icon>
        <span>{{ t("nav.subnets") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px" align="center">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button v-if="!showArchived" type="primary" :disabled="_authBtn.me?.can_edit === false" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="columnPickerItems" :visible="visibleKeys"
                    @update:visible="setVisible" @reset="reset" />
      <ExportButton :columns="columns" :rows="rows" filename="subnets" :title="t('nav.subnets')" />
      <n-space align="center" :size="6" style="margin-left: 4px">
        <span style="font-size: 13px; opacity: .75">{{ t("subnets.tree_view") }}</span>
        <n-switch v-model:value="treeMode" size="small" :disabled="showArchived" />
      </n-space>
      <n-button :type="showArchived ? 'warning' : 'default'" ghost
                style="margin-left: 4px" @click="showArchived = !showArchived">
        <template #icon><n-icon><ArchiveIcon /></n-icon></template>
        {{ showArchived ? t("subnets.exit_archive") : t("subnets.archive_area") }}
      </n-button>
    </n-space>
    <n-space v-if="customerFilter" align="center" style="margin-bottom: 8px">
      <n-tag type="info" closable @close="clearCustomerFilter">
        {{ t("nav.customers") }}：{{ customerFilterLabel }}
      </n-tag>
    </n-space>
    <n-space v-if="checkedKeys.length" align="center" style="margin-bottom: 8px; padding: 8px 12px; background: rgba(127,127,127,0.08); border-radius: 6px;">
      <span>{{ t("common.selected_n", { n: checkedKeys.length }) }}</span>
      <n-popconfirm @positive-click="doBulkDelete">
        <template #trigger>
          <n-button type="error" size="small" :loading="bulkBusy">
            <template #icon><n-icon><DeleteIcon /></n-icon></template>
            {{ t("common.bulk_delete") }}
          </n-button>
        </template>
        {{ t("common.confirm_delete_n", { n: checkedKeys.length }) }}
      </n-popconfirm>
      <n-button size="small" @click="checkedKeys = []">{{ t("common.clear_selection") }}</n-button>
    </n-space>
    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      :row-key="(row: Subnet) => row.id"
      :checked-row-keys="checkedKeys"
      @update:checked-row-keys="(keys: DataTableRowKey[]) => checkedKeys = keys"
      :pagination="pg"
      :bordered="false"
      :scroll-x="986"
      :row-props="(row: Subnet) => ({
        style: 'cursor: pointer',
        onClick: (e: MouseEvent) => {
          const target = e.target as HTMLElement;
          if (target.closest('.n-checkbox') || target.closest('.n-button') || target.closest('a') || target.closest('.n-data-table-expand-trigger')) return;
          router.push({ name: 'subnet-detail', params: { id: row.id } });
        },
      })"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <SubnetEditModal v-model:show="showEdit" :editing="editing"
                     :preset-section-id="presetSectionId" @saved="refresh" />
  </n-card>
</template>
