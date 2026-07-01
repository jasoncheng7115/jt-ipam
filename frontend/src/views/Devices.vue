<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
import { computed, h, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { apiClient } from "@/api/client";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NInputNumber, NInputGroup, NSelect, NPopconfirm, NTag, NTooltip, NSpin,
  useMessage, type DataTableColumns, type DataTableRowKey,
} from "naive-ui";
import {
  listDevices, createDevice, updateDevice, deleteDevice, bulkDeleteDevices, type Device,
  listLocations, listRacks, type Location, type Rack,
} from "@/api/basic";
import { getRackDiagram, type RackDiagram } from "@/api/racks";
import {
  DevicesIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon, EyeIcon, LinkIcon, RacksIcon,
} from "@/icons";
import { cmpNatural } from "@/utils/sort";
import { listAddresses } from "@/api/addresses";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useCustomers } from "@/composables/useCustomers";
import { useEntityLinks } from "@/composables/useEntityLinks";

const { options: customerOptions, labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();

const router = useRouter();
const links = useEntityLinks(router);
const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

async function doBulkDelete() {
  if (!checkedKeys.value.length) return;
  bulkBusy.value = true;
  try {
    const ids = checkedKeys.value.map((k) => String(k));
    const res = await bulkDeleteDevices(ids);
    if (res.failed > 0) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    checkedKeys.value = [];
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally { bulkBusy.value = false; }
}

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Device[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const locations = ref<Location[]>([]);
const racks = ref<Rack[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<Device | null>(null);

const form = ref<{
  name: string; fqdn: string; type: string;
  vendor: string; model: string; serial: string;
  description: string;
  location_id: string | null;
  rack_id: string | null;
  u_position: number | null;
  u_size: number | null;
  rack_face: "front" | "rear" | null;
  rack_side: "full" | "left" | "right";
  customer_id: string | null;
  primary_ip_id: string | null;
}>({
  name: "", fqdn: "", type: "server",
  vendor: "", model: "", serial: "",
  description: "",
  location_id: null, rack_id: null,
  u_position: null, u_size: null, rack_face: null, rack_side: "full",
  customer_id: null,
  primary_ip_id: null,
});

const typeOpts = ["server", "switch", "router", "firewall", "ap", "storage", "ipmi", "other"]
  .map((v) => ({ label: v, value: v }));
const rackFaceOpts = computed(() => [
  { label: t("devices.rack_face_front"), value: "front" },
  { label: t("devices.rack_face_rear"), value: "rear" },
]);
const rackSideOpts = computed(() => [
  { label: t("devices.rack_side_full"), value: "full" },
  { label: t("devices.rack_side_left"), value: "left" },
  { label: t("devices.rack_side_right"), value: "right" },
]);

// 主要 IP 選擇：載入位址清單供 device 綁定（設了會雙向連結，IP 清單/拓樸接得起來）
const ipAddrs = ref<{ id: string; ip: string; hostname: string | null }[]>([]);
async function loadAddresses() {
  if (ipAddrs.value.length) return;
  try {
    const r = await listAddresses({ pageSize: 500 });
    ipAddrs.value = r.items.map((a: any) => ({ id: a.id, ip: a.ip, hostname: a.hostname }));
  } catch { /* silent */ }
}
const ipOptions = computed(() =>
  ipAddrs.value.map((a) => ({
    label: a.hostname ? `${a.ip} — ${a.hostname}` : a.ip,
    value: a.id,
  })));

const locationOpts = computed(() => locations.value.map((l) => ({ label: l.name, value: l.id })));

// rack 依 location 過濾 (選了 location 才顯示該 location 下的 rack)
const filteredRackOpts = computed(() => {
  const all = racks.value.map((r) => ({
    label: r.location_id
      ? `${locations.value.find((l) => l.id === r.location_id)?.name ?? "?"} / ${r.name}`
      : r.name,
    value: r.id,
    location_id: r.location_id,
  }));
  if (!form.value.location_id) return all;
  return all.filter((r) => r.location_id === form.value.location_id);
});

async function refresh() {
  loading.value = true;
  try {
    const [d, l, rk] = await Promise.all([listDevices(), listLocations(), listRacks()]);
    rows.value = d.items;
    locations.value = l.items;
    racks.value = rk.items;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

// 匯出全部：分頁抓完整裝置清單（清單畫面預設只載前 200 筆）
async function fetchAllForExport(): Promise<Device[]> {
  const all: Device[] = [];
  const big = 500;   // 後端 page_size 上限
  let p = 1;
  for (;;) {
    const res = await listDevices({ page: p, pageSize: big });
    all.push(...res.items);
    if (res.items.length === 0 || all.length >= res.total) break;
    p++;
  }
  return all;
}

function openCreate() {
  editing.value = null;
  form.value = {
    name: "", fqdn: "", type: "server", vendor: "", model: "", serial: "",
    description: "", location_id: null, rack_id: null,
    u_position: null, u_size: null, rack_face: null, rack_side: "full", customer_id: null, primary_ip_id: null,
  };
  void ensureCustomersLoaded();
  void loadAddresses();
  show.value = true;
}

function openEdit(r: Device) {
  editing.value = r;
  form.value = {
    name: r.name, fqdn: r.fqdn ?? "", type: r.type,
    vendor: r.vendor ?? "", model: r.model ?? "", serial: r.serial ?? "",
    description: r.description ?? "",
    location_id: r.location_id, rack_id: r.rack_id,
    u_position: r.u_position, u_size: r.u_size,
    rack_face: (r as any).rack_face ?? null,
    rack_side: (r as any).rack_side ?? "full",
    customer_id: r.customer_id ?? null,
    primary_ip_id: (r as any).primary_ip_id ?? null,
  };
  void ensureCustomersLoaded();
  void loadAddresses();
  show.value = true;
}

// 切 location 時清掉 rack(避免選到別 location 的 rack)
function onLocationChange() {
  const rackStillValid = racks.value.find((r) => r.id === form.value.rack_id)?.location_id === form.value.location_id;
  if (!rackStillValid) { form.value.rack_id = null; uPickerDiagram.value = null; }
}
function onRackChange() { uPickerDiagram.value = null; }

// ── 迷你機櫃 U 位挑選器 ──
const showUPicker = ref(false);
const uPickerDiagram = ref<RackDiagram | null>(null);
const uPickerLoading = ref(false);
// 每個 U 的左/右半占用（full 裝置占兩半）。半 U 裝置只占一半，另一半仍可放。
const uHalf = computed<Record<number, { left: string | null; right: string | null }>>(() => {
  const m: Record<number, { left: string | null; right: string | null }> = {};
  for (const d of uPickerDiagram.value?.devices ?? []) {
    if (editing.value && d.device_id === editing.value.id) continue;  // 編輯中的自己不算占用
    const side = d.rack_side ?? "full";
    for (let u = d.u_position; u < d.u_position + d.u_size; u++) {
      const cell = (m[u] ??= { left: null, right: null });
      if (side === "left") cell.left = d.name;
      else if (side === "right") cell.right = d.name;
      else { cell.left = d.name; cell.right = d.name; }
    }
  }
  return m;
});
// 此 U 對「目前要放的占寬」是否可選（需要的半邊要空）
function uPickable(u: number): boolean {
  const cell = uHalf.value[u];
  if (!cell) return true;
  const side = form.value.rack_side;
  if (side === "left") return !cell.left;
  if (side === "right") return !cell.right;
  return !cell.left && !cell.right;
}
// 此 U 的占用顯示文字（半 U 分左右顯示）
function uCellText(u: number): string {
  const cell = uHalf.value[u];
  if (!cell || (!cell.left && !cell.right)) return t("devices.u_free");
  if (cell.left && cell.left === cell.right) return cell.left;            // full
  return `L：${cell.left || t("devices.u_free")}　R：${cell.right || t("devices.u_free")}`;
}
const uRows = computed(() => {
  const n = uPickerDiagram.value?.u_height ?? 0;
  return Array.from({ length: n }, (_, i) => n - i);   // 由上而下 = 大U在上
});
async function openUPicker() {
  if (!form.value.rack_id) return;
  uPickerLoading.value = true;
  showUPicker.value = true;
  try { uPickerDiagram.value = await getRackDiagram(form.value.rack_id); }
  catch { msg.error(t("errors.network")); }
  finally { uPickerLoading.value = false; }
}
function pickU(u: number) {
  form.value.u_position = u;
  if (!form.value.u_size) form.value.u_size = 1;
  showUPicker.value = false;
}

async function submit() {
  if (!form.value.name.trim()) {
    msg.error(t("devices.error_name_required"));
    return;
  }
  if (form.value.rack_id && !form.value.location_id) {
    msg.error(t("devices.error_location_for_rack"));
    return;
  }
  try {
    const payload = {
      name: form.value.name,
      fqdn: form.value.fqdn || null,
      type: form.value.type,
      vendor: form.value.vendor || undefined,
      model: form.value.model || undefined,
      serial: form.value.serial || undefined,
      description: form.value.description || undefined,
      location_id: form.value.location_id,
      rack_id: form.value.rack_id,
      u_position: form.value.u_position,
      u_size: form.value.u_size,
      rack_face: form.value.rack_id ? form.value.rack_face : null,
      rack_side: form.value.rack_id ? form.value.rack_side : "full",
      customer_id: form.value.customer_id,
      primary_ip_id: form.value.primary_ip_id,
    };
    if (editing.value) await updateDevice(editing.value.id, payload);
    else await createDevice(payload);
    show.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function del(r: Device) {
  try { await deleteDevice(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "devices",
  ["name", "ip", "fqdn", "type", "vendor", "model", "location_id", "rack_id", "customer_id", "actions"],
  ["name", "ip", "type", "vendor", "model", "location_id", "rack_id", "customer_id", "actions"],
);
const columnPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "ip", label: "IP" },
  { key: "fqdn", label: "FQDN" },
  { key: "type", label: t("cols.type") },
  { key: "vendor", label: t("cols.vendor") },
  { key: "model", label: t("cols.model") },
  { key: "location_id", label: t("cols.location") },
  { key: "rack_id", label: t("cols.rack") },
  { key: "customer_id", label: t("cols.unit") },
  { key: "actions", label: t("cols.actions") },
]);
async function linkMatchingIp(r: Device) {
  if (!r.ip_match_id) return;
  try {
    await updateDevice(r.id, { primary_ip_id: r.ip_match_id } as any);
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const allCols = computed<DataTableColumns<Device>>(() => [
  { type: "selection" },
  {
    title: t("common.name"), key: "name",
    render: (r) => links.device(r.id, r.name),
    sorter: (a, b) => cmpNatural(a.name, b.name),
  },
  {
    title: "IP", key: "ip",
    render: (r) => {
      if (!r.ip) return "—";
      // 有對應的 IP 位址物件 → 可點，帶去該位址
      if (r.ip_address_id) {
        return h("a", {
          href: "#",
          style: "color: var(--primary-color, #18a058); text-decoration: none; cursor: pointer;",
          onClick: (e: MouseEvent) => {
            e.preventDefault(); e.stopPropagation();
            router.push({ name: "addresses", query: { q: r.ip } });
          },
        }, r.ip);
      }
      return r.ip;
    },
    sorter: (a, b) => cmpNatural(a.ip ?? "", b.ip ?? ""),
  },
  {
    title: "FQDN", key: "fqdn",
    render: (r) => r.fqdn ?? "—",
    ellipsis: { tooltip: true },
    sorter: (a, b) => (a.fqdn ?? "").localeCompare(b.fqdn ?? ""),
  },
  {
    title: t("devices.type"), key: "type",
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.type),
    sorter: (a, b) => a.type.localeCompare(b.type),
  },
  {
    title: t("devices.vendor"), key: "vendor",
    render: (r) => r.vendor ?? "—",
    sorter: (a, b) => (a.vendor ?? "").localeCompare(b.vendor ?? ""),
  },
  {
    title: t("devices.model"), key: "model",
    render: (r) => r.model ?? "—",
    sorter: (a, b) => (a.model ?? "").localeCompare(b.model ?? ""),
  },
  {
    title: t("devices.location"), key: "location_id",
    render: (r) => links.location(r.location_id, locations.value.find((l) => l.id === r.location_id)?.name ?? "—"),
    sorter: (a, b) => {
      const an = locations.value.find((l) => l.id === a.location_id)?.name ?? "";
      const bn = locations.value.find((l) => l.id === b.location_id)?.name ?? "";
      return an.localeCompare(bn);
    },
  },
  {
    title: t("devices.rack"), key: "rack_id",
    render: (r) => {
      const rk = racks.value.find((x) => x.id === r.rack_id);
      if (!rk) return "—";
      const label = r.u_position ? `${rk.name} U${r.u_position}` : rk.name;
      return links.rack(r.rack_id, label);
    },
    sorter: (a, b) => {
      const an = racks.value.find((x) => x.id === a.rack_id)?.name ?? "";
      const bn = racks.value.find((x) => x.id === b.rack_id)?.name ?? "";
      return an.localeCompare(bn);
    },
  },
  {
    title: t("nav.customers"), key: "customer_id", width: 160,
    ellipsis: { tooltip: true },
    render: (r) => links.customer(r.customer_id, customerLabelFor(r.customer_id)),
    sorter: (a, b) => customerLabelFor(a.customer_id).localeCompare(customerLabelFor(b.customer_id)),
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 136,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      ...(r.ip_match_id ? [iconAction(LinkIcon, t("devices.link_matching_ip"), () => linkMatchingIp(r), "primary")] : []),
      iconAction(EyeIcon, t("common.view"),
        () => router.push({ name: "device-detail", params: { id: r.id } })),
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);

const cols = computed<DataTableColumns<Device>>(() =>
  allCols.value.filter((c: any) => c.type === "selection" || visibleKeys.value.includes(c.key)),
);

import { useRoute } from "vue-router";
const route = useRoute();
onMounted(async () => {
  await refresh();
  void ensureCustomersLoaded();
  // 從裝置細節頁帶 ?edit=<id> 進來 → 直接開該裝置的編輯
  const editId = route.query.edit as string | undefined;
  if (editId) {
    let r = rows.value.find((d) => d.id === editId);
    if (!r) {
      try { const { data } = await apiClient.get(`/api/v1/devices/${editId}`); r = data; } catch { /* ignore */ }
    }
    if (r) openEdit(r);
  }
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><DevicesIcon /></n-icon>
        <span>{{ t("nav.devices") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 180px" />
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <ColumnPicker :all="columnPickerItems" :visible="visibleKeys"
                    @update:visible="setVisible" @reset="reset" />
      <ExportButton :columns="cols" :rows="rows" :fetch-all="fetchAllForExport"
                    filename="devices" :title="t('nav.devices')" />
      <n-button type="primary" :disabled="_authBtn.me?.can_edit === false" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
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
      :columns="cols"
      :data="filteredRows"
      :loading="loading"
      :bordered="false"
      :scroll-x="1116"
      :pagination="pg"
      :row-key="(row: Device) => row.id"
      :checked-row-keys="checkedKeys"
      @update:checked-row-keys="(keys: DataTableRowKey[]) => checkedKeys = keys"
      :row-props="(row: Device) => ({
        style: 'cursor: pointer',
        onClick: (e: MouseEvent) => {
          const target = e.target as HTMLElement;
          if (target.closest('.n-checkbox')) return;
          router.push({ name: 'device-detail', params: { id: row.id } });
        },
      })"
    />

    <n-modal v-model:show="show" preset="card" style="width: 540px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editing ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form label-placement="top">
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="FQDN">
          <n-input v-model:value="form.fqdn" placeholder="sw1.dc.example.com" />
        </n-form-item>
        <n-form-item :label="t('devices.type')">
          <n-select v-model:value="form.type" :options="typeOpts" />
        </n-form-item>
        <n-space>
          <n-form-item :label="t('devices.vendor')" style="min-width: 220px">
            <n-input v-model:value="form.vendor" placeholder="Cisco / Juniper / Dell …" />
          </n-form-item>
          <n-form-item :label="t('devices.model')" style="min-width: 220px">
            <n-input v-model:value="form.model" placeholder="Catalyst 9300-48P …" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('devices.serial')">
          <n-input v-model:value="form.serial" />
        </n-form-item>
        <n-form-item :label="t('devices.primary_ip')">
          <n-select v-model:value="form.primary_ip_id" :options="ipOptions" filterable clearable
                    :placeholder="t('common.not_specified')" />
        </n-form-item>

        <h4 style="margin: 8px 0 4px 0">{{ t("devices.placement_section") }}</h4>
        <div class="dev-row">
          <n-form-item :label="t('devices.location')">
            <n-select v-model:value="form.location_id" :options="locationOpts" filterable clearable
                      :placeholder="t('devices.location_placeholder')"
                      @update:value="onLocationChange" style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('devices.rack')">
            <n-select v-model:value="form.rack_id" :options="filteredRackOpts" filterable clearable
                      :placeholder="form.location_id
                        ? t('devices.rack_placeholder')
                        : t('devices.rack_pick_location_first')"
                      :disabled="!form.location_id" style="width: 100%"
                      @update:value="onRackChange" />
          </n-form-item>
        </div>
        <div class="dev-row">
          <n-form-item :label="t('devices.u_position')">
            <n-input-group>
              <n-input-number v-model:value="form.u_position" :min="1" :max="99" clearable
                              :disabled="!form.rack_id" style="flex: 1" />
              <n-button :disabled="!form.rack_id" @click="openUPicker" :title="t('devices.pick_u')">
                <template #icon><n-icon><RacksIcon /></n-icon></template>
              </n-button>
            </n-input-group>
          </n-form-item>
          <n-form-item :label="t('devices.u_size')">
            <n-input-number v-model:value="form.u_size" :min="1" :max="99" clearable
                            :disabled="!form.rack_id" style="width: 100%" />
          </n-form-item>
        </div>
        <div class="dev-row">
          <n-form-item :label="t('devices.rack_face')">
            <n-select v-model:value="form.rack_face" :options="rackFaceOpts" clearable
                      :disabled="!form.rack_id" :placeholder="t('devices.rack_face_front')"
                      style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('devices.rack_side')">
            <n-select v-model:value="form.rack_side" :options="rackSideOpts"
                      :disabled="!form.rack_id" style="width: 100%" />
          </n-form-item>
        </div>

        <n-form-item :label="t('nav.customers')" style="margin-top: 8px">
          <n-select v-model:value="form.customer_id" :options="customerOptions"
                    :placeholder="t('common.not_specified')" clearable filterable />
        </n-form-item>

        <n-form-item :label="t('sections.description')" style="margin-top: 8px">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <!-- 迷你機櫃：挑 U 位 -->
    <n-modal v-model:show="showUPicker" preset="card" style="width: 340px" :title="t('devices.pick_u')">
      <n-spin :show="uPickerLoading">
        <p style="font-size:12px; opacity:.65; margin:0 0 8px">{{ t("devices.pick_u_hint") }}</p>
        <div class="upick-rack">
          <div v-for="u in uRows" :key="u" class="upick-row"
               :class="{ occupied: !uPickable(u), cur: form.u_position === u }"
               @click="uPickable(u) && pickU(u)">
            <span class="upick-u">{{ u }}</span>
            <span class="upick-body">{{ uCellText(u) }}</span>
          </div>
        </div>
      </n-spin>
    </n-modal>
  </n-card>
</template>

<style scoped>
/* 地點/機櫃、U位/佔用U數：兩欄等寬 */
.dev-row { display: flex; gap: 12px; }
.dev-row > * { flex: 1 1 0; min-width: 0; }
/* 迷你機櫃 U 位挑選 */
.upick-rack { border: 1px solid var(--n-border-color, rgba(127,127,127,.25)); border-radius: 8px; overflow: hidden; max-height: 60vh; overflow-y: auto; }
.upick-row { display: flex; align-items: center; gap: 8px; height: 26px; padding: 0 8px; font-size: 12px; border-bottom: 1px dashed rgba(127,127,127,.18); cursor: pointer; }
.upick-row:last-child { border-bottom: none; }
.upick-u { width: 28px; text-align: right; opacity: .55; font-variant-numeric: tabular-nums; }
.upick-body { flex: 1; color: var(--n-text-color-3, #888); }
.upick-row:not(.occupied):hover { background: rgba(24,160,88,.12); }
.upick-row:not(.occupied):hover .upick-body { color: var(--primary-color, #18a058); font-weight: 600; }
.upick-row.occupied { cursor: not-allowed; background: rgba(127,127,127,.12); }
.upick-row.occupied .upick-body { color: var(--n-text-color-2, #555); font-weight: 600; }
.upick-row.cur { box-shadow: inset 3px 0 0 var(--primary-color, #18a058); }
</style>
