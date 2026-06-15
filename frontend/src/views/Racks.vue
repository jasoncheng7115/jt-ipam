<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NSelect,
  NSpin,
  NButton,
  NPopconfirm,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NTooltip,
  NRadioGroup,
  NRadioButton,
  NButtonGroup,
  NDropdown,
  useMessage,
  type DataTableColumns,
  type DataTableRowKey,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { RacksIcon, DeleteIcon, PlusIcon, EditIcon, SaveIcon, CancelIcon, LocationsIcon, PinIcon, ExportIcon } from "@/icons";
import { exportTable, type ExportColumn } from "@/utils/tableExport";
import { exportRacksSvg, exportRacksPng, exportRacksDrawio, type RackNameAlign } from "@/utils/rackGraphicsExport";
import { getRackNameAlign } from "@/api/basic";
import { usePinned } from "@/composables/usePinned";
import { useRouter } from "vue-router";
import { apiClient } from "@/api/client";
import RackDiagram from "@/components/RackDiagram.vue";
import { RACK_DEVICE_TYPES, rackTypeColor } from "@/utils/rackColors";
import RackFloorPlan from "@/components/RackFloorPlan.vue";
import { getRackDiagram, type RackDiagram as RD } from "@/api/racks";
import { bulkDeleteRacks, listLocations, listDevices, updateDevice, type Location, type Device } from "@/api/basic";
import { useAuthStore } from "@/stores/auth";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";

interface Rack {
  id: string;
  name: string;
  u_height: number;
  width_mm?: number | null;
  depth_mm?: number | null;
  location_id: string | null;
  location_name?: string | null;
  description: string | null;
  seq?: number | null;
  device_count?: number;
  numbering?: "top-down" | "bottom-up";
  face?: "front" | "rear";
  pos_x?: number | null;
  pos_y?: number | null;
}

const { t } = useI18n();
const msg = useMessage();
const auth = useAuthStore();
const router = useRouter();
function goRooms() { router.push({ name: "locations" }); }
// 釘選機房（存 localStorage，每瀏覽器）：進機櫃頁時預設先看釘選的機房
const PINNED_ROOM_KEY = "jt_pinned_room";
const pinnedRoom = ref<string | null>(localStorage.getItem(PINNED_ROOM_KEY));
function togglePinRoom() {
  if (!roomId.value) return;
  if (pinnedRoom.value === roomId.value) {
    pinnedRoom.value = null; localStorage.removeItem(PINNED_ROOM_KEY);
  } else {
    pinnedRoom.value = roomId.value; localStorage.setItem(PINNED_ROOM_KEY, roomId.value);
  }
}
const isAdmin = computed(() => !!auth.me?.is_admin);
// 合併單卡模式：機房內所有機櫃排進同一張卡（不分卡片），存 localStorage（每瀏覽器）
const MERGED_VIEW_KEY = "jt_rack_merged";
const mergedView = ref<boolean>(localStorage.getItem(MERGED_VIEW_KEY) === "1");
watch(mergedView, (v) => localStorage.setItem(MERGED_VIEW_KEY, v ? "1" : "0"));
// 分段切換：獨立卡片 / 合併卡片 → 對應 mergedView 布林（合併 = true）
const rackViewMode = computed<"separate" | "merged">({
  get: () => (mergedView.value ? "merged" : "separate"),
  set: (v) => { mergedView.value = v === "merged"; },
});
// 合併卡共用的正/背面切換（控制卡內所有機櫃）
const mergedFace = ref<"front" | "rear">("front");
const mergedHasRear = computed(() =>
  roomDiagrams.value.some((d: any) => (d.devices || []).some((x: any) => x.rack_face === "rear")));
const roomFocus = ref<RD | null>(null);   // 在平面圖上點選的機櫃 → 顯示其 U 位
async function onRoomRackSelect(rackId: string) {
  try { roomFocus.value = await getRackDiagram(rackId); }
  catch { msg.error(t("errors.network")); }
}
const rows = ref<Rack[]>([]);
const loading = ref(false);
const selected = ref<string | null>(null);
const diagram = ref<RD | null>(null);
const diagramLoading = ref(false);

