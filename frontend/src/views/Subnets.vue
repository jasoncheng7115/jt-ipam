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
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NInputNumber,
  NCheckbox,
  NSwitch,
  NTag,
  useMessage,
  type DataTableColumns,
  type DataTableRowKey,
} from "naive-ui";
import {
  listSubnets, getSubnetUsage, bulkDeleteSubnets,
  createSubnet, updateSubnet, deleteSubnet, archiveSubnet, unarchiveSubnet, type SubnetUpdate,
} from "@/api/subnets";
import { listSections } from "@/api/sections";
import { listScanAgents } from "@/api/phase3";
import { listVLANs, listVRFs, listLocations, type VLAN, type VRF } from "@/api/basic";
import type { Subnet, SubnetUsage, Section } from "@/types";
import { SubnetsIcon, RefreshIcon, DeleteIcon, PlusIcon, EditIcon, SaveIcon, CancelIcon, ArchiveIcon, RestoreIcon, ScanAgentsIcon, PinIcon } from "@/icons";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useCustomers } from "@/composables/useCustomers";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { usePinnedSubnets } from "@/composables/usePinnedSubnets";
import { computed } from "vue";

const { labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();

const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

const router = useRouter();
const route = useRoute();
const links = useEntityLinks(router);

const { t } = useI18n();
const msg = useMessage();
// 從某單位的「子網路數」點過來時，只顯示該單位的子網路（可清除）
const customerFilter = ref<string | null>(null);
const rows = ref<Subnet[]>([]);
const usageMap = ref<Record<string, SubnetUsage>>({});
const loading = ref(false);

const { options: customerOptions, ensureLoaded: ensureCustomerOptsLoaded } = useCustomers();

// 編輯 modal 狀態
const sections = ref<Section[]>([]);
const vlans = ref<VLAN[]>([]);
const vrfs = ref<VRF[]>([]);
const sectionOpts = computed(() => sections.value.map((s) => ({ label: s.name, value: s.id })));
const vlanOpts = computed(() => vlans.value.map((v) => ({ label: `${v.number} · ${v.name}`, value: v.id })));
const vrfOpts = computed(() => vrfs.value.map((v) => ({ label: v.rd ? `${v.name} (${v.rd})` : v.name, value: v.id })));
// 上層子網路選項：所有子網路，排除自己
const masterOptions = computed(() => flatRows.value
  .filter((s) => s.id !== editing.value?.id)
  .map((s) => ({ label: s.description ? `${s.cidr} (${s.description})` : s.cidr, value: s.id })));

const showEdit = ref(false);
const editing = ref<Subnet | null>(null);
const form = ref({
  section_id: "" as string,
  cidr: "",
  description: "",
  vlan_id: null as string | null,
  vrf_id: null as string | null,
  master_subnet_id: null as string | null,
  customer_id: null as string | null,
  is_pool: false,
  is_full: false,
  scan_enabled: false,
  scan_method: ["icmp"] as string[],
  threshold_pct: null as number | null,
  scan_agent_id: null as string | null,
  gateway: "" as string,
  dns_servers: "" as string,
  location_id: null as string | null,
  allow_overlap: false,
});

// 目前掃描代理只實作 ICMP ping + 從 ARP 表補 MAC（snmp/nmap/mdns/netbios 尚未實作）
const scanMethodOpts = [
  { label: "ICMP (ping)", value: "icmp" },
  { label: t("cols.arp_fill"), value: "arp" },
];
const scanAgentOpts = ref<{ label: string; value: string }[]>([]);
const locationOpts = ref<{ label: string; value: string }[]>([]);

async function loadAuxOpts() {
  try {
    const [secs, vls, vfs] = await Promise.all([
      listSections(1, 500), listVLANs(), listVRFs(),
    ]);
    sections.value = secs.items;
    vlans.value = vls.items;
    vrfs.value = vfs.items;
  } catch { /* silent */ }
  try {
    const ag = await listScanAgents();
    scanAgentOpts.value = ag.items.map((a) => ({ label: a.name, value: a.id }));
  } catch { /* silent */ }
  try {
    const loc = await listLocations();
    locationOpts.value = loc.items.map((l) => ({ label: l.name, value: l.id }));
  } catch { /* silent */ }
  void ensureCustomerOptsLoaded();
}

function openCreate() {
  editing.value = null;
  form.value = {
    section_id: sections.value[0]?.id ?? "",
    cidr: "",
    description: "",
    vlan_id: null, vrf_id: null, master_subnet_id: null, customer_id: null,
    is_pool: false, is_full: false,
    scan_enabled: false, scan_method: ["icmp"],
    threshold_pct: null,
    scan_agent_id: null,
    gateway: "", dns_servers: "", location_id: null,
    allow_overlap: false,
  };
  showEdit.value = true;
}

function openEdit(r: Subnet) {
  editing.value = r;
  form.value = {
    section_id: r.section_id,
    cidr: r.cidr,
    description: r.description ?? "",
    vlan_id: r.vlan_id,
    vrf_id: r.vrf_id,
    master_subnet_id: (r as any).master_subnet_id ?? null,
    customer_id: r.customer_id ?? null,
    is_pool: r.is_pool, is_full: r.is_full,
    scan_enabled: r.scan_enabled,
    scan_method: [...(r.scan_method ?? ["icmp"])],
    threshold_pct: r.threshold_pct,
    scan_agent_id: r.scan_agent_id ?? null,
    gateway: r.gateway ?? "",
    dns_servers: r.dns_servers ?? "",
    location_id: r.location_id ?? null,
    allow_overlap: false,
  };
  showEdit.value = true;
}

async function submit() {
  if (!form.value.section_id) { msg.error(t("subnets.err_section_required")); return; }
  if (!editing.value && !form.value.cidr.trim()) { msg.error(t("subnets.err_cidr_required")); return; }
  try {
    if (editing.value) {
      // CIDR 不允許改；其餘 patch
      const patch: SubnetUpdate = {
        section_id: form.value.section_id,
        description: form.value.description.trim() || null,
        vlan_id: form.value.vlan_id ?? null,
        vrf_id: form.value.vrf_id ?? null,
        master_subnet_id: form.value.master_subnet_id ?? null,
        customer_id: form.value.customer_id ?? null,
        is_pool: form.value.is_pool,
        is_full: form.value.is_full,
        scan_enabled: form.value.scan_enabled,
        scan_method: form.value.scan_method,
        threshold_pct: form.value.threshold_pct ?? null,
        scan_agent_id: form.value.scan_agent_id ?? null,
        gateway: form.value.gateway.trim() || null,
        dns_servers: form.value.dns_servers.trim() || null,
        location_id: form.value.location_id ?? null,
      };
      await updateSubnet(editing.value.id, patch);
    } else {
      await createSubnet({
        section_id: form.value.section_id,
        cidr: form.value.cidr.trim(),
        description: form.value.description.trim() || null,
        vlan_id: form.value.vlan_id ?? null,
        vrf_id: form.value.vrf_id ?? null,
        customer_id: form.value.customer_id ?? null,
        is_pool: form.value.is_pool, is_full: form.value.is_full,
        scan_enabled: form.value.scan_enabled,
        scan_method: form.value.scan_method,
        threshold_pct: form.value.threshold_pct ?? null,
        scan_agent_id: form.value.scan_agent_id ?? null,
        gateway: form.value.gateway.trim() || null,
        dns_servers: form.value.dns_servers.trim() || null,
        location_id: form.value.location_id ?? null,
        allow_overlap: form.value.allow_overlap,
      });
    }
    showEdit.value = false;
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("common.save_failed"));
  }
}

