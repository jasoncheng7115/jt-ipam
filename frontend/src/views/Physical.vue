<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NTag, NModal, NForm, NFormItem,
  NSelect, NInput, NInputNumber, NPopconfirm, NProgress, NTabs, NTabPane, NTooltip, NCheckbox,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { PhysicalIcon, PowerIcon, VpnIcon, RefreshIcon, PlusIcon, EditIcon, DeleteIcon, ListIcon, CancelIcon, SaveIcon, LinkIcon, PowerFailureIcon } from "@/icons";
import { useRoute, useRouter } from "vue-router";
import CableTraceDrawer from "@/components/CableTraceDrawer.vue";
import { Physical, type DevicePort } from "@/api/phase3";
import { listDevices, listLocations, listRacks } from "@/api/basic";
import { useCustomers } from "@/composables/useCustomers";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import CableAttrFields, { blankCableAttrs, type CableAttrs } from "@/components/CableAttrFields.vue";
import { useDeviceOptions } from "@/composables/useDeviceOptions";
import VpnTunnelTable from "@/components/VpnTunnelTable.vue";
import PhysicalCheckDrawer from "@/components/PhysicalCheckDrawer.vue";
import PowerFailureDrawer from "@/components/PowerFailureDrawer.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
import { useAuthStore } from "@/stores/auth";
import { apiErrMsg } from "@/api/client";
const _authBtn = useAuthStore();
const canEdit = computed(() => _authBtn.me?.can_edit !== false);
const isAdmin = computed(() => !!_authBtn.me?.is_admin);
const route = useRoute();
const router = useRouter();

// 三個獨立頁面共用此元件，以 mode 決定顯示哪一段（佈線 / 電力 / VPN）
const props = defineProps<{ mode?: "cabling" | "power" | "vpn" }>();
const mode = computed(() => props.mode ?? "cabling");

const { t } = useI18n();
const msg = useMessage();

const cables = ref<any[]>([]);
const panels = ref<any[]>([]);
const feeds = ref<any[]>([]);
const outlets = ref<any[]>([]);
const loading = ref(false);

// 共用下拉資料
const devices = ref<{ id: string; name: string }[]>([]);
const locations = ref<{ id: string; name: string }[]>([]);
const racks = ref<{ id: string; name: string }[]>([]);
const rackOpts = computed(() => racks.value.map((r) => ({ label: r.name, value: r.id })));
const rackName = (id: string | null | undefined) => (id ? racks.value.find((r) => r.id === id)?.name ?? "—" : "—");
const deviceOpts = computed(() => devices.value.map((d) => ({ label: d.name, value: d.id })));

// 佈線篩選：文字（兩端/類型/線標/說明）+ 裝置 + 單位 + 機房（兩端任一符合即顯示）
const { options: customerOptions, ensureLoaded: ensureCustomers } = useCustomers();
const cableFilterText = ref("");
const cableFilterDevice = ref<string | null>(null);
const cableFilterCustomer = ref<string | null>(null);
const cableFilterLocation = ref<string | null>(null);
const cableFilterRack = ref<string | null>(null);
const cableFilterStatus = ref<string | null>(null);
const cableFilterNoLabel = ref(false);
const filteredCables = computed(() => {
  const q = cableFilterText.value.trim().toLowerCase();
  const dev = cableFilterDevice.value;
  const cust = cableFilterCustomer.value;
  const loc = cableFilterLocation.value;
  const rack = cableFilterRack.value;
  // 裝置、機房、機櫃要落在同一端（交接包 physical-v1.2：A 端在機房 X、B 端在機櫃 Y 不算符合）
  const endOk = (c: any, s: "a" | "b") => (!dev || c[`${s}_device_id`] === dev)
    && (!loc || c[`${s}_location_id`] === loc) && (!rack || c[`${s}_rack_id`] === rack);
  return cables.value.filter((c: any) => {
    if ((dev || loc || rack) && !endOk(c, "a") && !endOk(c, "b")) return false;
    if (cust && c.a_customer_id !== cust && c.b_customer_id !== cust) return false;
    if (cableFilterStatus.value && c.status !== cableFilterStatus.value) return false;
    if (cableFilterNoLabel.value && String(c.label ?? "").trim()) return false;
    if (q) {
      const hay = [c.type, c.label, c.a_end, c.b_end, c.status, c.description]
        .map((x) => String(x ?? "").toLowerCase()).join(" ");
      if (!hay.includes(q)) return false;
    }
    return true;
  });
});
const locationOpts = computed(() => locations.value.map((l) => ({ label: l.name, value: l.id })));
const cableStatusOpts = computed(() => ["connected", "planned", "decommissioned"].map((v) => ({ label: t(`physical.cable_status_${v}`), value: v })));
// 追線側欄
const traceCable = ref<any | null>(null);
const showTrace = ref(false);
function openTrace(r: any) { traceCable.value = r; showTrace.value = true; }
// 篩選條件寫進網址（可以分享給別人）
const FILTER_KEYS = { q: cableFilterText, device: cableFilterDevice, customer: cableFilterCustomer,
                      location: cableFilterLocation, rack: cableFilterRack, status: cableFilterStatus } as const;