// 機房（= location）：選一間機房可一次把該機房所有機櫃並排成一排
const locations = ref<Location[]>([]);
const roomId = ref<string | null>(null);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const pin = usePinned("racks");
const locPin = usePinned("locations");   // 在「機房」頁釘選的常用機房
const displayRows = computed(() => {
  // 上面選了機房/地點時，「所有機櫃」表也只顯示該機房的機櫃
  const base = roomId.value
    ? filteredRows.value.filter((r) => r.location_id === roomId.value)
    : filteredRows.value;
  return pin.sortPinnedFirst(base);
});
const roomDiagrams = ref<RD[]>([]);
// 同排機櫃最高 U 數 → 傳給每個機櫃圖做「落地靠下對齊」
const maxRoomU = computed(() => roomDiagrams.value.reduce((m, d) => Math.max(m, d.u_height || 0), 0));
const roomLoading = ref(false);
const locationOptions = computed(() =>
  locations.value.map((l) => ({ label: l.name, value: l.id })));

async function loadRoom(locId: string) {
  roomLoading.value = true;
  try {
    // 並排順序：先依「編號 seq」（小的在左、未設定排最後），再依平面圖位置(pos_x→pos_y)，最後依名稱
    const racksHere = rows.value
      .filter((r) => r.location_id === locId)
      .sort((a, b) => {
        const as = a.seq ?? 9999, bs = b.seq ?? 9999;
        if (as !== bs) return as - bs;
        const ax = a.pos_x ?? 99, bx = b.pos_x ?? 99;
        if (ax !== bx) return ax - bx;
        const ay = a.pos_y ?? 99, by = b.pos_y ?? 99;
        if (ay !== by) return ay - by;
        return a.name.localeCompare(b.name);
      });
    roomDiagrams.value = (await Promise.all(
      racksHere.map((r) => getRackDiagram(r.id).catch(() => null)),
    )).filter((d): d is RD => d !== null);
  } catch {
    msg.error(t("errors.network"));
    roomDiagrams.value = [];
  } finally {
    roomLoading.value = false;
  }
}

watch(roomId, (v) => {
  roomFocus.value = null;
  if (v) { selected.value = null; void loadRoom(v); }
  else roomDiagrams.value = [];
});

const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

