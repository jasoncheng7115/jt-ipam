<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NSelect, NInputNumber, NPopconfirm, NTag, NIcon, NTooltip, NPopover, NSpin,
  NSwitch, NDivider,
  useMessage, type DataTableColumns, type DataTableRowKey,
} from "naive-ui";
import {
  listNATs, createNAT, updateNAT, deleteNAT, bulkDeleteNATs, type NAT,
} from "@/api/phase3";
import { listAddresses, getAddress } from "@/api/addresses";
import { listSubnets } from "@/api/subnets";
import { useCustomers } from "@/composables/useCustomers";
import type { IPAddress } from "@/types";
import { listDevices } from "@/api/basic";
import { listFirewalls, type OPNsenseFirewall } from "@/api/integrations";
import { useRouter } from "vue-router";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const router = useRouter();
const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "nat",
  ["name", "type", "protocol", "src_ip_id", "src_interface", "src_port", "dst_ip_id", "dst_port", "device_id", "description", "source_label", "actions"],
  ["name", "type", "protocol", "src_ip_id", "src_interface", "src_port", "dst_ip_id", "dst_port", "device_id", "description", "source_label", "actions"],
);
const columnPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "type", label: t("cols.type") },
  { key: "protocol", label: t("cols.protocol") },
  { key: "src_ip_id", label: t("cols.src_ip") },
  { key: "dst_ip_id", label: t("cols.dst_ip") },
  { key: "src_port", label: t("cols.src_port") },
  { key: "dst_port", label: t("cols.dst_port") },
  { key: "src_interface", label: t("cols.src_iface") },
  { key: "device_id", label: t("cols.device") },
  { key: "description", label: t("cols.description") },
  { key: "source_label", label: t("cols.source") },
  { key: "actions", label: t("cols.actions") },
]);
import {
  NatIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon, EyeIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";

const msg = useMessage();
const rows = ref<NAT[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

async function doBulkDelete() {
  if (!checkedKeys.value.length) return;
  bulkBusy.value = true;
  try {
    const ids = checkedKeys.value.map(String);
    const res = await bulkDeleteNATs(ids);
    if (res.failed > 0) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    checkedKeys.value = [];
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    bulkBusy.value = false;
  }
}
const loading = ref(false);
const show = ref(false);
const editing = ref<NAT | null>(null);
const viewOnly = ref(false);   // 點列＝唯讀檢視；點編輯鈕＝可編輯
function blankForm() {
  return {
    name: "", type: "many_to_one", protocol: "any",
    src_ip_id: null as string | null,
    dst_ip_id: null as string | null,
    device_id: null as string | null,
    src_interface: "" as string,
    src_port: null as number | null,
    dst_port: null as number | null,
    description: "",
    // OPNsense 完整欄位
    disabled: false, no_rdr: false, ip_version: "inet",
    src_not: false, dst_not: false,
    src_port_to: null as number | null,
    dst_port_to: null as number | null,
    log: false,
    category: "" as string,
    nat_reflection: null as string | null,
    pool_options: null as string | null,
    filter_rule: "" as string,
  };
}
const form = ref(blankForm());

const addrOpts = ref<{ label: string; value: string }[]>([]);
const deviceOpts = ref<{ label: string; value: string }[]>([]);
const subnetCidr = ref<Record<string, string>>({});   // subnet_id → cidr，給 IP 細節彈窗顯示子網路
const { labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const firewalls = ref<OPNsenseFirewall[]>([]);

const sourceKindFilter = ref<string[]>([]);
const sourceFwFilter = ref<string | null>(null);

const sourceKindOpts = [
  { label: "OPNsense", value: "opnsense" },
  { label: "phpIPAM",  value: "phpipam" },
  { label: t("cols.manual"),     value: "manual" },
];
const firewallOpts = computed(() =>
  firewalls.value.map((f) => ({ label: f.name, value: f.id })),
);

const typeOpts = [
  { label: t("nat.type_one_to_one"),  value: "one_to_one" },
  { label: t("nat.type_many_to_one"), value: "many_to_one" },
  { label: t("nat.type_port_forward"), value: "port_forward" },
];
const protoOpts = ["tcp", "udp", "tcp/udp", "icmp", "esp", "gre", "any"].map((v) => ({ label: v, value: v }));
const ipVersionOpts = [{ label: "IPv4", value: "inet" }, { label: "IPv6", value: "inet6" }];
const natReflectionOpts = [
  { label: t("nat.reflect_default"), value: "default" },
  { label: t("common.enable"), value: "enable" },
  { label: t("common.disabled"), value: "disable" },
];

const filterDeviceId = ref<string | null>(null);

// 用 link 元件渲染一個可點選的 cell
// 滑過關聯到 jt-ipam 的 IP 時，即時彈出該 IP 的細節（懶載入 + 快取）
const ipDetailCache = ref<Record<string, IPAddress | "loading" | "error">>({});
async function loadIpDetail(ipId: string) {
  const cur = ipDetailCache.value[ipId];
  if (cur && cur !== "error") return;  // 已載入或載入中
  ipDetailCache.value = { ...ipDetailCache.value, [ipId]: "loading" };
  try {
    const a = await getAddress(ipId);
    ipDetailCache.value = { ...ipDetailCache.value, [ipId]: a };
  } catch {
    ipDetailCache.value = { ...ipDetailCache.value, [ipId]: "error" };
  }
}
function ipDetailRow(label: string, value: unknown) {
  if (value == null || value === "") return null;
  return h("div", { style: "display:flex; gap:8px; font-size:12.5px; line-height:1.7" }, [
    h("span", { style: "opacity:.55; min-width:64px" }, label),
    h("span", { style: "word-break:break-all" }, String(value)),
  ]);
}
function ipDetailCard(ipId: string) {
  const d = ipDetailCache.value[ipId];
  if (!d || d === "loading") return h(NSpin, { size: "small" });
  if (d === "error") return h("span", { style: "font-size:12.5px;opacity:.6" }, t("errors.network"));
  const stateLabel = d.state === "used" ? t("addresses.state_used")
    : d.state === "reserved" ? t("addresses.state_reserved") : (d.state ?? "—");
  return h("div", { style: "min-width:200px; max-width:320px" }, [
    h("div", { style: "font-weight:600; margin-bottom:4px; font-family:monospace" }, d.ip),
    ipDetailRow(t("addresses.hostname"), d.hostname),
    ipDetailRow(t("common.status"), stateLabel),
    ipDetailRow(t("addresses.mac"), d.mac),
    ipDetailRow(t("cols.vendor"), d.mac_vendor),
    ipDetailRow(t("subnets.cidr"), d.subnet_id ? subnetCidr.value[d.subnet_id] : null),
    ipDetailRow(t("nav.customers"), d.customer_id ? customerLabelFor(d.customer_id) : null),
    ipDetailRow(t("cols.device"), d.device_name),
    ipDetailRow(t("addresses.owner"), d.owner),
    ipDetailRow(t("addresses.switch_port"), d.switch_port),
    ipDetailRow(t("common.description"), d.description),
  ].filter(Boolean));
}
function ipLinkCell(ipId: string | null) {
  if (!ipId) return "—";
  const label = addrOpts.value.find((o) => o.value === ipId)?.label ?? ipId.slice(0, 8) + "…";
  const ipText = label.split(" — ")[0];
  const link = h("a", {
    href: "#",
    style: "color: var(--primary-color, #18a058); text-decoration: none;",
    onClick: (e: MouseEvent) => {
      e.preventDefault();
      void router.push({ name: "address-detail", params: { id: ipId } });
    },
  }, label);
  // hover 即時彈出該 IP 細節
  return h(NPopover, {
    trigger: "hover", delay: 150, placement: "top",
    onUpdateShow: (s: boolean) => { if (s) void loadIpDetail(ipId); },
  }, { trigger: () => link, default: () => ipDetailCard(ipId) });
}

// alias 參考 → 可點的 tag，導到防火牆頁的「Aliases」分頁並以該名稱篩選（看 alias 成員內容）
// 帶上該規則所屬防火牆，讓對方頁面自動選到正確的防火牆
function aliasCell(name: string | null, fwId?: string | null) {
  if (!name) return null;
  return h(NTag, {
    size: "small", type: "info", bordered: false,
    style: "cursor: pointer; max-width: 100%; vertical-align: middle",
    title: `@${name} — ${t("nat.alias_goto")}`,
    onClick: () => router.push({
      name: "firewall",
      query: { tab: "aliases", q: name, ...(fwId ? { fw: fwId } : {}) },
    }),
  }, { default: () => h("span", {
    // 給定 px max-width 才會真的截斷（%/auto 對 inline 內容無效）；完整名稱看 hover title
    style: "display:inline-block; max-width:92px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:bottom",
  }, `@${name}`) });
}

function deviceLinkCell(devId: string | null) {
  if (!devId) return "—";
  const label = deviceOpts.value.find((o) => o.value === devId)?.label ?? devId.slice(0, 8) + "…";
  return h("a", {
    href: "#",
    style: "color: var(--primary-color, #18a058); text-decoration: none;",
    onClick: (e: MouseEvent) => {
      e.preventDefault();
      void router.push({ name: "device-detail", params: { id: devId } });
    },
  }, label);
}

async function loadOpts() {
  try {
    void ensureCustomersLoaded();
    const [addr, dev, fws, subs] = await Promise.all([
      listAddresses({ pageSize: 500 }),
      listDevices(),
      listFirewalls().catch(() => ({ items: [] as OPNsenseFirewall[] })),
      listSubnets({ page: 1, pageSize: 500 }).catch(() => ({ items: [] as any[] })),
    ]);
    subnetCidr.value = Object.fromEntries((subs.items ?? []).map((s: any) => [s.id, s.cidr]));
    addrOpts.value = addr.items.map((a: any) => ({
      label: `${a.ip}${a.hostname ? " — " + a.hostname : ""}`,
      value: a.id,
    }));
    deviceOpts.value = dev.items.map((d: any) => ({
      label: `${d.name}${d.type ? " (" + d.type + ")" : ""}`,
      value: d.id,
    }));
    firewalls.value = (fws.items ?? []) as OPNsenseFirewall[];
  } catch { /* 先靜默；refresh 會報網路問題 */ }
}

async function refresh() {
  loading.value = true;
  try {
    rows.value = (await listNATs({
      deviceId: filterDeviceId.value || undefined,
      sourceKind: sourceKindFilter.value.length ? sourceKindFilter.value : undefined,
      sourceFirewallId: (sourceKindFilter.value.length === 1 && sourceKindFilter.value[0] === "opnsense")
        ? (sourceFwFilter.value || undefined)
        : undefined,
    })).items;
  }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

import { watch } from "vue";
watch([filterDeviceId, sourceKindFilter, sourceFwFilter], () => { void refresh(); });
function openCreate() {
  viewOnly.value = false;
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}
function openEdit(r: NAT, view = false) {
  viewOnly.value = view;
  editing.value = r;
  form.value = {
    name: r.name, type: r.type, protocol: r.protocol,
    src_ip_id: r.src_ip_id, dst_ip_id: r.dst_ip_id, device_id: r.device_id,
    src_interface: r.src_interface ?? "",
    src_port: r.src_port, dst_port: r.dst_port,
    description: r.description ?? "",
    disabled: r.disabled, no_rdr: r.no_rdr, ip_version: r.ip_version || "inet",
    src_not: r.src_not, dst_not: r.dst_not,
    src_port_to: r.src_port_to, dst_port_to: r.dst_port_to,
    log: r.log, category: r.category ?? "",
    nat_reflection: r.nat_reflection, pool_options: r.pool_options,
    filter_rule: r.filter_rule ?? "",
  };
  show.value = true;
}
async function submit() {
  try {
    const payload = {
      name: form.value.name,
      type: form.value.type,
      protocol: form.value.protocol,
      src_ip_id: form.value.src_ip_id ?? null,
      dst_ip_id: form.value.dst_ip_id ?? null,
      device_id: form.value.device_id ?? null,
      src_interface: form.value.src_interface.trim() || null,
      src_port: form.value.src_port ?? null,
      dst_port: form.value.dst_port ?? null,
      description: form.value.description || null,
      disabled: form.value.disabled,
      no_rdr: form.value.no_rdr,
      ip_version: form.value.ip_version,
      src_not: form.value.src_not,
      dst_not: form.value.dst_not,
      src_port_to: form.value.src_port_to ?? null,
      dst_port_to: form.value.dst_port_to ?? null,
      log: form.value.log,
      category: form.value.category.trim() || null,
      nat_reflection: form.value.nat_reflection ?? null,
      pool_options: form.value.pool_options ?? null,
      filter_rule: form.value.filter_rule.trim() || null,
    } as any;
    if (editing.value) await updateNAT(editing.value.id, payload);
    else await createNAT(payload);
    show.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: NAT) {
  try { await deleteNAT(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allCols = computed<DataTableColumns<NAT>>(() => autoSort([
  { type: "selection" },
  { title: t("common.name"), key: "name", minWidth: 140, ellipsis: { tooltip: true } },
  {
    title: t("nat.type"), key: "type", width: 100,
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.type),
  },
  { title: t("nat.protocol"), key: "protocol", width: 100 },
  {
    title: t("nat.src_ip"), key: "src_ip_id", width: 110,
    render: (r) => r.src_ip_id ? ipLinkCell(r.src_ip_id) : (aliasCell(r.src_alias, r.source_firewall_id) ?? "—"),
  },
  { title: t("nat.src_interface"), key: "src_interface", width: 130, render: (r) => r.src_interface ?? "—" },
  { title: t("nat.src_port"), key: "src_port", width: 110,
    render: (r) => r.src_port != null ? String(r.src_port) : (aliasCell(r.src_port_alias, r.source_firewall_id) ?? "—") },
  {
    title: t("nat.dst_ip"), key: "dst_ip_id", width: 150,
    render: (r) => r.dst_ip_id ? ipLinkCell(r.dst_ip_id) : (aliasCell(r.dst_alias, r.source_firewall_id) ?? aliasCell(r.redirect_alias, r.source_firewall_id) ?? "—"),
  },
  { title: t("nat.dst_port"), key: "dst_port", width: 110,
    render: (r) => r.dst_port != null ? String(r.dst_port) : (aliasCell(r.dst_port_alias, r.source_firewall_id) ?? "—") },
  {
    title: t("nav.devices"), key: "device_id", width: 150, ellipsis: { tooltip: true },
    render: (r) => deviceLinkCell(r.device_id),
  },
  { title: t("common.description"), key: "description", minWidth: 200, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  {
    title: t("cols.source"), key: "source_label", width: 200,
    render: (r) => {
      if (!r.source_label) return "—";
      const type = r.source_kind === "opnsense" ? "info"
                 : r.source_kind === "phpipam"  ? "warning"
                 : "default";
      return h(NTag, { size: "small", type, bordered: false }, () => r.source_label);
    },
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));

const cols = computed<DataTableColumns<NAT>>(() =>
  allCols.value.filter((c: any) => c.type === "selection" || visibleKeys.value.includes(c.key)),
);

onMounted(() => { void refresh(); void loadOpts(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><NatIcon /></n-icon>
        <span>{{ t("nav.nat") }}</span>
      </n-space>
    </template>

    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" :disabled="_authBtn.me?.can_edit === false" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="columnPickerItems" :visible="visibleKeys"
                    @update:visible="setVisible" @reset="reset" />
      <ExportButton :columns="cols" :rows="rows" filename="nat" :title="t('nav.nat')" />
      <n-select
        v-model:value="filterDeviceId"
        :options="deviceOpts"
        :placeholder="t('nat.filter_device')"
        clearable filterable
        style="width: 220px"
      />
      <n-select
        v-model:value="sourceKindFilter"
        :options="sourceKindOpts"
        multiple
        :placeholder="t('nat.filter_source')"
        clearable
        style="min-width: 200px; max-width: 360px"
      />
      <n-select
        v-if="sourceKindFilter.length === 1 && sourceKindFilter[0] === 'opnsense'"
        v-model:value="sourceFwFilter"
        :options="firewallOpts"
        :placeholder="t('nat.filter_host')"
        clearable filterable
        style="width: 220px"
      />
    </n-space>

    <n-space v-if="checkedKeys.length" align="center"
             style="margin-bottom: 8px; padding: 8px 12px; background: rgba(127,127,127,0.08); border-radius: 6px;">
      <span>{{ t("common.selected_n", { n: checkedKeys.length }) }}</span>
      <n-popconfirm @positive-click="doBulkDelete">
        <template #trigger>
          <n-button type="error" size="small" :loading="bulkBusy">{{ t("common.bulk_delete") }}</n-button>
        </template>
        {{ t("nat.confirm_delete_n", { n: checkedKeys.length }) }}
      </n-popconfirm>
      <n-button size="small" @click="checkedKeys = []">{{ t("common.clear_selection") }}</n-button>
    </n-space>

    <n-data-table
      :columns="cols"
      :data="filteredRows"
      :loading="loading"
      :bordered="false"
      :scroll-x="1656"
      :pagination="pg"
      :row-key="(r: NAT) => r.id"
      :checked-row-keys="checkedKeys"
      @update:checked-row-keys="(keys: DataTableRowKey[]) => checkedKeys = keys"
      :row-props="(row: NAT) => ({
        style: 'cursor: pointer',
        onClick: (e: MouseEvent) => {
          const target = e.target as HTMLElement;
          if (target.closest('a') || target.closest('.n-button') || target.closest('.n-checkbox') || target.closest('.n-tag')) return;
          openEdit(row, true);
        },
      })"
    />

    <n-modal v-model:show="show" preset="card" style="width: 540px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20">
            <component :is="viewOnly ? EyeIcon : editing ? EditIcon : PlusIcon" />
          </n-icon>
          <span>{{ viewOnly ? t("common.view") : editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form :style="viewOnly ? 'pointer-events:none; opacity:.92' : ''">
        <n-form-item :label="t('common.name')"><n-input :disabled="viewOnly" v-model:value="form.name" /></n-form-item>
        <n-form-item :label="t('nat.type')">
          <n-select :disabled="viewOnly" v-model:value="form.type" :options="typeOpts" />
        </n-form-item>
        <n-form-item :label="t('nat.protocol')">
          <n-select :disabled="viewOnly" v-model:value="form.protocol" :options="protoOpts" />
        </n-form-item>
        <n-form-item :label="t('nat.ip_version')">
          <n-select :disabled="viewOnly" v-model:value="form.ip_version" :options="ipVersionOpts" />
        </n-form-item>
        <n-space :size="20" style="margin-bottom: 6px">
          <n-form-item :label="t('nat.disabled')" :show-feedback="false">
            <n-switch :disabled="viewOnly" v-model:value="form.disabled" />
          </n-form-item>
          <n-form-item :label="t('nat.no_rdr')" :show-feedback="false">
            <n-switch :disabled="viewOnly" v-model:value="form.no_rdr" />
          </n-form-item>
          <n-form-item :label="t('nat.log')" :show-feedback="false">
            <n-switch :disabled="viewOnly" v-model:value="form.log" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('nat.device')">
          <n-select :disabled="viewOnly" v-model:value="form.device_id" :options="deviceOpts" filterable clearable
                    :placeholder="t('nat.device_placeholder')" />
        </n-form-item>
        <!-- 來源相關欄位放一起 -->
        <n-form-item :label="t('nat.src_ip')">
          <n-select :disabled="viewOnly" v-model:value="form.src_ip_id" :options="addrOpts" filterable clearable
                    :placeholder="t('nat.src_ip_placeholder')" />
        </n-form-item>
        <n-form-item :label="t('nat.src_interface')">
          <n-input :disabled="viewOnly" v-model:value="form.src_interface" placeholder="wan / lan / opt2 …" />
        </n-form-item>
        <n-space :size="12" align="end">
          <n-form-item :label="t('nat.src_port')" :show-feedback="false">
            <n-input-number :disabled="viewOnly" v-model:value="form.src_port" :min="1" :max="65535" clearable style="width: 120px" />
          </n-form-item>
          <n-form-item :label="t('nat.port_to')" :show-feedback="false">
            <n-input-number :disabled="viewOnly" v-model:value="form.src_port_to" :min="1" :max="65535" clearable style="width: 120px" />
          </n-form-item>
          <n-form-item :label="t('nat.invert')" :show-feedback="false">
            <n-switch :disabled="viewOnly" v-model:value="form.src_not" />
          </n-form-item>
        </n-space>
        <!-- 目的相關欄位放一起 -->
        <n-form-item :label="t('nat.dst_ip')">
          <n-select :disabled="viewOnly" v-model:value="form.dst_ip_id" :options="addrOpts" filterable clearable
                    :placeholder="t('nat.dst_ip_placeholder')" />
        </n-form-item>
        <n-space :size="12" align="end">
          <n-form-item :label="t('nat.dst_port')" :show-feedback="false">
            <n-input-number :disabled="viewOnly" v-model:value="form.dst_port" :min="1" :max="65535" clearable style="width: 120px" />
          </n-form-item>
          <n-form-item :label="t('nat.port_to')" :show-feedback="false">
            <n-input-number :disabled="viewOnly" v-model:value="form.dst_port_to" :min="1" :max="65535" clearable style="width: 120px" />
          </n-form-item>
          <n-form-item :label="t('nat.invert')" :show-feedback="false">
            <n-switch :disabled="viewOnly" v-model:value="form.dst_not" />
          </n-form-item>
        </n-space>
        <n-divider style="margin: 8px 0" />
        <n-space :size="12">
          <n-form-item :label="t('nat.nat_reflection')" :show-feedback="false">
            <n-select :disabled="viewOnly" v-model:value="form.nat_reflection" :options="natReflectionOpts" style="width: 140px" />
          </n-form-item>
          <n-form-item :label="t('nat.pool_options')" :show-feedback="false">
            <n-input :disabled="viewOnly" v-model:value="form.pool_options" placeholder="default" style="width: 160px" />
          </n-form-item>
          <n-form-item :label="t('nat.category')" :show-feedback="false">
            <n-input :disabled="viewOnly" v-model:value="form.category" style="width: 160px" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('nat.filter_rule')">
          <n-input :disabled="viewOnly" v-model:value="form.filter_rule" :placeholder="t('nat.filter_rule_ph')" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input :disabled="viewOnly" v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ viewOnly ? t("common.close") : t("common.cancel") }}
        </n-button>
        <n-button v-if="viewOnly && _authBtn.me?.can_edit !== false" @click="openEdit(editing!, false)">
          <template #icon><n-icon><EditIcon /></n-icon></template>
          {{ t("common.edit") }}
        </n-button>
        <n-button v-if="!viewOnly" type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