function readFilterQuery() {
  for (const [k, r] of Object.entries(FILTER_KEYS)) {
    const v = route.query[k];
    if (typeof v === "string" && v) (r as any).value = v;
  }
  if (route.query.nolabel === "1") cableFilterNoLabel.value = true;
}
watch([cableFilterText, cableFilterDevice, cableFilterCustomer, cableFilterLocation, cableFilterRack, cableFilterStatus, cableFilterNoLabel], () => {
  if (mode.value !== "cabling") return;
  const q: Record<string, string> = {};
  for (const [k, r] of Object.entries(FILTER_KEYS)) if ((r as any).value) q[k] = String((r as any).value).trim();
  if (cableFilterNoLabel.value) q.nolabel = "1";
  void router.replace({ query: q });
});

async function refresh() {
  loading.value = true;
  try {
    if (mode.value === "cabling") {
      cables.value = await Physical.cables();
    } else if (mode.value === "power") {
      [panels.value, feeds.value, outlets.value] = await Promise.all([
        Physical.panels(), Physical.feeds(), Physical.outlets(),
      ]);
      void ensureRacks();
    }
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

async function ensureDevices() {
  if (!devices.value.length) {
    try { devices.value = (await listDevices({ pageSize: 500 })).items.map((d: any) => ({ id: d.id, name: d.name })); } catch { /* */ }
  }
}
async function ensureRacks() {
  if (!racks.value.length) {
    try { racks.value = (await listRacks()).items.map((r: any) => ({ id: r.id, name: r.name })); } catch { /* */ }
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
  return h(NTag, { size: "small", type, bordered: false },
    () => (["connected", "planned", "decommissioned"].includes(s) ? t(`physical.cable_status_${s}`) : s));
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
  { title: t("common.actions"), key: "actions", width: 120, render: (r: any) => h(NSpace, { size: 4, wrap: false }, () => [
    h(NTooltip, null, {
      trigger: () => h(NButton, { size: "tiny", quaternary: true, type: "info", disabled: !r.a_port_id && !r.b_port_id,
                                  "data-testid": `cable-trace-${r.label ?? r.id}`, onClick: () => openTrace(r) },
        { icon: () => h(NIcon, null, () => h(LinkIcon)) }),
      default: () => t("cable_trace.title"),
    }),
    h(NButton, { size: "tiny", quaternary: true, type: "success", disabled: !canEdit.value, onClick: () => openEditCable(r) },
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
const { visibleKeys: cableVisible, setVisible: setCableVisible, reset: resetCableVisible,
  order: cableOrder, setOrder: setCableOrder, orderColumns: orderCableCols } =
  useColumnPrefs("cables", cableKeys, cableKeys);
const cableCols = computed<DataTableColumns<any>>(() =>
  orderCableCols(cableColsAll.value.filter((c: any) => cableVisible.value.includes(c.key))));
const panelCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("nav.locations"), key: "location_id", render: (r: any) => locations.value.find((l) => l.id === r.location_id)?.name ?? "—" },
  { title: t("sections.description"), key: "description", minWidth: 160, ellipsis: { tooltip: true }, render: (r: any) => r.description ?? "—" },
  powerActionsCol("panel"),
]));
// 迴路的規劃負載（issue #54，交接包 physical-v1.2）：已知分配 / 人工確認的規劃容量。
// 資料不完整時標「至少」且不用綠色；沒設規劃容量就不算使用率
function loadCell(r: any) {
  const w = `${Number(r.allocated_w ?? 0).toLocaleString()} W`;
  if (r.utilization_pct == null) {
    return h("span", null, [`${r.partial ? t("rack_power.at_least_w", { w }) : w}　`,
      h("span", { class: "hint" }, t("power_status.no_capacity"))]);
  }
  const pct = Number(r.utilization_pct);
  const color = r.status === "over" ? "error" : r.status === "warn" ? "warning" : r.status === "ok" ? "success" : "default";
  const tag = r.status === "over" ? "error" : r.status === "warn" ? "warning" : r.status === "ok" ? "success" : "default";
  return h("span", { style: "display:inline-flex;align-items:center;gap:8px" }, [
    h(NProgress, { type: "line", percentage: Math.min(pct, 100), status: color, showIndicator: false,
                   height: 8, style: "width:80px" }),
    h("span", null, `${w} / ${Number(r.planning_capacity_w).toLocaleString()} W（${r.partial ? t("rack_power.at_least", { pct }) : `${pct}%`}）`),
    h(NTag, { size: "tiny", bordered: false, type: tag }, () => t(`power_status.${r.status}`)),
  ]);
}
const feedCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("cols.panel"), key: "panel_id", render: (r: any) => panels.value.find((p) => p.id === r.panel_id)?.name ?? r.panel_id },
  { title: t("physical.feed_spec"), key: "voltage_v", width: 170,
    render: (r: any) => `${r.voltage_v} V / ${r.amperage_a} A / ${t(`physical.phase_${r.phase}`)} ${String(r.supply_type ?? "").toUpperCase()}` },
  { title: t("nav.racks"), key: "rack_id", width: 120, render: (r: any) => rackName(r.rack_id) },
  { title: t("physical.feed_load"), key: "utilization_pct", minWidth: 300, render: loadCell,
    exportValue: (r: any) => r.utilization_pct } as any,
  { title: t("physical.feed_devices"), key: "devices", width: 90, render: (r: any) => r.devices ?? 0 },
  { title: t("sections.description"), key: "description", minWidth: 140, ellipsis: { tooltip: true }, render: (r: any) => r.description ?? "—" },
  powerActionsCol("feed"),
]));
const outletCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "label", render: (r: any) => r.label ?? r.name ?? "—" },
  { title: t("cols.feed"), key: "feed_id", render: (r: any) => feeds.value.find((f) => f.id === r.feed_id)?.name ?? "—" },
  { title: t("nav.racks"), key: "rack_id", width: 140, render: (r: any) => rackName(r.rack_id) },
  // 接在這個插座的電源（裝置電源埠；一個插座只接一顆）
  { title: t("physical.outlet_used_by"), key: "occupied_by", minWidth: 160, ellipsis: { tooltip: true },
    render: (r: any) => (r.occupied_by?.length ? r.occupied_by.join("、") : "—"),
    exportValue: (r: any) => (r.occupied_by ?? []).join(", ") } as any,
  { title: t("sections.description"), key: "description", minWidth: 160, ellipsis: { tooltip: true }, render: (r: any) => r.description ?? "—" },
  powerActionsCol("outlet"),
]));
// 電力三表的欄位顯示偏好（ColumnPicker + useColumnPrefs）+ 即時篩選。actions 欄永遠保留。
function usePowerPrefs(name: string, cols: typeof panelCols, rows: typeof panels) {
  const allKeys = cols.value.filter((c: any) => c.key && c.key !== "actions").map((c: any) => String(c.key));
  const { visibleKeys, setVisible, reset, order, setOrder, orderColumns } =
    useColumnPrefs(`power_${name}`, allKeys, allKeys);
  const items = computed(() => cols.value
    .filter((c: any) => c.key && c.key !== "actions")
    .map((c: any) => ({ key: String(c.key), label: typeof c.title === "string" ? c.title : String(c.key) })));
  const visibleCols = computed<DataTableColumns<any>>(() =>
    orderColumns(cols.value.filter((c: any) => c.key === "actions" || visibleKeys.value.includes(String(c.key)))));
  const { query, filtered } = useTableQuickFilter(rows);
  return reactive({ visibleKeys, setVisible, reset, order, setOrder, items, visibleCols, query, filtered });
}
const panelP = usePowerPrefs("panels", panelCols, panels);
const feedP = usePowerPrefs("feeds", feedCols, feeds);
const outletP = usePowerPrefs("outlets", outletCols, outlets);


