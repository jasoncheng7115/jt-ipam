<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NTag, NModal, NForm, NFormItem,
  NSelect, NInput, NInputNumber, NPopconfirm, NTabs, NTabPane,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { PhysicalIcon, PowerIcon, VpnIcon, RefreshIcon, PlusIcon, EditIcon, DeleteIcon, ListIcon } from "@/icons";
import { Physical, type DevicePort } from "@/api/phase3";
import { listDevices, listLocations } from "@/api/basic";
import { useCustomers } from "@/composables/useCustomers";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
const canEdit = computed(() => _authBtn.me?.can_edit !== false);

// 三個獨立頁面共用此元件，以 mode 決定顯示哪一段（佈線 / 電力 / VPN）
const props = defineProps<{ mode?: "cabling" | "power" | "vpn" }>();
const mode = computed(() => props.mode ?? "cabling");

const { t } = useI18n();
const msg = useMessage();

const cables = ref<any[]>([]);
const panels = ref<any[]>([]);
const feeds = ref<any[]>([]);
const outlets = ref<any[]>([]);
const vpns = ref<any[]>([]);
const loading = ref(false);

// 共用下拉資料
const devices = ref<{ id: string; name: string }[]>([]);
const locations = ref<{ id: string; name: string }[]>([]);
const deviceOpts = computed(() => devices.value.map((d) => ({ label: d.name, value: d.id })));

// 佈線篩選：文字（兩端/類型/線標/說明）+ 裝置 + 單位 + 機房（兩端任一符合即顯示）
const { options: customerOptions, ensureLoaded: ensureCustomers } = useCustomers();
const cableFilterText = ref("");
const cableFilterDevice = ref<string | null>(null);
const cableFilterCustomer = ref<string | null>(null);
const cableFilterLocation = ref<string | null>(null);
const filteredCables = computed(() => {
  const q = cableFilterText.value.trim().toLowerCase();
  const dev = cableFilterDevice.value;
  const cust = cableFilterCustomer.value;
  const loc = cableFilterLocation.value;
  return cables.value.filter((c: any) => {
    if (dev && c.a_device_id !== dev && c.b_device_id !== dev) return false;
    if (cust && c.a_customer_id !== cust && c.b_customer_id !== cust) return false;
    if (loc && c.a_location_id !== loc && c.b_location_id !== loc) return false;
    if (q) {
      const hay = [c.type, c.label, c.a_end, c.b_end, c.status, c.description]
        .map((x) => String(x ?? "").toLowerCase()).join(" ");
      if (!hay.includes(q)) return false;
    }
    return true;
  });
});
const locationOpts = computed(() => locations.value.map((l) => ({ label: l.name, value: l.id })));

