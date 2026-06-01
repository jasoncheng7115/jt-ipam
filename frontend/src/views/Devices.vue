<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { apiClient } from "@/api/client";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NInputNumber, NSelect, NPopconfirm, NTag, NTooltip,
  useMessage, type DataTableColumns, type DataTableRowKey,
} from "naive-ui";
import {
  listDevices, createDevice, updateDevice, deleteDevice, bulkDeleteDevices, type Device,
  listLocations, listRacks, type Location, type Rack,
} from "@/api/basic";
import {
  DevicesIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon, EyeIcon,
} from "@/icons";
import { cmpNatural } from "@/utils/sort";
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
  customer_id: string | null;
}>({
  name: "", fqdn: "", type: "server",
  vendor: "", model: "", serial: "",
  description: "",
  location_id: null, rack_id: null,
  u_position: null, u_size: null,
  customer_id: null,
});

const typeOpts = ["server", "switch", "router", "firewall", "ap", "storage", "ipmi", "other"]
  .map((v) => ({ label: v, value: v }));

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

function openCreate() {
  editing.value = null;
  form.value = {
    name: "", fqdn: "", type: "server", vendor: "", model: "", serial: "",
    description: "", location_id: null, rack_id: null,
    u_position: null, u_size: null, customer_id: null,
  };
  void ensureCustomersLoaded();
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
    customer_id: r.customer_id ?? null,
  };
  void ensureCustomersLoaded();
  show.value = true;
}

// 切 location 時清掉 rack(避免選到別 location 的 rack)
function onLocationChange() {
  const rackStillValid = racks.value.find((r) => r.id === form.value.rack_id)?.location_id === form.value.location_id;
  if (!rackStillValid) form.value.rack_id = null;
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
      customer_id: form.value.customer_id,
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
    render: (r) => r.ip ?? "—",
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
      <ExportButton :columns="cols" :rows="rows" filename="devices" :title="t('nav.devices')" />
      <n-button type="primary" @click="openCreate">
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

        <h4 style="margin: 8px 0 4px 0">{{ t("devices.placement_section") }}</h4>
        <n-space>
          <n-form-item :label="t('devices.location')" style="min-width: 240px">
            <n-select v-model:value="form.location_id" :options="locationOpts" filterable clearable
                      :placeholder="t('devices.location_placeholder')"
                      @update:value="onLocationChange" />
          </n-form-item>
          <n-form-item :label="t('devices.rack')" style="min-width: 240px">
            <n-select v-model:value="form.rack_id" :options="filteredRackOpts" filterable clearable
                      :placeholder="form.location_id
                        ? t('devices.rack_placeholder')
                        : t('devices.rack_pick_location_first')"
                      :disabled="!form.location_id" />
          </n-form-item>
        </n-space>
        <n-space>
          <n-form-item :label="t('devices.u_position')" style="min-width: 160px">
            <n-input-number v-model:value="form.u_position" :min="1" :max="99" clearable
                            :disabled="!form.rack_id" />
          </n-form-item>
          <n-form-item :label="t('devices.u_size')" style="min-width: 160px">
            <n-input-number v-model:value="form.u_size" :min="1" :max="99" clearable
                            :disabled="!form.rack_id" />
          </n-form-item>
        </n-space>

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
  </n-card>
</template>