// ── 新增纜線 ──
const showCable = ref(false);
const savingCable = ref(false);
const cableForm = ref<{ aDevice: string | null; aPort: string | null; bDevice: string | null; bPort: string | null }>(
  { aDevice: null, aPort: null, bDevice: null, bPort: null });
const cableNewAttrs = ref<CableAttrs>(blankCableAttrs());
// 兩端裝置：遠端搜尋（以前只載前 500 台，之後的選不到）
const devA = useDeviceOptions();
const devB = useDeviceOptions();
const aPorts = ref<DevicePort[]>([]);
const bPorts = ref<DevicePort[]>([]);
const portOpts = (ps: DevicePort[]) => ps.map((p) => ({ label: `${p.name}${p.type ? ` (${p.type})` : ""}`, value: p.id }));
watch(() => cableForm.value.aDevice, async (d) => { cableForm.value.aPort = null; aPorts.value = d ? await Physical.ports(d) : []; });
watch(() => cableForm.value.bDevice, async (d) => { cableForm.value.bPort = null; bPorts.value = d ? await Physical.ports(d) : []; });
async function openCable() {
  cableForm.value = { aDevice: null, aPort: null, bDevice: null, bPort: null };
  cableNewAttrs.value = blankCableAttrs();
  aPorts.value = []; bPorts.value = [];
  await Promise.all([devA.search(), devB.search()]);
  showCable.value = true;
}
async function saveCable() {
  const f = cableForm.value;
  if (!f.aPort || !f.bPort) { msg.warning(t("physical.pick_both_ports")); return; }
  if (f.aPort === f.bPort) { msg.warning(t("physical.same_port")); return; }
  savingCable.value = true;
  try {
    const a = cableNewAttrs.value;
    await Physical.connectPorts(f.aPort, f.bPort, {
      type: a.type ?? undefined, label: a.label.trim() || undefined, color: a.color.trim() || undefined,
      length_m: a.length_m ?? undefined, status: a.status, description: a.description.trim() || undefined,
    });
    msg.success(t("common.ok")); showCable.value = false; await refresh();
  } catch (e: any) { msg.error(apiErrMsg(e) || t("errors.network")); } finally { savingCable.value = false; }
}