async function refresh() {
  loading.value = true;
  try {
    if (mode.value === "cabling") {
      cables.value = await Physical.cables();
    } else if (mode.value === "power") {
      [panels.value, feeds.value, outlets.value] = await Promise.all([
        Physical.panels(), Physical.feeds(), Physical.outlets(),
      ]);
    } else {
      vpns.value = await Physical.vpns();
    }
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

async function ensureDevices() {
  if (!devices.value.length) {
    try { devices.value = (await listDevices({ pageSize: 500 })).items.map((d: any) => ({ id: d.id, name: d.name })); } catch { /* */ }
  }
}
async function ensureLocations() {
  if (!locations.value.length) {
    try { locations.value = (await listLocations()).items.map((l: any) => ({ id: l.id, name: l.name })); } catch { /* */ }
  }
}

const pageTitle = computed(() =>
  mode.value === "power" ? t("nav.power")
    : mode.value === "vpn" ? t("nav.vpn_tunnels")
    : t("nav.cabling"));
const PageIcon = computed(() => (mode.value === "power" ? PowerIcon : mode.value === "vpn" ? VpnIcon : PhysicalIcon));

function statusTag(s: string) {
  const type = s === "connected" ? "success" : s === "planned" ? "info" : "default";
  return h(NTag, { size: "small", type, bordered: false }, () => s);
}

// 纜線兩端 device@port：把分隔符 @ 染成不同顏色，裝置與連接埠一眼分得開
function renderEnd(val: string | null) {
  if (!val) return "—";
  const i = val.indexOf("@");
  if (i < 0) return val;
  return h("span", null, [
    val.slice(0, i),
    h("span", { style: "color:#18a058;font-weight:700;margin:0 1px" }, "@"),
    val.slice(i + 1),
  ]);
}
const cableColsAll = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("cols.type"), key: "type", width: 100, render: (r: any) => r.type ?? "—" },
  { title: t("physical.cable_label"), key: "label", width: 120, ellipsis: { tooltip: true }, render: (r: any) => r.label ?? "—" },
  { title: t("physical.a_end"), key: "a_end", minWidth: 190, ellipsis: { tooltip: true }, render: (r: any) => renderEnd(r.a_end) },
  { title: t("physical.b_end"), key: "b_end", minWidth: 190, ellipsis: { tooltip: true }, render: (r: any) => renderEnd(r.b_end) },
  { title: t("common.status"), key: "status", width: 100, render: (r: any) => statusTag(r.status) },
  { title: t("sections.description"), key: "description", minWidth: 150, ellipsis: { tooltip: true }, render: (r: any) => r.description ?? "—" },
  { title: t("common.actions"), key: "actions", width: 96, render: (r: any) => h(NSpace, { size: 4 }, () => [
    h(NButton, { size: "tiny", quaternary: true, disabled: !canEdit.value, onClick: () => openEditCable(r) },
      { icon: () => h(NIcon, null, () => h(EditIcon)) }),
    h(NPopconfirm, { onPositiveClick: () => delCable(r) }, {
      trigger: () => h(NButton, { size: "tiny", quaternary: true, type: "error", disabled: !canEdit.value },
        { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
      default: () => t("common.confirm_delete"),
    }),
  ]) },
]));
const cableKeys = ["type", "label", "a_end", "b_end", "status", "description", "actions"];
const cablePickerItems = computed(() => cableColsAll.value
  .map((c: any) => ({ key: c.key, label: typeof c.title === "function" ? c.title() : c.title }))
  .filter((c: any) => c.key));
const { visibleKeys: cableVisible, setVisible: setCableVisible, reset: resetCableVisible } =
  useColumnPrefs("cables", cableKeys, cableKeys);
const cableCols = computed<DataTableColumns<any>>(() =>
  cableColsAll.value.filter((c: any) => cableVisible.value.includes(c.key)));
const panelCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("nav.locations"), key: "location_id", render: (r: any) => locations.value.find((l) => l.id === r.location_id)?.name ?? "—" },
  powerActionsCol("panel"),
]));
const feedCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("cols.panel"), key: "panel_id", render: (r: any) => panels.value.find((p) => p.id === r.panel_id)?.name ?? r.panel_id },
  powerActionsCol("feed"),
]));
const outletCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "label", render: (r: any) => r.label ?? r.name ?? "—" },
  { title: t("cols.feed"), key: "feed_id", render: (r: any) => feeds.value.find((f) => f.id === r.feed_id)?.name ?? "—" },
  powerActionsCol("outlet"),
]));
// 電力三表的欄位顯示偏好（ColumnPicker + useColumnPrefs）+ 即時篩選。actions 欄永遠保留。
function usePowerPrefs(name: string, cols: typeof panelCols, rows: typeof panels) {
  const allKeys = cols.value.filter((c: any) => c.key && c.key !== "actions").map((c: any) => String(c.key));
  const { visibleKeys, setVisible, reset } = useColumnPrefs(`power_${name}`, allKeys, allKeys);
  const items = computed(() => cols.value
    .filter((c: any) => c.key && c.key !== "actions")
    .map((c: any) => ({ key: String(c.key), label: typeof c.title === "string" ? c.title : String(c.key) })));
  const visibleCols = computed<DataTableColumns<any>>(() =>
    cols.value.filter((c: any) => c.key === "actions" || visibleKeys.value.includes(String(c.key))));
  const { query, filtered } = useTableQuickFilter(rows);
  return reactive({ visibleKeys, setVisible, reset, items, visibleCols, query, filtered });
}
const panelP = usePowerPrefs("panels", panelCols, panels);
const feedP = usePowerPrefs("feeds", feedCols, feeds);
const outletP = usePowerPrefs("outlets", outletCols, outlets);

const vpnCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("cols.type"), key: "type" },
  { title: t("common.status"), key: "status" },
  {
    title: t("physical.vpn_peer"), key: "peer", minWidth: 260,
    render: (r) => {
      if (r.peered && r.a_device_name && r.b_device_name) {
        const reliable = r.pairing_method === "wireguard_pubkey";
        const badge = h(NTag, {
          size: "tiny", type: reliable ? "success" : "warning", bordered: false,
          style: "margin-left:8px",
        }, () => reliable ? t("physical.pair_reliable") : t("physical.pair_besteffort"));
        return h("span", { class: "vpn-peered" }, [
          `${r.a_device_name} ⇄ ${r.b_device_name}`, badge,
        ]);
      }
      if (r.b_endpoint) return h("span", { style: "opacity:.7" }, `→ ${r.b_endpoint}`);
      return h("span", { style: "opacity:.4" }, "—");
    },
  },
]));

// ── 新增纜線 ──
const cableTypeOpts = ["cat5e", "cat6", "cat6a", "dac", "fiber-mm", "fiber-sm"].map((v) => ({ label: v, value: v }));
const cableStatusOpts = ["connected", "planned", "decommissioned"].map((v) => ({ label: t(`physical.cable_status_${v}`), value: v }));
const showCable = ref(false);
const savingCable = ref(false);
const cableForm = ref<{ aDevice: string | null; aPort: string | null; bDevice: string | null; bPort: string | null; type: string | null; label: string }>(
  { aDevice: null, aPort: null, bDevice: null, bPort: null, type: "cat6", label: "" });
const aPorts = ref<DevicePort[]>([]);
const bPorts = ref<DevicePort[]>([]);
const portOpts = (ps: DevicePort[]) => ps.map((p) => ({ label: `${p.name}${p.type ? ` (${p.type})` : ""}`, value: p.id }));
watch(() => cableForm.value.aDevice, async (d) => { cableForm.value.aPort = null; aPorts.value = d ? await Physical.ports(d) : []; });
watch(() => cableForm.value.bDevice, async (d) => { cableForm.value.bPort = null; bPorts.value = d ? await Physical.ports(d) : []; });
async function openCable() { await ensureDevices(); cableForm.value = { aDevice: null, aPort: null, bDevice: null, bPort: null, type: "cat6", label: "" }; aPorts.value = []; bPorts.value = []; showCable.value = true; }
async function saveCable() {
  const f = cableForm.value;
  if (!f.aPort || !f.bPort) { msg.warning(t("physical.pick_both_ports")); return; }
  if (f.aPort === f.bPort) { msg.warning(t("physical.same_port")); return; }
  savingCable.value = true;
  try {
    await Physical.connectPorts(f.aPort, f.bPort, { type: f.type ?? undefined, label: f.label.trim() || undefined });
    msg.success(t("common.ok")); showCable.value = false; await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); } finally { savingCable.value = false; }
}

// ── 編輯 / 刪除纜線 ──
const showCableEdit = ref(false);
const savingCableEdit = ref(false);
const editingCableId = ref<string | null>(null);
const cableEditForm = ref<{ type: string | null; label: string; color: string; length_m: number | null; description: string; status: string }>(
  { type: null, label: "", color: "", length_m: null, description: "", status: "connected" });