async function del(r: Subnet) {
  try { await deleteSubnet(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("common.delete_failed")); }
}
async function archive(r: Subnet) {
  try { await archiveSubnet(r.id); msg.success(t("subnets.archived_ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function unarchive(r: Subnet) {
  try { await unarchiveSubnet(r.id); msg.success(t("subnets.unarchived_ok")); await refresh(); }
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
    title: () => t("subnets.cidr"), key: "cidr", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => links.subnet(r.id, r.cidr),
    sorter: (a, b) => cidrSortNum(a.cidr) - cidrSortNum(b.cidr) || a.cidr.localeCompare(b.cidr),
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
  void loadAuxOpts();
  void ensurePinsLoaded();
  // 從子網路詳情「新增下層子網路」帶 ?create=1&section= 過來 → 開新增、預選區段
  if (route.query.create === "1") {
    void (async () => {
      await loadAuxOpts();
      openCreate();
      const sec = route.query.section;
      if (typeof sec === "string" && sec) form.value.section_id = sec;
    })();
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
      :pagination="{ pageSize: 50 }"
      :bordered="false"
      :scroll-x="986"
      :row-props="(row: Subnet) => ({
        style: 'cursor: pointer',
        onClick: (e: MouseEvent) => {
          const target = e.target as HTMLElement;
          if (target.closest('.n-checkbox') || target.closest('.n-button') || target.closest('a')) return;
          router.push({ name: 'subnet-detail', params: { id: row.id } });
        },
      })"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="showEdit" preset="card" style="width: 640px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20">
            <component :is="editing ? EditIcon : PlusIcon" />
          </n-icon>
          <span>{{ (editing ? t("common.edit") : t("common.create")) + " " + t("subnets.title") }}</span>
        </n-space>
      </template>
      <n-form label-placement="left" label-width="120">
        <n-form-item label="CIDR" required>
          <n-input v-model:value="form.cidr" placeholder="192.168.1.0/24"
                   :disabled="!!editing" />
        </n-form-item>
        <n-form-item v-if="!editing" :label="t('subnets.allow_overlap')">
          <n-checkbox v-model:checked="form.allow_overlap">{{ t("subnets.allow_overlap_hint") }}</n-checkbox>
        </n-form-item>
        <n-form-item :label="t('subnets.section')" required>
          <n-select v-model:value="form.section_id" :options="sectionOpts" filterable />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item label="VLAN">
          <n-select v-model:value="form.vlan_id" :options="vlanOpts" clearable filterable />
        </n-form-item>
        <n-form-item label="VRF">
          <n-select v-model:value="form.vrf_id" :options="vrfOpts" clearable filterable />
        </n-form-item>
        <n-form-item v-if="editing" :label="t('subnets.master')">
          <n-select v-model:value="form.master_subnet_id" :options="masterOptions"
                    :placeholder="t('common.not_specified')" clearable filterable />
        </n-form-item>
        <n-form-item :label="t('cols.unit')">
          <n-select v-model:value="form.customer_id" :options="customerOptions"
                    :placeholder="t('common.not_specified')" clearable filterable />
        </n-form-item>
        <n-form-item :label="t('subnets.gateway')">
          <n-input v-model:value="form.gateway" :placeholder="t('subnets.gateway_ph')" />
        </n-form-item>
        <n-form-item :label="t('subnets.dns_servers')">
          <n-input v-model:value="form.dns_servers" :placeholder="t('subnets.dns_servers_ph')" />
        </n-form-item>
        <n-form-item :label="t('subnets.location')">
          <n-select v-model:value="form.location_id" :options="locationOpts"
                    :placeholder="t('common.not_specified')" clearable filterable />
        </n-form-item>
        <n-form-item :label="t('subnets.pool_full')">
          <n-space>
            <n-checkbox v-model:checked="form.is_pool">{{ t("subnets.is_pool") }}</n-checkbox>
            <n-checkbox v-model:checked="form.is_full">{{ t("subnets.is_full") }}</n-checkbox>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('subnets.scan')">
          <n-space vertical style="width: 100%">
            <n-checkbox v-model:checked="form.scan_enabled">{{ t("subnets.scan_enable") }}</n-checkbox>
            <n-select v-if="form.scan_enabled"
                      v-model:value="form.scan_method" :options="scanMethodOpts"
                      multiple :placeholder="t('subnets.scan_methods')" />
            <n-select v-if="form.scan_enabled"
                      v-model:value="form.scan_agent_id" :options="scanAgentOpts"
                      clearable
                      :placeholder="t('subnets.scan_agent_ph')" />
          </n-space>
        </n-form-item>
        <n-form-item :label="t('subnets.threshold_pct')">
          <n-input-number v-model:value="form.threshold_pct" :min="0" :max="100" clearable
                          :placeholder="t('subnets.threshold_ph')" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showEdit = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