// ── 編輯 / 刪除纜線 ──
const showCableEdit = ref(false);
const savingCableEdit = ref(false);
const editingCableId = ref<string | null>(null);
const cableEditForm = ref<CableAttrs>(blankCableAttrs());
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
interface PowerForm {
  name: string; location_id: string | null; panel_id: string | null; feed_id: string | null; rack_id: string | null;
  description: string; voltage_v: number; amperage_a: number; phase: "single" | "three"; supply_type: "ac" | "dc";
  planning_capacity_w: number | null; warning_pct: number; capacity_basis: string;
}
function blankPower(): PowerForm {
  return { name: "", location_id: null, panel_id: panels.value[0]?.id ?? null, feed_id: feeds.value[0]?.id ?? null,
           rack_id: null, description: "", voltage_v: 220, amperage_a: 20, phase: "single", supply_type: "ac",
           planning_capacity_w: null, warning_pct: 80, capacity_basis: "" };
}
const powerForm = ref<PowerForm>(blankPower());
const phaseOpts = computed(() => (["single", "three"] as const).map((v) => ({ label: t(`physical.phase_${v}`), value: v })));
const supplyOpts = [{ label: "AC", value: "ac" }, { label: "DC", value: "dc" }];
const panelOpts = computed(() => panels.value.map((p) => ({ label: p.name, value: p.id })));
const feedOpts = computed(() => feeds.value.map((f) => ({ label: f.name, value: f.id })));
const powerTitle = computed(() => powerKind.value === "panel" ? t("physical.panels") : powerKind.value === "feed" ? t("physical.feeds") : t("physical.outlets"));
const powerEditId = ref<string | null>(null);
async function openPower(kind: "panel" | "feed" | "outlet") {
  powerKind.value = kind;
  powerEditId.value = null;
  powerForm.value = blankPower();
  if (kind === "panel") await ensureLocations();
  else await ensureRacks();
  showPower.value = true;
}
async function openPowerEdit(kind: "panel" | "feed" | "outlet", r: any) {
  powerKind.value = kind;
  powerEditId.value = r.id;
  powerForm.value = {
    ...blankPower(),
    name: r.name ?? r.label ?? "",
    location_id: r.location_id ?? null,
    panel_id: r.panel_id ?? null,
    feed_id: r.feed_id ?? null,
    rack_id: r.rack_id ?? null,
    description: r.description ?? "",
    voltage_v: r.voltage_v ?? 220, amperage_a: r.amperage_a ?? 20,
    phase: r.phase ?? "single", supply_type: r.supply_type ?? "ac",
    planning_capacity_w: r.planning_capacity_w ?? null, warning_pct: r.warning_pct ?? 80,
    capacity_basis: r.capacity_basis ?? "",
  };
  if (kind === "panel") await ensureLocations();
  else await ensureRacks();
  showPower.value = true;
}
async function savePower() {
  const f = powerForm.value;
  if (!f.name.trim()) { msg.warning(t("physical.need_name")); return; }
  if (powerKind.value === "feed" && !f.panel_id) { msg.warning(t("physical.need_panel")); return; }
  savingPower.value = true;
  const id = powerEditId.value;
  try {
    const description = f.description.trim() || null;
    if (powerKind.value === "panel") {
      const p = { name: f.name.trim(), location_id: f.location_id, description };
      if (id) await Physical.updatePanel(id, p);
      else await Physical.createPanel(p);
    } else if (powerKind.value === "feed") {
      const p = { panel_id: f.panel_id!, name: f.name.trim(), voltage_v: f.voltage_v, amperage_a: f.amperage_a,
                  phase: f.phase, supply_type: f.supply_type, rack_id: f.rack_id, description,
                  planning_capacity_w: f.planning_capacity_w, warning_pct: f.warning_pct || 80,
                  capacity_basis: f.capacity_basis.trim() || null };
      if (id) await Physical.updateFeed(id, p);
      else await Physical.createFeed(p);
    } else {
      const p = { feed_id: f.feed_id, label: f.name.trim(), rack_id: f.rack_id, description };
      if (id) await Physical.updateOutlet(id, p);
      else await Physical.createOutlet(p);
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
const failure = ref<InstanceType<typeof PowerFailureDrawer> | null>(null);
function powerActionsCol(kind: "panel" | "feed" | "outlet") {
  return {
    title: t("common.actions"), key: "actions", width: kind === "feed" ? 128 : 96,
    render: (r: any) => h(NSpace, { size: 4 }, () => [
      h(NButton, { size: "tiny", quaternary: true, type: "success", disabled: !canEdit.value, onClick: () => openPowerEdit(kind, r) },
        { icon: () => h(NIcon, null, () => h(EditIcon)) }),
      h(NPopconfirm, { onPositiveClick: () => delPower(kind, r) }, {
        trigger: () => h(NButton, { size: "tiny", quaternary: true, type: "error", disabled: !canEdit.value },
          { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
        default: () => t("common.confirm_delete"),
      }),
      ...(kind === "feed" ? [h(NButton, { size: "tiny", quaternary: true, type: "warning", title: t("power_failure.simulate"),
                                          "data-testid": `feed-simulate-${r.name}`, onClick: () => failure.value?.open([r.id]) },
        { icon: () => h(NIcon, null, () => h(PowerFailureIcon)) })] : []),
    ]),
  };
}

function ensureCablingFilters() {
  void ensureDevices(); void ensureLocations(); void ensureCustomers(); void ensureRacks();
  readFilterQuery();
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
    <!-- 這列只給佈線：電力的每個頁籤、VPN 的清單元件都有自己的重新整理（以前電力頁出現兩顆；
         VPN 頁這顆按了沒反應，refresh() 沒有 VPN 分支） -->
    <n-space v-if="mode === 'cabling'" align="center" style="margin-bottom: 12px">
      <template v-if="mode === 'cabling'">
        <n-input v-model:value="cableFilterText" clearable style="width: 200px"
                 :placeholder="t('physical.cable_filter_ph')" />
        <n-select v-model:value="cableFilterDevice" :options="deviceOpts" filterable clearable
                  style="width: 200px" :placeholder="t('physical.cable_filter_device')" />
        <n-select v-model:value="cableFilterCustomer" :options="customerOptions" filterable clearable
                  style="width: 170px" :placeholder="t('physical.cable_filter_customer')" />
        <n-select v-model:value="cableFilterLocation" :options="locationOpts" filterable clearable
                  style="width: 170px" :placeholder="t('physical.cable_filter_location')" />
        <n-select v-model:value="cableFilterRack" :options="rackOpts" filterable clearable
                  style="width: 150px" :placeholder="t('physical.cable_filter_rack')" data-testid="cable-filter-rack" />
        <n-select v-model:value="cableFilterStatus" :options="cableStatusOpts" clearable
                  style="width: 130px" :placeholder="t('common.status')" data-testid="cable-filter-status" />
        <n-checkbox v-model:checked="cableFilterNoLabel" data-testid="cable-filter-nolabel">{{ t("physical.cable_filter_nolabel") }}</n-checkbox>
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
                    @update:visible="setCableVisible" @reset="resetCableVisible"
                    :order="cableOrder" @update:order="setCableOrder" />
      <ExportButton v-if="mode === 'cabling'" :columns="orderCableCols(cablePickerItems)" :rows="filteredCables"
                    filename="cables" :title="t('nav.cabling')" />
      <PhysicalCheckDrawer v-if="isAdmin" />
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
          <PhysicalCheckDrawer v-if="isAdmin" />
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openPower('panel')">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("common.add") }}
          </n-button>
          <ColumnPicker :all="panelP.items" :visible="panelP.visibleKeys"
                        @update:visible="panelP.setVisible" @reset="panelP.reset"
                        :order="panelP.order" @update:order="panelP.setOrder" />
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
                        @update:visible="feedP.setVisible" @reset="feedP.reset"
                        :order="feedP.order" @update:order="feedP.setOrder" />
          <ExportButton :columns="feedP.visibleCols" :rows="feedP.filtered" filename="power-feeds" :title="t('physical.feeds')" />
          <n-button type="warning" secondary :disabled="!feeds.length" data-testid="power-failure-open" @click="failure?.open([])">
            <template #icon><n-icon><PowerFailureIcon /></n-icon></template>{{ t("power_failure.simulate") }}
          </n-button>
        </n-space>
        <n-data-table :columns="feedP.visibleCols" :data="feedP.filtered" :loading="loading" :bordered="false" :pagination="pg"
                      :scroll-x="1300" data-testid="power-feeds-table" />
        <PowerFailureDrawer ref="failure" />
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
                        @update:visible="outletP.setVisible" @reset="outletP.reset"
                        :order="outletP.order" @update:order="outletP.setOrder" />
          <ExportButton :columns="outletP.visibleCols" :rows="outletP.filtered" filename="power-outlets" :title="t('physical.outlets')" />
        </n-space>
        <n-data-table :columns="outletP.visibleCols" :data="outletP.filtered" :loading="loading" :bordered="false" :pagination="pg" />
      </n-tab-pane>
    </n-tabs>

    <VpnTunnelTable v-else />
    <CableTraceDrawer v-model:show="showTrace" :cable="traceCable" />

    <!-- 新增纜線 -->
    <n-modal v-model:show="showCable" preset="card" style="max-width:520px">
      <template #header>
        <n-space align="center" :size="8" :wrap-item="false">
          <n-icon :size="18"><PlusIcon /></n-icon>{{ t('physical.add_cable') }}
        </n-space>
      </template>
      <n-form label-placement="top">
        <n-form-item :label="t('physical.a_device')">
          <n-select v-model:value="cableForm.aDevice" :options="devA.options.value" filterable clearable remote
                    :loading="devA.loading.value" @search="devA.onSearch" :placeholder="t('physical.pick_device')"
                    data-testid="cable-a-device" />
        </n-form-item>
        <n-form-item :label="t('physical.a_port')">
          <n-select v-model:value="cableForm.aPort" :options="portOpts(aPorts)" :disabled="!cableForm.aDevice" filterable clearable
                    :placeholder="t('physical.pick_port')" data-testid="cable-a-port" />
        </n-form-item>
        <n-form-item :label="t('physical.b_device')">
          <n-select v-model:value="cableForm.bDevice" :options="devB.options.value" filterable clearable remote
                    :loading="devB.loading.value" @search="devB.onSearch" :placeholder="t('physical.pick_device')"
                    data-testid="cable-b-device" />
        </n-form-item>
        <n-form-item :label="t('physical.b_port')">
          <n-select v-model:value="cableForm.bPort" :options="portOpts(bPorts)" :disabled="!cableForm.bDevice" filterable clearable
                    :placeholder="t('physical.pick_port')" data-testid="cable-b-port" />
        </n-form-item>
        <CableAttrFields v-model="cableNewAttrs" />
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCable = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="savingCable" @click="saveCable"><template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}</n-button>
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
        <CableAttrFields v-model="cableEditForm" />
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCableEdit = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="savingCableEdit" @click="saveEditCable"><template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}</n-button>
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
        <template v-if="powerKind === 'feed'">
          <n-space :wrap-item="false" :size="12">
            <n-form-item :label="t('physical.voltage_v')" style="flex:1">
              <n-input-number v-model:value="powerForm.voltage_v" :min="12" :max="600" data-testid="feed-voltage" />
            </n-form-item>
            <n-form-item :label="t('physical.amperage_a')" style="flex:1">
              <n-input-number v-model:value="powerForm.amperage_a" :min="1" :max="400" data-testid="feed-amperage" />
            </n-form-item>
          </n-space>
          <n-space :wrap-item="false" :size="12">
            <n-form-item :label="t('physical.phase')" style="flex:1">
              <n-select v-model:value="powerForm.phase" :options="phaseOpts" data-testid="feed-phase" />
            </n-form-item>
            <n-form-item :label="t('physical.supply_type')" style="flex:1">
              <n-select v-model:value="powerForm.supply_type" :options="supplyOpts" />
            </n-form-item>
          </n-space>
          <n-space :wrap-item="false" :size="12">
            <n-form-item :label="t('physical.planning_capacity')" style="flex:1">
              <n-input-number v-model:value="powerForm.planning_capacity_w" :min="0" :max="10000000" :precision="2" clearable
                              :placeholder="t('power_profile.unknown')" data-testid="feed-capacity">
                <template #suffix>W</template>
              </n-input-number>
            </n-form-item>
            <n-form-item :label="t('physical.warning_pct')" style="flex:1">
              <n-input-number v-model:value="powerForm.warning_pct" :min="1" :max="100" data-testid="feed-warning">
                <template #suffix>%</template>
              </n-input-number>
            </n-form-item>
          </n-space>
          <n-form-item :label="t('physical.capacity_basis')" :show-feedback="false" style="margin-bottom: 4px">
            <n-input v-model:value="powerForm.capacity_basis" maxlength="128" clearable
                     :placeholder="t('physical.capacity_basis_ph')" data-testid="feed-capacity-basis" />
          </n-form-item>
          <div class="hint" style="margin:-8px 0 12px">{{ t("physical.feed_capacity_hint", { va: (powerForm.voltage_v * powerForm.amperage_a).toLocaleString() }) }}</div>
        </template>
        <n-form-item v-if="powerKind !== 'panel'" :label="t('nav.racks')">
          <n-select v-model:value="powerForm.rack_id" :options="rackOpts" filterable clearable data-testid="power-rack" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="powerForm.description" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showPower = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="savingPower" @click="savePower"><template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.hint { font-size: 12px; opacity: .7; }
</style>