function openEditCable(r: any) {
  editingCableId.value = r.id;
  cableEditForm.value = {
    type: r.type ?? null, label: r.label ?? "", color: r.color ?? "",
    length_m: r.length_m ?? null, description: r.description ?? "", status: r.status ?? "connected",
  };
  showCableEdit.value = true;
}
async function saveEditCable() {
  if (!editingCableId.value) return;
  savingCableEdit.value = true;
  try {
    const f = cableEditForm.value;
    await Physical.updateCable(editingCableId.value, {
      type: f.type, label: f.label.trim() || null, color: f.color.trim() || null,
      length_m: f.length_m, description: f.description.trim() || null, status: f.status,
    });
    msg.success(t("common.ok")); showCableEdit.value = false; await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); } finally { savingCableEdit.value = false; }
}
async function delCable(r: any) {
  try { await Physical.deleteCable(r.id); msg.success(t("common.ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
}

// ── 新增電力（配電盤 / 供電迴路 / 插座）──
const showPower = ref(false);
const powerKind = ref<"panel" | "feed" | "outlet">("panel");
const savingPower = ref(false);
const powerForm = ref<{ name: string; location_id: string | null; panel_id: string | null; feed_id: string | null }>(
  { name: "", location_id: null, panel_id: null, feed_id: null });
const panelOpts = computed(() => panels.value.map((p) => ({ label: p.name, value: p.id })));
const feedOpts = computed(() => feeds.value.map((f) => ({ label: f.name, value: f.id })));
const powerTitle = computed(() => powerKind.value === "panel" ? t("physical.panels") : powerKind.value === "feed" ? t("physical.feeds") : t("physical.outlets"));
const powerEditId = ref<string | null>(null);
async function openPower(kind: "panel" | "feed" | "outlet") {
  powerKind.value = kind;
  powerEditId.value = null;
  powerForm.value = { name: "", location_id: null, panel_id: panels.value[0]?.id ?? null, feed_id: feeds.value[0]?.id ?? null };
  if (kind === "panel") await ensureLocations();
  showPower.value = true;
}
async function openPowerEdit(kind: "panel" | "feed" | "outlet", r: any) {
  powerKind.value = kind;
  powerEditId.value = r.id;
  powerForm.value = {
    name: r.name ?? r.label ?? "",
    location_id: r.location_id ?? null,
    panel_id: r.panel_id ?? null,
    feed_id: r.feed_id ?? null,
  };
  if (kind === "panel") await ensureLocations();
  showPower.value = true;
}
async function savePower() {
  const f = powerForm.value;
  if (!f.name.trim()) { msg.warning(t("physical.need_name")); return; }
  if (powerKind.value === "feed" && !f.panel_id) { msg.warning(t("physical.need_panel")); return; }
  savingPower.value = true;
  const id = powerEditId.value;
  try {
    if (powerKind.value === "panel") {
      if (id) await Physical.updatePanel(id, { name: f.name.trim(), location_id: f.location_id });
      else await Physical.createPanel({ name: f.name.trim(), location_id: f.location_id });
    } else if (powerKind.value === "feed") {
      if (id) await Physical.updateFeed(id, { panel_id: f.panel_id!, name: f.name.trim() });
      else await Physical.createFeed({ panel_id: f.panel_id!, name: f.name.trim() });
    } else if (id) {
      await Physical.updateOutlet(id, { feed_id: f.feed_id, label: f.name.trim() });
    } else {
      await Physical.createOutlet({ feed_id: f.feed_id, label: f.name.trim() });
    }
    msg.success(t("common.ok")); showPower.value = false; await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); } finally { savingPower.value = false; }
}
async function delPower(kind: "panel" | "feed" | "outlet", r: any) {
  try {
    if (kind === "panel") await Physical.deletePanel(r.id);
    else if (kind === "feed") await Physical.deleteFeed(r.id);
    else await Physical.deleteOutlet(r.id);
    msg.success(t("common.ok")); await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
}
function powerActionsCol(kind: "panel" | "feed" | "outlet") {
  return {
    title: t("common.actions"), key: "actions", width: 96,
    render: (r: any) => h(NSpace, { size: 4 }, () => [
      h(NButton, { size: "tiny", quaternary: true, disabled: !canEdit.value, onClick: () => openPowerEdit(kind, r) },
        { icon: () => h(NIcon, null, () => h(EditIcon)) }),
      h(NPopconfirm, { onPositiveClick: () => delPower(kind, r) }, {
        trigger: () => h(NButton, { size: "tiny", quaternary: true, type: "error", disabled: !canEdit.value },
          { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  };
}

function ensureCablingFilters() {
  void ensureDevices(); void ensureLocations(); void ensureCustomers();
}
onMounted(() => { void refresh(); if (mode.value === "cabling") ensureCablingFilters(); });
watch(mode, () => { void refresh(); if (mode.value === "cabling") ensureCablingFilters(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><component :is="PageIcon" /></n-icon>
        <span>{{ pageTitle }}</span>
      </n-space>
    </template>
    <n-space align="center" style="margin-bottom: 12px">
      <template v-if="mode === 'cabling'">
        <n-input v-model:value="cableFilterText" clearable style="width: 200px"
                 :placeholder="t('physical.cable_filter_ph')" />
        <n-select v-model:value="cableFilterDevice" :options="deviceOpts" filterable clearable
                  style="width: 200px" :placeholder="t('physical.cable_filter_device')" />
        <n-select v-model:value="cableFilterCustomer" :options="customerOptions" filterable clearable
                  style="width: 170px" :placeholder="t('physical.cable_filter_customer')" />
        <n-select v-model:value="cableFilterLocation" :options="locationOpts" filterable clearable
                  style="width: 170px" :placeholder="t('physical.cable_filter_location')" />
      </template>
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button v-if="mode === 'cabling'" type="primary" :disabled="!canEdit" @click="openCable">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("physical.add_cable") }}
      </n-button>
      <ColumnPicker v-if="mode === 'cabling'" :all="cablePickerItems" :visible="cableVisible"
                    @update:visible="setCableVisible" @reset="resetCableVisible" />
      <ExportButton v-if="mode === 'cabling'" :columns="cablePickerItems" :rows="filteredCables"
                    filename="cables" :title="t('nav.cabling')" />
    </n-space>

    <n-data-table v-if="mode === 'cabling'"
      :columns="cableCols" :data="filteredCables" :loading="loading" :bordered="false" :scroll-x="900" :pagination="pg" />

    <!-- 電力：配電盤 / 供電迴路 / 插座 三表拆內層頁籤（比照防火牆規則/別名：type=line + icon） -->
    <n-tabs v-else-if="mode === 'power'" type="line">
      <n-tab-pane name="panels">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><PowerIcon /></n-icon>{{ t('physical.panels') }} ({{ panels.length }})</span>
        </template>
        <n-space align="center" style="margin:8px 0">
          <n-input v-model:value="panelP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openPower('panel')">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("common.add") }}
          </n-button>
          <ColumnPicker :all="panelP.items" :visible="panelP.visibleKeys"
                        @update:visible="panelP.setVisible" @reset="panelP.reset" />
          <ExportButton :columns="panelP.visibleCols" :rows="panelP.filtered" filename="power-panels" :title="t('physical.panels')" />
        </n-space>
        <n-data-table :columns="panelP.visibleCols" :data="panelP.filtered" :loading="loading" :bordered="false" :pagination="pg" />
      </n-tab-pane>
      <n-tab-pane name="feeds">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ListIcon /></n-icon>{{ t('physical.feeds') }} ({{ feeds.length }})</span>
        </template>
        <n-space align="center" style="margin:8px 0">
          <n-input v-model:value="feedP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" :disabled="!panels.length" @click="openPower('feed')">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("common.add") }}
          </n-button>
          <ColumnPicker :all="feedP.items" :visible="feedP.visibleKeys"
                        @update:visible="feedP.setVisible" @reset="feedP.reset" />
          <ExportButton :columns="feedP.visibleCols" :rows="feedP.filtered" filename="power-feeds" :title="t('physical.feeds')" />
        </n-space>
        <n-data-table :columns="feedP.visibleCols" :data="feedP.filtered" :loading="loading" :bordered="false" :pagination="pg" />
      </n-tab-pane>
      <n-tab-pane name="outlets">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ListIcon /></n-icon>{{ t('physical.outlets') }} ({{ outlets.length }})</span>
        </template>
        <n-space align="center" style="margin:8px 0">
          <n-input v-model:value="outletP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openPower('outlet')">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("common.add") }}
          </n-button>
          <ColumnPicker :all="outletP.items" :visible="outletP.visibleKeys"
                        @update:visible="outletP.setVisible" @reset="outletP.reset" />
          <ExportButton :columns="outletP.visibleCols" :rows="outletP.filtered" filename="power-outlets" :title="t('physical.outlets')" />
        </n-space>
        <n-data-table :columns="outletP.visibleCols" :data="outletP.filtered" :loading="loading" :bordered="false" :pagination="pg" />
      </n-tab-pane>
    </n-tabs>

    <n-data-table v-else
      :columns="vpnCols" :data="vpns" :loading="loading" :bordered="false" :pagination="pg" />

    <!-- 新增纜線 -->
    <n-modal v-model:show="showCable" preset="card" style="max-width:520px">
      <template #header>
        <n-space align="center" :size="8" :wrap-item="false">
          <n-icon :size="18"><PlusIcon /></n-icon>{{ t('physical.add_cable') }}
        </n-space>
      </template>
      <n-form label-placement="top">
        <n-form-item :label="t('physical.a_device')">
          <n-select v-model:value="cableForm.aDevice" :options="deviceOpts" filterable clearable :placeholder="t('physical.pick_device')" />
        </n-form-item>
        <n-form-item :label="t('physical.a_port')">
          <n-select v-model:value="cableForm.aPort" :options="portOpts(aPorts)" :disabled="!cableForm.aDevice" filterable clearable :placeholder="t('physical.pick_port')" />
        </n-form-item>
        <n-form-item :label="t('physical.b_device')">
          <n-select v-model:value="cableForm.bDevice" :options="deviceOpts" filterable clearable :placeholder="t('physical.pick_device')" />
        </n-form-item>
        <n-form-item :label="t('physical.b_port')">
          <n-select v-model:value="cableForm.bPort" :options="portOpts(bPorts)" :disabled="!cableForm.bDevice" filterable clearable :placeholder="t('physical.pick_port')" />
        </n-form-item>
        <n-space :wrap-item="false" style="width:100%">
          <n-form-item :label="t('cols.type')" style="width:150px">
            <n-select v-model:value="cableForm.type" :options="cableTypeOpts" style="width:100%" :consistent-menu-width="false" />
          </n-form-item>
          <n-form-item :label="t('physical.cable_label')" style="flex:1">
            <n-input v-model:value="cableForm.label" placeholder="(optional)" />
          </n-form-item>
        </n-space>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCable = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="savingCable" @click="saveCable">{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 編輯纜線 -->
    <n-modal v-model:show="showCableEdit" preset="card" style="max-width:460px">
      <template #header>
        <n-space align="center" :size="8" :wrap-item="false">
          <n-icon :size="18"><EditIcon /></n-icon>{{ t('physical.edit_cable') }}
        </n-space>
      </template>
      <n-form label-placement="top">
        <n-form-item :label="t('cols.type')">
          <n-select v-model:value="cableEditForm.type" :options="cableTypeOpts" clearable />
        </n-form-item>
        <n-form-item :label="t('physical.cable_label')">
          <n-input v-model:value="cableEditForm.label" placeholder="(optional)" />
        </n-form-item>
        <n-space>
          <n-form-item :label="t('physical.cable_color')" style="flex:1">
            <n-input v-model:value="cableEditForm.color" placeholder="(optional)" />
          </n-form-item>
          <n-form-item :label="t('physical.cable_length_m')" style="flex:1">
            <n-input-number v-model:value="cableEditForm.length_m" :min="0" :max="10000" style="width:100%" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('common.status')">
          <n-select v-model:value="cableEditForm.status" :options="cableStatusOpts" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="cableEditForm.description" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCableEdit = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="savingCableEdit" @click="saveEditCable">{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 新增電力 -->
    <n-modal v-model:show="showPower" preset="card" style="max-width:460px">
      <template #header>
        <n-space align="center" :size="8" :wrap-item="false">
          <n-icon :size="18"><component :is="powerEditId ? EditIcon : PlusIcon" /></n-icon>
          {{ (powerEditId ? t('common.edit') : t('common.add')) }} — {{ powerTitle }}
        </n-space>
      </template>
      <n-form label-placement="top">
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="powerForm.name" />
        </n-form-item>
        <n-form-item v-if="powerKind === 'panel'" :label="t('nav.locations')">
          <n-select v-model:value="powerForm.location_id" :options="locationOpts" filterable clearable />
        </n-form-item>
        <n-form-item v-if="powerKind === 'feed'" :label="t('cols.panel')">
          <n-select v-model:value="powerForm.panel_id" :options="panelOpts" filterable />
        </n-form-item>
        <n-form-item v-if="powerKind === 'outlet'" :label="t('cols.feed')">
          <n-select v-model:value="powerForm.feed_id" :options="feedOpts" filterable clearable />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showPower = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="savingPower" @click="savePower">{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.vpn-peered {
  color: #18a058;
  font-weight: 600;
}
</style>