async function doBulkDelete() {
  if (!checkedKeys.value.length) return;
  bulkBusy.value = true;
  try {
    const res = await bulkDeleteRacks(checkedKeys.value.map(String));
    if (res.failed) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    checkedKeys.value = [];
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { bulkBusy.value = false; }
}

const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "racks",
  ["seq", "location_name", "name", "u_height", "dimensions", "device_count", "description"],
  ["seq", "location_name", "name", "u_height", "dimensions", "device_count", "description"],
);
const columnPickerItems = [
  { key: "seq", label: t("racks.seq") },
  { key: "location_name", label: t("nav.locations") },
  { key: "name", label: t("cols.name") },
  { key: "u_height", label: t("cols.u_height") },
  { key: "dimensions", label: t("racks.dimensions") },
  { key: "device_count", label: t("racks.device_count") },
  { key: "description", label: t("cols.description") },
];
function iconBtn(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, {
      size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); },
    }, { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allColumns = computed<DataTableColumns<Rack>>(() => [
  { type: "selection" },
  { title: t("racks.seq"), key: "seq", width: 80,
    render: (r) => (r as any).seq ?? "—",
    sorter: (a, b) => ((a as any).seq ?? 9999) - ((b as any).seq ?? 9999) },
  { title: t("nav.locations"), key: "location_name", width: 160,
    render: (r) => (r as any).location_name ?? "—",
    sorter: (a, b) => ((a as any).location_name ?? "").localeCompare((b as any).location_name ?? "") },
  { title: t("common.name"), key: "name", sorter: (a, b) => a.name.localeCompare(b.name) },
  { title: t("racks.u_height"), key: "u_height", width: 100, sorter: (a, b) => a.u_height - b.u_height },
  { title: t("racks.dimensions"), key: "dimensions", width: 140,
    render: (r) => (r.width_mm && r.depth_mm) ? `${r.width_mm} × ${r.depth_mm} mm` : "—" },
  { title: t("racks.device_count"), key: "device_count", width: 100,
    render: (r) => (r as any).device_count ?? 0,
    sorter: (a, b) => ((a as any).device_count ?? 0) - ((b as any).device_count ?? 0) },
  { title: t("common.description"), key: "description", render: (r) => r.description ?? "—",
    sorter: (a, b) => (a.description ?? "").localeCompare(b.description ?? "") },
  {
    title: t("common.actions"), key: "actions", width: 128, className: "col-actions",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      h(NButton, {
        size: "small", quaternary: true,
        type: pin.isPinned(r.id) ? "warning" : "default", title: t("common.pin"),
        onClick: (e: MouseEvent) => { e.stopPropagation(); pin.toggle(r.id); },
      }, { icon: () => h(NIcon, { color: pin.isPinned(r.id) ? "#f0a020" : undefined }, () => h(PinIcon)) }),
      iconBtn(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => removeRack(r) }, {
        trigger: () => iconBtn(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
const columns = computed<DataTableColumns<Rack>>(() =>
  allColumns.value.filter((c: any) =>
    c.type === "selection" || c.key === "actions" || visibleKeys.value.includes(c.key)),
);

// ── 新增 / 編輯 / 刪除機櫃 ──
const showEdit = ref(false);
const editing = ref<Rack | null>(null);
const form = ref({
  name: "", u_height: 42, location_id: null as string | null, description: "",
  seq: null as number | null,
  width_mm: null as number | null, depth_mm: null as number | null,
  numbering: "top-down" as "top-down" | "bottom-up", face: "front" as "front" | "rear",
});
function openCreate() {
  editing.value = null;
  form.value = { name: "", u_height: 42, location_id: roomId.value, description: "",
    seq: null, width_mm: null, depth_mm: null, numbering: "top-down", face: "front" };
  showEdit.value = true;
}
function openEdit(r: Rack) {
  editing.value = r;
  form.value = {
    name: r.name, u_height: r.u_height, location_id: r.location_id, description: r.description ?? "",
    seq: r.seq ?? null,
    width_mm: r.width_mm ?? null, depth_mm: r.depth_mm ?? null,
    numbering: r.numbering ?? "top-down", face: r.face ?? "front",
  };
  showEdit.value = true;
}
const numberingOpts = [
  { label: t("racks.numbering_top_down"), value: "top-down" },
  { label: t("racks.numbering_bottom_up"), value: "bottom-up" },
];
// 常見機櫃外寬 / 外深（mm）快捷
const WIDTH_PRESETS = [600, 800];
const DEPTH_PRESETS = [600, 800, 1000, 1100, 1200];

async function submitRack() {
  if (!form.value.name.trim()) { msg.error(t("common.name_required")); return; }
  const payload = {
    name: form.value.name.trim(),
    u_height: form.value.u_height,
    location_id: form.value.location_id ?? null,
    description: form.value.description.trim() || null,
    seq: form.value.seq ?? null,
    width_mm: form.value.width_mm ?? null,
    depth_mm: form.value.depth_mm ?? null,
    numbering: form.value.numbering,
    face: form.value.face,
  };
  try {
    if (editing.value) await apiClient.patch(`/api/v1/racks/${editing.value.id}`, payload);
    else await apiClient.post("/api/v1/racks", payload);
    showEdit.value = false;
    msg.success(t("common.ok"));
    await refresh();
    if (roomId.value) await loadRoom(roomId.value);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function removeRack(r: Rack) {
  try {
    await apiClient.delete(`/api/v1/racks/${r.id}`);
    msg.success(t("common.ok"));
    if (selected.value === r.id) selected.value = null;
    await refresh();
    if (roomId.value) await loadRoom(roomId.value);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function refresh() {
  loading.value = true;
  try {
    const { data } = await apiClient.get<{ items: Rack[] }>("/api/v1/racks", {
      params: { page: 1, page_size: 200 },
    });
    rows.value = data.items;
    // 只有在「沒有選機房（整排檢視）」時才預設第一個機櫃，否則會蓋掉機房自動選
    if (!selected.value && !roomId.value && rows.value.length) {
      selected.value = rows.value[0].id;
    }
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function loadDiagram(id: string) {
  diagramLoading.value = true;
  try {
    diagram.value = await getRackDiagram(id);
  } catch {
    msg.error(t("errors.network"));
    diagram.value = null;
  } finally {
    diagramLoading.value = false;
  }
}

watch(selected, (v) => {
  if (v) { roomId.value = null; void loadDiagram(v); }
  else diagram.value = null;
});

// 合併卡圖形匯出沿用全域「機櫃名稱對齊」偏好（與 RackDiagram 一致）
const mergedNameAlign = ref<RackNameAlign>("left");

onMounted(async () => {
  void getRackNameAlign().then((a) => { mergedNameAlign.value = a as RackNameAlign; });
  // 先 refresh() 把所有機櫃載進 rows（loadRoom 依賴它過濾該機房的機櫃，
  // 否則「機櫃示意圖」會誤判此機房尚無機櫃）。refresh 可能先預設第一個機櫃。
  await refresh();
  try {
    const r = await listLocations();
    locations.value = r.items;
    // 決定預設機房（優先 pinnedRoom，其次機房頁釘選的第一個）；設定後 watch(roomId)
    // 會清掉單櫃選取並用「整排機櫃」檢視覆蓋 refresh 的單櫃預設。
    let def: string | null =
      (pinnedRoom.value && r.items.some((l) => l.id === pinnedRoom.value)) ? pinnedRoom.value : null;
    if (!def) def = locPin.ids.value.find((id) => r.items.some((l) => l.id === id)) ?? null;
    if (def) roomId.value = def;
  } catch { /* silent */ }
});

// ── 點空 U 位 → 挑裝置放入（任一機櫃圖都可點，event 會帶 rack_id）──
const showDevicePick = ref(false);
const pickEmptyU = ref<number | null>(null);
const pickRackId = ref<string | null>(null);
const pickDeviceId = ref<string | null>(null);
const pickDeviceSize = ref(1);
const pickSide = ref<"full" | "left" | "right">("full");
const pickSideOpts = computed(() => [
  { label: t("devices.rack_side_full"), value: "full" },
  { label: t("devices.rack_side_left"), value: "left" },
  { label: t("devices.rack_side_right"), value: "right" },
]);
const pickableDevices = ref<Device[]>([]);
const pickBusy = ref(false);
const pickDeviceOpts = computed(() => pickableDevices.value.map((d) => ({
  label: d.ip ? `${d.name} — ${d.ip}` : d.name, value: d.id,
})));
async function onPickEmpty(u: number, rackId: string, side?: "left" | "right") {
  pickEmptyU.value = u;
  pickRackId.value = rackId;
  pickDeviceId.value = null;
  pickDeviceSize.value = 1;
  pickSide.value = side ?? "full";
  showDevicePick.value = true;
  try {
    const r = await listDevices();
    // 優先列「尚未放進任何機櫃」的裝置；其餘也會列出（選了會搬過來）
    pickableDevices.value = r.items
      .filter((d) => !(d as any).rack_id)
      .concat(r.items.filter((d) => (d as any).rack_id));
  } catch { msg.error(t("errors.network")); }
}
// 把某機櫃的圖在所有出現處（選定 / 釘選機房 / 所屬機櫃清單）都重新整理
async function refreshRackEverywhere(rackId: string) {
  const fresh = await getRackDiagram(rackId).catch(() => null);
  if (!fresh) return;
  if (diagram.value?.rack_id === rackId) diagram.value = fresh;
  if (roomFocus.value?.rack_id === rackId) roomFocus.value = fresh;
  const idx = roomDiagrams.value.findIndex((d) => d.rack_id === rackId);
  if (idx >= 0) roomDiagrams.value[idx] = fresh;
}
async function confirmPickDevice() {
  if (!pickDeviceId.value || !pickRackId.value || pickEmptyU.value == null) return;
  pickBusy.value = true;
  try {
    await updateDevice(pickDeviceId.value, {
      rack_id: pickRackId.value, u_position: pickEmptyU.value,
      u_size: Math.max(1, pickDeviceSize.value || 1),
      rack_side: pickSide.value,
    } as any);
    showDevicePick.value = false;
    msg.success(t("common.ok"));
    await refreshRackEverywhere(pickRackId.value);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { pickBusy.value = false; }
}

// 合併卡匯出：圖形（SVG/PNG/draw.io，整個機房多機櫃並排）+ 純資料格式
const mergedExportOptions = [
  { label: "SVG", key: "svg" },
  { label: "PNG", key: "png" },
  { label: "draw.io", key: "drawio" },
  { type: "divider", key: "d1" },
  { label: "CSV", key: "csv" },
  { label: "Excel (.xlsx)", key: "xlsx" },
  { label: "OpenDocument (.ods)", key: "ods" },
  { label: "Markdown (.md)", key: "md" },
  { label: "純文字 (.txt)", key: "txt" },
];
function onMergedExport(key: string) {
  if (["svg", "png", "drawio"].includes(key)) {
    const diags = roomDiagrams.value as any[];
    if (!diags.length) return;
    const fname = "room-racks";
    const al = maxRoomU.value;
    if (key === "svg") exportRacksSvg(diags, al, mergedNameAlign.value, fname);
    else if (key === "png") exportRacksPng(diags, al, mergedNameAlign.value, fname);
    else exportRacksDrawio(diags, al, mergedNameAlign.value, fname);
    return;
  }
  if (!["csv", "xlsx", "ods", "md", "txt"].includes(key)) return;
  const fmt = key as "csv" | "xlsx" | "ods" | "md" | "txt";
  const cols: ExportColumn[] = [
    { key: "rack", label: t("nav.racks") },
    { key: "name", label: t("cols.name") },
    { key: "type", label: t("cols.type") },
    { key: "u_position", label: "U" },
    { key: "u_size", label: "U Size" },
    { key: "rack_face", label: t("racks.face") },
    { key: "primary_ip", label: "IP" },
  ];
  const rows = roomDiagrams.value.flatMap((d: any) =>
    (d.devices || []).map((dev: any) => ({
      rack: d.name,
      name: dev.name,
      type: dev.type,
      u_position: dev.u_position,
      u_size: dev.u_size,
      rack_face: dev.rack_face ?? "front",
      primary_ip: dev.primary_ip ?? "",
    })));
  exportTable(fmt, "room-racks", cols, rows, "Racks");
}
</script>

<template>
  <n-space vertical :size="16">
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="22"><RacksIcon /></n-icon>
          <span>{{ t("racks.page_title") }}</span>
        </n-space>
      </template>
      <n-space align="center">
        <n-select
          v-model:value="roomId"
          :options="locationOptions"
          :placeholder="t('racks.room_placeholder')"
          style="width: 260px"
          clearable
        />
        <n-button v-if="roomId" size="small" quaternary
                  :type="pinnedRoom === roomId ? 'warning' : 'default'"
                  @click="togglePinRoom"
                  :title="pinnedRoom === roomId ? t('racks.unpin_room') : t('racks.pin_room')">
          {{ pinnedRoom === roomId ? '★' : '☆' }}
        </n-button>
        <span style="opacity: .4">{{ t("racks.or") }}</span>
        <n-select
          v-model:value="selected"
          :options="rows.map((r) => ({ label: `${r.name} (${r.u_height}U)`, value: r.id }))"
          :placeholder="t('racks.select_placeholder')"
          style="width: 280px"
          clearable
        />
        <n-button quaternary size="small" @click="goRooms" :title="t('racks.manage_rooms_hint')">
          <template #icon><n-icon><LocationsIcon /></n-icon></template>
          {{ t("racks.manage_rooms") }}
        </n-button>
        <n-space v-if="roomId" align="center" :size="6" :wrap-item="false" style="margin-left:8px">
          <span style="font-size:13px; opacity:.75">{{ t("racks.view_mode") }}</span>
          <n-radio-group v-model:value="rackViewMode" size="small">
            <n-radio-button value="separate">{{ t("racks.view_separate") }}</n-radio-button>
            <n-radio-button value="merged">{{ t("racks.view_merged") }}</n-radio-button>
          </n-radio-group>
        </n-space>
      </n-space>
    </n-card>

    <!-- 機房模式：平面圖 + 一整排機櫃並排 -->
    <template v-if="roomId">
      <n-card style="margin-bottom: 16px">
        <rack-floor-plan :location-id="roomId" :can-edit="isAdmin" @select="onRoomRackSelect" />
      </n-card>

      <!-- 在平面圖上點選的機櫃 → 顯示其 U 位 -->
      <n-card v-if="roomFocus" style="margin-bottom: 16px" :bordered="false" content-style="padding:0">
        <n-space justify="end" style="margin-bottom: 6px">
          <n-button size="tiny" quaternary @click="roomFocus = null">
            {{ t("common.cancel") }}
          </n-button>
        </n-space>
        <rack-diagram :diagram="roomFocus" :editable="isAdmin" @pick-empty="onPickEmpty" />
      </n-card>

      <!-- 點選聚焦時隱藏整排總覽，避免同一機櫃顯示兩次 -->
      <n-spin v-if="!roomFocus" :show="roomLoading">
        <template v-if="roomDiagrams.length">
          <!-- 合併單卡：所有機櫃排進同一張卡（去各櫃外框，加小標題） -->
          <n-card v-if="mergedView" :title="t('racks.merged_title')">
            <template #header-extra>
              <div style="display:flex; align-items:center; gap:8px">
                <n-button-group size="tiny">
                  <n-button :type="mergedFace === 'front' ? 'primary' : 'default'" @click="mergedFace = 'front'">
                    {{ t("racks.face_front") }}
                  </n-button>
                  <n-button :type="mergedFace === 'rear' ? 'primary' : 'default'" @click="mergedFace = 'rear'">
                    {{ t("racks.face_rear") }}<span v-if="mergedHasRear" style="margin-left:3px">•</span>
                  </n-button>
                </n-button-group>
                <n-dropdown trigger="click" :options="mergedExportOptions" @select="onMergedExport">
                  <n-button size="tiny">
                    <template #icon><n-icon><ExportIcon /></n-icon></template>
                    {{ t("common.export") }}
                  </n-button>
                </n-dropdown>
              </div>
            </template>
            <div class="rack-row">
              <div v-for="d in roomDiagrams" :key="d.rack_id" class="merged-rack">
                <div class="merged-rack__name">
                  {{ d.name }}<span class="merged-rack__u">{{ d.u_height }}U</span>
                </div>
                <rack-diagram :diagram="d" :show-legend="false" :editable="isAdmin"
                              :floor-align-to="maxRoomU" :face="mergedFace" :controls="false"
                              bare @pick-empty="onPickEmpty" />
              </div>
            </div>
            <div class="rack-legend-shared">
              <span v-for="ty in RACK_DEVICE_TYPES" :key="ty" class="legend-item"
                    :style="{ background: rackTypeColor(ty) }">{{ ty }}</span>
            </div>
          </n-card>
          <!-- 各自一張卡片（預設） -->
          <template v-else>
            <div class="rack-row">
              <rack-diagram v-for="d in roomDiagrams" :key="d.rack_id" :diagram="d"
                            :show-legend="false" :editable="isAdmin" :floor-align-to="maxRoomU"
                            @pick-empty="onPickEmpty" />
            </div>
            <!-- 整排機櫃共用一個圖例（不用每櫃都重複） -->
            <div class="rack-legend-shared">
              <span v-for="ty in RACK_DEVICE_TYPES" :key="ty" class="legend-item"
                    :style="{ background: rackTypeColor(ty) }">{{ ty }}</span>
            </div>
          </template>
        </template>
        <n-card v-else-if="!roomLoading" :title="t('racks.diagram_title')">
          <p style="opacity: 0.7">{{ t("racks.room_empty") }}</p>
        </n-card>
      </n-spin>
    </template>

    <!-- 單一機櫃模式 -->
    <n-spin v-else :show="diagramLoading">
      <rack-diagram v-if="diagram" :diagram="diagram" :editable="isAdmin" @pick-empty="onPickEmpty" />
      <n-card v-else-if="!selected" :title="t('racks.diagram_title')">
        <p style="opacity: 0.7">{{ t("racks.diagram_empty") }}</p>
      </n-card>
    </n-spin>

    <n-card :title="t('racks.all_title')">
      <n-space style="margin-bottom: 8px" align="center">
        <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 180px" />
        <n-button type="primary" @click="openCreate" :disabled="!isAdmin"
                  :title="isAdmin ? undefined : t('errors.admin_required')">
          <template #icon><n-icon><PlusIcon /></n-icon></template>
          {{ t("racks.add") }}
        </n-button>
        <ColumnPicker :all="columnPickerItems" :visible="visibleKeys"
                      @update:visible="setVisible" @reset="reset" />
        <ExportButton :columns="columns" :rows="rows" filename="racks" :title="t('nav.racks')" />
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
        :data="displayRows"
        :loading="loading"
        :pagination="pg"
        :bordered="false"
        :row-key="(row: Rack) => row.id"
        :checked-row-keys="checkedKeys"
        @update:checked-row-keys="(keys: DataTableRowKey[]) => checkedKeys = keys"
        :row-props="(row: Rack) => ({
          style: 'cursor: pointer',
          onClick: (e: MouseEvent) => {
            const target = e.target as HTMLElement;
            if (target.closest('.n-checkbox')) return;
            selected = row.id;
          },
        })"
      />
    </n-card>

    <n-modal v-model:show="showEdit" preset="card" style="width: 460px"
             :title="editing ? t('common.edit') : t('racks.add')">
      <n-form label-placement="left" label-width="90">
        <n-form-item :label="t('common.name')" required>
          <n-input v-model:value="form.name" />
        </n-form-item>
        <n-form-item :label="t('racks.u_height')">
          <n-input-number v-model:value="form.u_height" :min="1" :max="99" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('racks.seq')">
          <n-input-number v-model:value="form.seq" :min="0" :max="9999" clearable
                          :placeholder="t('racks.seq_ph')" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('racks.width_mm')">
          <div class="dim-field">
            <n-input-number v-model:value="form.width_mm" :min="100" :max="2000" :step="50"
                            clearable placeholder="600" style="width: 100%">
              <template #suffix>mm</template>
            </n-input-number>
            <div class="preset-chips">
              <span class="preset-chips__label">{{ t("racks.common") }}</span>
              <button v-for="w in WIDTH_PRESETS" :key="w" type="button"
                      class="preset-chip" :class="{ 'preset-chip--on': form.width_mm === w }"
                      @click="form.width_mm = w">{{ w }}</button>
            </div>
          </div>
        </n-form-item>
        <n-form-item :label="t('racks.depth_mm')">
          <div class="dim-field">
            <n-input-number v-model:value="form.depth_mm" :min="100" :max="3000" :step="50"
                            clearable placeholder="1000" style="width: 100%">
              <template #suffix>mm</template>
            </n-input-number>
            <div class="preset-chips">
              <span class="preset-chips__label">{{ t("racks.common") }}</span>
              <button v-for="d in DEPTH_PRESETS" :key="d" type="button"
                      class="preset-chip" :class="{ 'preset-chip--on': form.depth_mm === d }"
                      @click="form.depth_mm = d">{{ d }}</button>
            </div>
          </div>
        </n-form-item>
        <n-form-item :label="t('racks.numbering')">
          <n-select v-model:value="form.numbering" :options="numberingOpts" />
        </n-form-item>
        <n-form-item :label="t('nav.locations')">
          <n-select v-model:value="form.location_id" :options="locationOptions"
                    clearable :placeholder="t('racks.room_placeholder')" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showEdit = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submitRack">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <!-- 點空 U 位 → 挑裝置放入 -->
    <n-modal v-model:show="showDevicePick" preset="card" style="width: 420px"
             :title="t('racks.place_device') + (pickEmptyU != null ? ' · U' + pickEmptyU : '')">
      <n-form-item :label="t('nav.devices')">
        <n-select v-model:value="pickDeviceId" :options="pickDeviceOpts" filterable
                  :placeholder="t('racks.pick_device_ph')" />
      </n-form-item>
      <n-form-item :label="t('racks.u_size')">
        <n-input-number v-model:value="pickDeviceSize" :min="1" :max="20" style="width: 140px" />
      </n-form-item>
      <n-form-item :label="t('devices.rack_side')">
        <n-select v-model:value="pickSide" :options="pickSideOpts" style="width: 200px" />
      </n-form-item>
      <p style="font-size:12px; opacity:.6; margin:0 0 8px">{{ t("racks.place_device_hint") }}</p>
      <n-space justify="end">
        <n-button @click="showDevicePick = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" :disabled="!pickDeviceId" :loading="pickBusy" @click="confirmPickDevice">
          {{ t("common.confirm") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-space>
</template>

<style scoped>
/* 機房內機櫃並排成一橫排（依平面圖相對位置排序）；超出寬度橫向捲動，不上下堆疊 */
.rack-row {
  display: flex;
  flex-wrap: nowrap;
  gap: 16px;
  align-items: stretch;   /* 卡片等高，搭配 U 格 margin-top:auto → 機櫃靠下對齊（落地） */
  overflow-x: auto;
  /* 左側留 2px，避免最左機櫃框的左邊線被 overflow-x 裁掉 */
  padding: 0 2px 8px;
}
.rack-row > * { flex: 0 0 auto; }
.rack-row :deep(.n-card) { width: auto; height: 100%; }
/* 合併單卡模式：每櫃一欄（小標題 + bare 機櫃圖），共用外層卡片 */
.merged-rack { display: flex; flex-direction: column; }
.merged-rack__name {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 6px;
  white-space: nowrap;
  text-align: center;
}
.merged-rack__u {
  margin-left: 6px;
  font-weight: 400;
  font-size: 11px;
  opacity: 0.6;
  font-family: monospace;
}
/* 整排機櫃共用的圖例 */
.rack-legend-shared {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  margin-top: 10px;
  /* 薄底色區塊，讓圖例不再孤零零地浮在底部 */
  padding: 8px 12px;
  border: 1px solid rgba(127, 127, 127, 0.18);
  border-radius: 8px;
  background: rgba(127, 127, 127, 0.05);
}
.rack-legend-shared .legend-item {
  padding: 2px 8px;
  border-radius: 3px;
  color: white;
  font-family: monospace;
}
/* 寬/深常見尺寸快選膠囊 */
/* 寬/深欄位：輸入框 + 快選整欄上下排，輸入框占滿寬度，快選在下方一排 */
.dim-field {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.preset-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.preset-chips__label {
  font-size: 12px;
  opacity: 0.55;
  margin-right: 2px;
}
.preset-chip {
  font: inherit;
  font-size: 12.5px;
  line-height: 1;
  padding: 4px 11px;
  border-radius: 999px;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.28));
  background: transparent;
  color: inherit;
  cursor: pointer;
  transition: all 0.15s;
  font-variant-numeric: tabular-nums;
}
.preset-chip:hover {
  border-color: #18a058;
  color: #18a058;
}
.preset-chip--on {
  background: #18a058;
  border-color: #18a058;
  color: #fff;
  font-weight: 600;
}
</style>
