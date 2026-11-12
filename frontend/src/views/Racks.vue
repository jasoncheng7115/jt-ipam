<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { WIDTH_PARTS, spanFor, slotFor, posFor, rackDefaults, usesLevels, boardDefault, rackPixelHeight, levelEditOrder, slotNotation, RACK_FLOOR_MM } from "@/utils/rackSlots";
import { FINISHES } from "@/utils/rackFinish";
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
  useMessage,
  type DataTableColumns,
  type DataTableRowKey,
  NSwitch,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { RacksIcon, DeleteIcon, PlusIcon, EditIcon, SaveIcon, CancelIcon, LocationsIcon, PinIcon, CheckIcon, CopyIcon, RestoreIcon, SettingsIcon } from "@/icons";
import { exportTable, type ExportColumn } from "@/utils/tableExport";
import { exportRacksSvg, exportRacksPng, exportRacksDrawio, type RackNameAlign } from "@/utils/rackGraphicsExport";
import { getRackNameAlign } from "@/api/basic";
import { usePinned } from "@/composables/usePinned";
import { useExportBusy } from "@/composables/useExportBusy";
import { useRoute, useRouter } from "vue-router";
import { apiClient, apiErrMsg } from "@/api/client";
import RackDiagram from "@/components/RackDiagram.vue";
import RackRoomToolbar from "@/components/RackRoomToolbar.vue";
import { RACK_DEVICE_TYPES, rackTypeFill } from "@/utils/rackColors";
import RackFloorPlan from "@/components/RackFloorPlan.vue";
import {
  getRackDiagram, getRackEmbedConfig, rackEmbedUrl, revealRackEmbedToken,
  type RackDiagram as RD, type RackEmbedConfig,
  rackLevelOp, type RackLevelPlan, type RackKind, type RackFinish,
} from "@/api/racks";
import { bulkDeleteRacks, listLocations, listDevices, updateDevice, type Location, type Device } from "@/api/basic";
import { useAuthStore } from "@/stores/auth";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";

interface Rack {
  id: string;
  name: string;
  u_height: number;
  kind?: RackKind;
  finish?: RackFinish | null;
  width_mm?: number | null;
  row_height_mm?: number | null;
  depth_mm?: number | null;
  location_id: string | null;
  location_name?: string | null;
  description: string | null;
  seq?: number | null;
  device_count?: number;
  numbering?: "top-down" | "bottom-up";
  face?: "front" | "rear";
  expose_svg?: boolean;
  pos_x?: number | null;
  pos_y?: number | null;
}

const { t } = useI18n();
const msg = useMessage();
const auth = useAuthStore();
const router = useRouter();
const route = useRoute();
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
// 照角色授權（2026-11-12 客戶：掛「系統管理員」角色仍不能新增機櫃）；能不能改哪一座由後端判斷
const isAdmin = computed(() => auth.canCreate("rack"));
// 合併單卡模式：機房內所有機櫃排進同一張卡（不分卡片），存 localStorage（每瀏覽器）
const MERGED_VIEW_KEY = "jt_rack_merged";
const mergedView = ref<boolean>(localStorage.getItem(MERGED_VIEW_KEY) === "1");
watch(mergedView, (v) => localStorage.setItem(MERGED_VIEW_KEY, v ? "1" : "0"));
// 分段切換：獨立卡片 / 合併卡片 → 對應 mergedView 布林（合併 = true）
const rackViewMode = computed<"separate" | "merged">({
  get: () => (mergedView.value ? "merged" : "separate"),
  set: (v) => { mergedView.value = v === "merged"; },
});
// 整排共用的正/背面切換（合併卡片與分開卡片都是這一個，控制整排機櫃）
const mergedFace = ref<"front" | "rear">("front");
// 整排共用的顯示大小：一拉整排一起縮放。與單櫃檢視的分開記（整排通常要縮得比單櫃小）。
const ROOM_ZOOM_KEY = "jt.rackRoomZoom";
const roomZoom = ref(1);
try {
  const v = Number(localStorage.getItem(ROOM_ZOOM_KEY));
  if (v >= 0.35 && v <= 1) roomZoom.value = v;
} catch { /* 隱私模式讀不到就用預設 */ }
watch(roomZoom, (v) => {
  try { localStorage.setItem(ROOM_ZOOM_KEY, String(v)); } catch { /* 忽略 */ }
});
const mergedHasRear = computed(() =>
  roomDiagrams.value.some((d: any) => (d.devices || []).some((x: any) => x.rack_face === "rear")));
const roomFocus = ref<RD | null>(null);   // 在平面圖上點選的機櫃 → 顯示其 U 位
async function onRoomRackSelect(rackId: string) {
  try { roomFocus.value = await getRackDiagram(rackId); }
  catch (e) { msg.error(apiErrMsg(e)); }
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
// 並排時的底部對齊基準：該排最高的那台**畫出來多少 px**（不是 U 數 —— 機櫃與層架的
// 列高差好幾倍，用數量算會把層架推到畫面外）。
/** 各機櫃回報的實測自然高度（px）。由資料推算的高度不含外框的 border/padding，而那是
 *  逐型態不同的（標準機櫃 12px、鍍鉻層架 8px…）—— 只用推算值的話，最高的那一台會因為
 *  補白被夾到 0 而比其它台低個幾 px，看起來就是「地板高度不一樣」。 */
const measuredRackPx = ref<Record<string, number>>({});
function onRackMeasured(rackId: string, px: number) {
  if (measuredRackPx.value[rackId] === px) return;
  measuredRackPx.value = { ...measuredRackPx.value, [rackId]: px };
}
const maxRoomU = computed(() =>
  roomDiagrams.value.reduce((m, d) => Math.max(
    m, rackPixelHeight(d as any), measuredRackPx.value[d.rack_id] ?? 0), 0));
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

const { visibleKeys, setVisible, reset, order, setOrder, orderColumns } = useColumnPrefs(
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
  // 表格裡機櫃與層架混在一起，欄名用中性的「高度」，單位跟著每一列的型態走
  { title: t("racks.height_col"), key: "u_height", width: 100,
    render: (r) => rowsText(r.u_height, (r as any).kind),
    sorter: (a, b) => a.u_height - b.u_height },
  // 只填了其中一個也要顯示：原本要「兩個都有」才顯示，結果填了寬度的層架整格變成「—」，
  // 看起來像沒填過。缺的那一邊才是「—」。
  { title: t("racks.dimensions"), key: "dimensions", width: 140,
    render: (r) => (r.width_mm || r.depth_mm)
      ? `${r.width_mm ?? "—"} × ${r.depth_mm ?? "—"} mm`
      : "—" },
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
  orderColumns(allColumns.value.filter((c: any) =>
    c.type === "selection" || c.key === "actions" || visibleKeys.value.includes(c.key))),
);

// ── 新增 / 編輯 / 刪除機櫃 ──
const showEdit = ref(false);
const editing = ref<Rack | null>(null);
const form = ref({
  name: "", u_height: 42, location_id: null as string | null, description: "",
  seq: null as number | null,
  kind: "rack" as RackKind,
  finish: null as RackFinish | null,
  width_mm: null as number | null, row_height_mm: null as number | null,
  level_heights: null as number[] | null,
  board_mm: null as number | null, floor_mm: null as number | null,
  depth_mm: null as number | null,
  numbering: "top-down" as "top-down" | "bottom-up", face: "front" as "front" | "rear",
  expose_svg: false,
});
function openCreate() {
  editing.value = null;
  form.value = { name: "", u_height: 42, location_id: roomId.value, description: "",
    seq: null, kind: "rack", finish: null, width_mm: null, row_height_mm: null, level_heights: null,
    board_mm: null, floor_mm: null,
    depth_mm: null, numbering: "top-down", face: "front", expose_svg: false };
  showEdit.value = true;
}
function openEdit(r: Rack) {
  editing.value = r;
  form.value = {
    name: r.name, u_height: r.u_height, location_id: r.location_id, description: r.description ?? "",
    kind: (r as any).kind ?? "rack", finish: r.finish ?? null,
    row_height_mm: (r as any).row_height_mm ?? null,
    level_heights: ((r as any).level_heights ?? null) as number[] | null,
    board_mm: (r as any).board_mm ?? null, floor_mm: (r as any).floor_mm ?? null,
    seq: r.seq ?? null,
    width_mm: r.width_mm ?? null, depth_mm: r.depth_mm ?? null,
    numbering: r.numbering ?? "top-down", face: r.face ?? "front",
    expose_svg: r.expose_svg ?? false,
  };
  showEdit.value = true;
}
// 對外嵌入設定（系統層開關 + token）。機櫃逐櫃開放，但整個功能沒開就一律不給。
const embedCfg = ref<RackEmbedConfig | null>(null);
async function loadEmbedCfg() {
  try { embedCfg.value = await getRackEmbedConfig(); } catch { embedCfg.value = null; }
}
async function copyEmbedUrl() {
  if (!editing.value || !embedCfg.value?.token_set) return;
  try {
    // 權杖不再隨設定回傳，複製時才向後端取（後端留稽核）
    const url = rackEmbedUrl(editing.value.id, await revealRackEmbedToken());
    await navigator.clipboard.writeText(url);
    // 提醒使用者這串等同鑰匙 —— 貼到公開的地方等於把所有已開放的機櫃一起公開
    msg.success(t("racks.embed_copied"));
  } catch {
    msg.error(t("errors.server"));
  }
}

// 層架沒有 U：列的單位、編號方向的字都要跟著型態換，不然畫面會自相矛盾
const numberingOpts = computed(() => kindUsesLevels.value
  ? [
      { label: t("racks.numbering_top_down_level"), value: "top-down" },
      { label: t("racks.numbering_bottom_up_level"), value: "bottom-up" },
    ]
  : [
      { label: t("racks.numbering_top_down"), value: "top-down" },
      { label: t("racks.numbering_bottom_up"), value: "bottom-up" },
    ]);
// 常見機櫃外寬 / 外深（mm）快捷
const kindOpts = computed(() => [
  { label: t("racks.kind_rack"), value: "rack" },
  { label: t("racks.kind_industrial"), value: "industrial" },
  { label: t("racks.kind_lackrack"), value: "lackrack" },
  { label: t("racks.kind_shelf"), value: "shelf" },
  { label: t("racks.kind_wire_shelf"), value: "wire_shelf" },
  { label: t("racks.kind_angle_shelf"), value: "angle_shelf" },
  { label: t("racks.kind_wood_shelf"), value: "wood_shelf" },
  { label: t("racks.kind_kallax"), value: "kallax" },
]);
/** 表面顏色：只有外觀會因顏色而不同的型態才有（角鋼的黑／白／鍍鋅、KALLAX、LACK） */
const finishOpts = computed(() => (FINISHES[form.value.kind] ?? []).map((v) => ({
  label: t(`racks.finish_${v}`), value: v,
})));
/** 層架類才需要設「每層高度」；機櫃類是固定 1U。 */
const kindUsesLevels = computed(() => usesLevels(form.value.kind));
/** 留白時後端會用的預設值 —— 當成輸入框的提示，使用者才知道不填會變成多少。 */
const kindDefaults = computed(() => rackDefaults(form.value.kind));

/** 各層高度不同（層架的層板本來就一層一層可調）。關掉＝整台用同一個 row_height_mm。 */
/**
 * 常見的成品組合，一鍵套用。
 *
 * - IVAR／角鋼層架：給側架（立柱）總高與層數，層高＝「總高 − 板厚 × 片數 − 離地」平均分給每一層。
 *   IVAR 179 公分側架官方要求至少 4 層，實務上 6 層最常見。角鋼層架是台灣市售最常見的幾組
 *   （PChome 848 筆商品統計：寬 90／120、深 45、高 180 壓倒性最多，4 層與 5 層最常見）；
 *   「4 層」＝4 片板，可放的位置是 3 層之間＋最上面那片板的上面。
 * - KALLAX：給格數（欄×列），外寬＝65＋350×欄數，格子 335mm。
 * - LackRack：給疊幾張，一張 8U。
 */
interface RackPreset {
  label: string;
  levels: number;          // u_height（層數或 U 數）
  width: number;
  depth: number;
  frame?: number;          // 有給：層高由總高平均算出來
  board?: number;
  floor?: number;
  row?: number;            // 有給：直接用這個層高
}
const RACK_PRESETS = computed<Record<string, RackPreset[]>>(() => ({
  wood_shelf: [{ label: t("racks.preset_ivar_179"), levels: 6, frame: 1790, board: 18, floor: 10, width: 420, depth: 300 }],
  angle_shelf: ([[900, 450, 1800, 4], [1200, 450, 1800, 4], [1200, 450, 1800, 5], [900, 450, 1500, 4],
                 [600, 300, 1800, 4]] as const).map(([w, d, h, n]) => ({
    label: t("racks.preset_angle", { w: w / 10, d: d / 10, h: h / 10, n }),
    levels: n - 1, frame: h, board: 59, floor: 10, width: w, depth: d,
  })),
  kallax: ([[1, 4], [2, 2], [2, 4], [3, 3], [3, 4], [4, 4], [5, 5]] as const).map(([c, r]) => ({
    label: t("racks.preset_kallax", { c, r }),
    levels: r, row: 335, board: 15, floor: 0, width: 65 + 350 * c, depth: 390,
  })),
  lackrack: [1, 2, 3].map((n) => ({
    label: t("racks.preset_lack", { n, u: n * 8 }), levels: n * 8, width: 550, depth: 550,
  })),
}));
function applyPreset(pre: RackPreset) {
  form.value.u_height = pre.levels;
  form.value.width_mm = pre.width;
  form.value.depth_mm = pre.depth;
  if (pre.board != null) form.value.board_mm = pre.board;
  if (pre.floor != null) form.value.floor_mm = pre.floor;
  if (pre.frame != null) {
    // 層板本身與離地都不算在淨空高裡（層高填的是淨空高）
    const usable = pre.frame - (pre.board ?? 0) * (pre.levels + 1) - (pre.floor ?? 0);
    form.value.row_height_mm = Math.max(10, Math.round(usable / pre.levels));
  } else if (pre.row != null) {
    form.value.row_height_mm = pre.row;
  }
  perLevel.value = false;
}
const presetsOf = computed(() => RACK_PRESETS.value[form.value.kind] ?? []);
const presetHint = computed(() => ({
  kallax: t("racks.preset_hint_kallax"), lackrack: t("racks.preset_hint_lack"),
} as Record<string, string>)[form.value.kind] ?? t("racks.preset_hint"));
const hasPreset = computed(() => presetsOf.value.length > 0);
/** 這個型態的層板厚度預設（輸入框的提示字）。 */
const kindBoard = computed(() => boardDefault(form.value.kind));

const perLevel = ref(false);
/** 逐層高度的編輯暫存，由第 1 層起算；長度一律跟著層數走。 */
const levelRows = ref<number[]>([]);
/** 目前該填幾層 —— 層數改了就補齊或截掉，不要留下對不上的長度。 */
watch([() => form.value.u_height, () => form.value.kind, perLevel], () => {
  const n = form.value.u_height || 0;
  const fill = form.value.row_height_mm || kindDefaults.value.row;
  const cur = levelRows.value.slice(0, n);
  while (cur.length < n) cur.push(fill);
  levelRows.value = cur;
});
watch(() => form.value.level_heights, (v) => {
  // 開啟編輯時把既有資料帶進來；沒有逐層資料就等於「整台同高」
  perLevel.value = Array.isArray(v) && v.length > 0;
  levelRows.value = Array.isArray(v) ? [...v] : [];
}, { immediate: true });
/** 編輯列由上而下的順序，跟機櫃圖看到的一致（top-down＝最高層在最上面）。
 *  只動顯示順序，levelRows 仍是第 1 層在索引 0。 */
const levelRowOrder = computed(() => levelEditOrder(levelRows.value.length, form.value.numbering));
/** 「16U」或「4 層」——與 RackDiagram 的標題同一套字。 */
function rowsText(n: number, kind?: string | null): string {
  return usesLevels(kind) ? t("racks.rows_levels", { n }) : `${n}U`;
}
// 快捷尺寸也要跟著型態：機櫃是 60/80 公分寬，層架市面上是 90/120/150 公分。
// 木質層架給 IKEA IVAR 的實際尺寸：層板有 42×30 / 42×50 / 83×30 / 83×50 四種組合。
const WIDTH_PRESETS = computed(() => {
  const k = form.value.kind;
  if (k === "wood_shelf") return [420, 830];
  // 角鋼：台尺制 15 公分一格，最常見 90、120，其次 60、150、180
  if (k === "angle_shelf") return [600, 900, 1200, 1500, 1800];
  // KALLAX：1～5 欄（外寬＝65＋350×欄數）
  if (k === "kallax") return [415, 765, 1115, 1465, 1815];
  if (k === "lackrack") return [550];
  return kindUsesLevels.value ? [900, 1200, 1500] : [600, 800];
});
const DEPTH_PRESETS = computed(() => {
  const k = form.value.kind;
  if (k === "wood_shelf") return [300, 500];
  if (k === "angle_shelf") return [300, 450, 600];
  if (k === "kallax") return [390];
  if (k === "lackrack") return [550];
  return kindUsesLevels.value ? [450, 600, 750] : [600, 800, 1000, 1100, 1200];
});

/**
 * 換型態時把「還沒動過的」列數換成該型態的常見值：層架不會有 42 層，
 * 機櫃也不會只有 4 U。只在值還是另一種型態的預設時才換 —— 使用者自己填過的不動。
 */
// 各型態的常見列數。以前只分「機櫃 42／層架 4」而且判斷層架時只認 shelf 與 wire_shelf，
// 換成 IVAR 時列數不會跟著換；現在逐型態列出，只要值還是「上一個型態的預設」就換。
const DEFAULT_ROWS: Record<string, number> = {
  rack: 42, industrial: 42, lackrack: 8, shelf: 4, wire_shelf: 4, wood_shelf: 6,
  angle_shelf: 3, kallax: 4,
};
watch(() => form.value.kind, (now, was) => {
  if (!was || now === was) return;
  const untouched = form.value.u_height === (DEFAULT_ROWS[was] ?? 42);
  if (untouched) form.value.u_height = DEFAULT_ROWS[now] ?? 42;
  // 顏色只對有顏色選項的型態有意義；換到別的型態時不要帶著一個不適用的值
  if (form.value.finish && !(FINISHES[now] ?? []).includes(form.value.finish)) form.value.finish = null;
});
/** 離地高度留白時會用的值（提示文字）：KALLAX 直接落地、LackRack 桌下約 44mm */
const kindFloor = computed(() => RACK_FLOOR_MM[form.value.kind] ?? 0);

async function submitRack() {
  if (!form.value.name.trim()) { msg.error(t("common.name_required")); return; }
  const payload = {
    name: form.value.name.trim(),
    u_height: form.value.u_height,
    location_id: form.value.location_id ?? null,
    description: form.value.description.trim() || null,
    seq: form.value.seq ?? null,
    kind: form.value.kind ?? 'rack',
    row_height_mm: form.value.row_height_mm ?? null,
    level_heights: perLevel.value ? levelRows.value.slice(0, form.value.u_height) : null,
    board_mm: form.value.board_mm ?? null,
    floor_mm: form.value.floor_mm ?? null,
    finish: FINISHES[form.value.kind] ? (form.value.finish ?? null) : null,
    width_mm: form.value.width_mm ?? null,
    depth_mm: form.value.depth_mm ?? null,
    numbering: form.value.numbering,
    face: form.value.face,
    expose_svg: form.value.expose_svg,
  };
  try {
    const editedId = editing.value?.id ?? null;
    if (editing.value) await apiClient.patch(`/api/v1/racks/${editing.value.id}`, payload);
    else await apiClient.post("/api/v1/racks", payload);
    showEdit.value = false;
    msg.success(t("common.ok"));
    await refresh();
    if (roomId.value) await loadRoom(roomId.value);
    // 單櫃檢視與平面圖點選的那一櫃不在 loadRoom 的範圍裡：以前改完顏色、寬度這類設定，
    // 畫面要重新整理才會換（使用者回報）。改的是哪一櫃，就把它在每一處都重畫。
    if (editedId) await refreshRackEverywhere(editedId);
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

/**
 * 只有「最後一次請求」的結果可以套用。
 *
 * 之前沒有這個守門：帶 `?rack=` 進來時會先被預設選取觸發一次載入、再被指名的那台觸發
 * 一次，兩個請求同時在飛，**慢的那個後到就贏了** —— 下拉寫著 R5、下面卻畫 HQ-A01。
 */
let diagramSeq = 0;
async function loadDiagram(id: string) {
  const mine = ++diagramSeq;
  diagramLoading.value = true;
  try {
    const d = await getRackDiagram(id);
    if (mine !== diagramSeq) return;          // 已經有更新的請求了，這份丟掉
    diagram.value = d;
  } catch {
    if (mine !== diagramSeq) return;
    msg.error(t("errors.network"));
    diagram.value = null;
  } finally {
    if (mine === diagramSeq) diagramLoading.value = false;
  }
}

watch(selected, (v) => {
  if (v) { roomId.value = null; void loadDiagram(v); }
  else diagram.value = null;
});

// 已經在機櫃頁時再帶 ?rack= 過來（機櫃圖上群組的「另有 N 台在機櫃 B」）：元件不會重新掛載，
// onMounted 那段不會再跑，要另外跟著網址切過去
watch(() => route.query.rack, (v) => {
  const id = typeof v === "string" ? v : "";
  if (!id || id === selected.value || !rows.value.some((x) => x.id === id)) return;
  roomId.value = null;
  selected.value = id;
});

// 合併卡圖形匯出沿用全域「機櫃名稱對齊」偏好（與 RackDiagram 一致）
const mergedNameAlign = ref<RackNameAlign>("left");

onMounted(async () => {
  void loadEmbedCfg();
  // 從別的頁帶機櫃 id 過來（例如儀表板的使用率）：直接選到那一台，
  // 不要再套用「預設機房」的整排檢視，否則等於沒帶到。
  const wanted = String(route.query.rack ?? "");

  void getRackNameAlign().then((a) => { mergedNameAlign.value = a as RackNameAlign; });
  // 先 refresh() 把所有機櫃載進 rows（loadRoom 依賴它過濾該機房的機櫃，
  // 否則「機櫃示意圖」會誤判此機房尚無機櫃）。refresh 可能先預設第一個機櫃。
  await refresh();
  if (wanted && rows.value.some((x) => x.id === wanted)) {
    roomId.value = null;
    selected.value = wanted;
  }
  try {
    const r = await listLocations();
    locations.value = r.items;
    // 決定預設機房（優先 pinnedRoom，其次機房頁釘選的第一個）；設定後 watch(roomId)
    // 會清掉單櫃選取並用「整排機櫃」檢視覆蓋 refresh 的單櫃預設。
    if (wanted) return;            // 指名了機櫃就不要被預設機房蓋掉
    let def: string | null =
      (pinnedRoom.value && r.items.some((l) => l.id === pinnedRoom.value)) ? pinnedRoom.value : null;
    if (!def) def = locPin.ids.value.find((id) => r.items.some((l) => l.id === id)) ?? null;
    if (def) roomId.value = def;
  } catch { /* silent */ }
});

// ── 點空 U 位 → 挑裝置放入（任一機櫃圖都可點，event 會帶 rack_id）──
// ── 層數調整（插入／刪除一層，上面的裝置整批移位）──
// 放在「機櫃設定」視窗裡（以前在頁首，使用者說不該放那裡）：操作的對象就是正在編輯的那一櫃。
const showLevelOps = ref(false);
const levelOp = ref<"insert" | "remove">("remove");
const levelAt = ref(1);
const levelPlan = ref<RackLevelPlan | null>(null);
const levelBusy = ref(false);
/** 正在調整的機櫃（id／型態／目前的層數）。 */
const levelRack = ref<{ id: string; kind: string | null; height: number } | null>(null);
/** 做完留著，按「復原」就照它送回去（插入與刪除互為反操作，伺服器不必存狀態）。 */
const levelUndo = ref<(RackLevelPlan["undo"] & { rackId: string }) | null>(null);

const levelUnit = (n: number) =>
  usesLevels(levelRack.value?.kind) ? t("racks.level_at", { n }) : t("racks.level_at_u", { n });

function openLevelOps() {
  const r = editing.value;
  if (!r) return;
  levelRack.value = { id: r.id, kind: (r as any).kind ?? "rack", height: r.u_height };
  levelPlan.value = null;
  levelAt.value = 1;
  levelOp.value = "remove";
  showLevelOps.value = true;
  void previewLevel();
}
async function previewLevel() {
  if (!levelRack.value) return;
  levelBusy.value = true;
  try {
    levelPlan.value = await rackLevelOp(levelRack.value.id, {
      op: levelOp.value, at: levelAt.value, dry_run: true,
    });
  } catch (e) {
    levelPlan.value = null;
    msg.error(apiErrMsg(e));
  } finally { levelBusy.value = false; }
}
/**
 * 層數調整做完之後：清單、每一處的機櫃圖，以及**還開著的設定視窗**都要換成新的值。
 * 設定視窗裡留著舊的層數與逐層高度的話，使用者接著按「儲存」就會把剛做的調整蓋回去。
 */
async function afterLevelChange(rackId: string) {
  await refresh();
  await refreshRackEverywhere(rackId);
  if (showEdit.value && editing.value?.id === rackId) {
    const fresh = rows.value.find((x) => x.id === rackId);
    if (fresh) openEdit(fresh);
  }
}
async function applyLevel() {
  const rack = levelRack.value;
  if (!rack) return;
  levelBusy.value = true;
  try {
    const r = await rackLevelOp(rack.id, { op: levelOp.value, at: levelAt.value });
    levelUndo.value = r.undo ? { ...r.undo, rackId: rack.id } : null;
    showLevelOps.value = false;
    msg.success(t("racks.level_done", { h: `${r.old_height} → ${r.new_height}`, n: r.moves.length }));
    await afterLevelChange(rack.id);
  } catch (e) { msg.error(apiErrMsg(e)); } finally { levelBusy.value = false; }
}
async function undoLevel() {
  const u = levelUndo.value;
  if (!u) return;
  levelBusy.value = true;
  try {
    await rackLevelOp(u.rackId, { op: u.op, at: u.at, height_mm: u.height_mm });
    levelUndo.value = null;
    msg.success(t("racks.level_undone"));
    await afterLevelChange(u.rackId);
  } catch (e) { msg.error(apiErrMsg(e)); } finally { levelBusy.value = false; }
}

const showDevicePick = ref(false);
const pickEmptyU = ref<number | null>(null);
const pickRackId = ref<string | null>(null);
/** 被點的那台是「層」還是「U」—— 合併卡片時畫面上不只一台，不能只看目前選取的那台。 */
const pickUsesLevels = computed(() => {
  const id = pickRackId.value;
  const d = [diagram.value, roomFocus.value, ...roomDiagrams.value]
    .find((x) => x && (x as any).rack_id === id);
  return usesLevels((d as any)?.kind);
});
const pickPosLabel = computed(() => pickEmptyU.value == null ? "" :
  (pickUsesLevels.value ? t("racks.level_at", { n: pickEmptyU.value })
                        : t("racks.level_at_u", { n: pickEmptyU.value })));
const pickDeviceId = ref<string | null>(null);
const pickDeviceSize = ref(1);
// 橫向位置：介面上選「寬度 + 第幾格」，送出時換算成 rack_slot / rack_slot_span（issue #31）
const pickParts = ref<number>(1);
const pickPos = ref<number>(1);
const pickPartsOpts = computed(() => WIDTH_PARTS.map((n) => ({
  label: n === 1 ? t("devices.rack_width_full") : t("devices.rack_width_nth", { n }),
  value: n,
})));
const pickPosOpts = computed(() => Array.from({ length: pickParts.value }, (_, i) => ({
  label: t("devices.rack_pos_nth", { n: i + 1 }), value: i + 1,
})));
const pickableDevices = ref<Device[]>([]);
const pickBusy = ref(false);
const pickDeviceOpts = computed(() => pickableDevices.value.map((d) => ({
  label: d.ip ? `${d.name} — ${d.ip}` : d.name, value: d.id,
})));
async function onPickEmpty(u: number, rackId: string, slot?: number) {
  pickEmptyU.value = u;
  pickRackId.value = rackId;
  pickDeviceId.value = null;
  pickDeviceSize.value = 1;
  // 點空隙進來時：挑一個「格線剛好對齊被點格子」的寬度當預設，位置設成那一格。
  // 點整列空位（slot=0）就維持整 U。
  pickParts.value = 1;
  pickPos.value = 1;
  if (slot && slot > 0) {
    const fit = WIDTH_PARTS.find((n) => slot % spanFor(n) === 0) ?? 1;
    pickParts.value = fit;
    pickPos.value = posFor(slot, fit);
  }
  showDevicePick.value = true;
  try {
    const r = await listDevices();
    // 優先列「尚未放進任何機櫃」的裝置；其餘也會列出（選了會搬過來）
    pickableDevices.value = r.items
      .filter((d) => !(d as any).rack_id)
      .concat(r.items.filter((d) => (d as any).rack_id));
  } catch (e) { msg.error(apiErrMsg(e)); }
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
      rack_slot: slotFor(pickParts.value, pickPos.value),
      rack_slot_span: spanFor(pickParts.value),
    } as any);
    showDevicePick.value = false;
    msg.success(t("common.ok"));
    await refreshRackEverywhere(pickRackId.value);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { pickBusy.value = false; }
}

// 合併卡匯出：圖形（SVG/PNG/draw.io，整個機房多機櫃並排）+ 純資料格式
const mergedExportOptions = computed(() => [
  { label: "SVG", key: "svg" },
  { label: "PNG", key: "png" },
  { label: "draw.io", key: "drawio" },
  { type: "divider", key: "d1" },
  { label: "CSV", key: "csv" },
  { label: "Excel (.xlsx)", key: "xlsx" },
  { label: "OpenDocument (.ods)", key: "ods" },
  { label: "Markdown (.md)", key: "md" },
  { label: t("export.fmt_txt"), key: "txt" },
]);
const { exporting: mergedExporting, run: runMergedExport } = useExportBusy();
function onMergedExport(key: string) { void runMergedExport(() => doMergedExport(key)); }
function doMergedExport(key: string) {
  if (["svg", "png", "drawio"].includes(key)) {
    if (!roomDiagrams.value.length) return;
    // 文案（「9 層」「頂」）在這裡翻好再交給匯出器 —— utils 不碰 i18n
    const diags = roomDiagrams.value.map((d: any) => ({
      ...d, rowsLabel: rowsText(d.u_height, d.kind), topLabel: t("racks.level_top"),
    }));
    const fname = "room-racks";
    // 對齊基準是**像素**（層架的列高是機櫃的好幾倍），匯出器也照像素算
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
    // 同一排可能混著機櫃與層架，標題用中性的「層／U」，實際單位看 rack 欄
    { key: "u_position", label: `${t("racks.levels")} / U` },
    { key: "u_size", label: "Size" },
    { key: "slot", label: t("devices.rack_width") },
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
      slot: slotNotation(dev),
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
          :options="rows.map((r) => ({ label: `${r.name} (${rowsText(r.u_height, (r as any).kind)})`, value: r.id }))"
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
      <!-- 標題放在卡片的標題列（跟其他卡片同一個樣式），工具按鈕留在內文最上方 -->
      <n-card style="margin-bottom: 16px">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="18"><RacksIcon /></n-icon>
            <span>{{ t("racks.floor_plan") }}</span>
          </n-space>
        </template>
        <rack-floor-plan :location-id="roomId" :can-edit="isAdmin" @select="onRoomRackSelect" />
      </n-card>

      <!-- 在平面圖上點選的機櫃 → 顯示其 U 位 -->
      <n-card v-if="roomFocus" style="margin-bottom: 16px" :bordered="false" content-style="padding:0">
        <n-space justify="end" style="margin-bottom: 6px">
          <n-button size="tiny" quaternary @click="roomFocus = null"><template #icon><n-icon><CancelIcon /></n-icon></template>
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
            <!-- 控制元件放在卡片內文最上方（標題列不放控制元件） -->
            <rack-room-toolbar v-model:face="mergedFace" v-model:zoom="roomZoom" :has-rear="mergedHasRear"
                               :export-options="mergedExportOptions" :exporting="mergedExporting" @export="onMergedExport" />
            <div class="rack-row">
              <div v-for="d in roomDiagrams" :key="d.rack_id" class="merged-rack">
                <div class="merged-rack__name">
                  {{ d.name }}<span class="merged-rack__u">{{ rowsText(d.u_height, (d as any).kind) }}</span>
                </div>
                <rack-diagram :diagram="d" :show-legend="false" :editable="isAdmin"
                              :floor-align-to="maxRoomU" :face="mergedFace" :shared-zoom="roomZoom" :controls="false"
                              @measured="onRackMeasured" @update:face="mergedFace = $event"
                              bare @pick-empty="onPickEmpty" />
              </div>
            </div>
            <div class="rack-legend-shared">
              <span v-for="ty in RACK_DEVICE_TYPES" :key="ty" class="legend-item"
                    :style="{ background: rackTypeFill(ty) }">{{ ty }}</span>
            </div>
          </n-card>
          <!-- 各自一張卡片（預設） -->
          <template v-else>
            <rack-room-toolbar v-model:face="mergedFace" v-model:zoom="roomZoom" :has-rear="mergedHasRear"
                               :export-options="mergedExportOptions" :exporting="mergedExporting" @export="onMergedExport" />
            <div class="rack-row">
              <rack-diagram v-for="d in roomDiagrams" :key="d.rack_id" :diagram="d"
                            :show-legend="false" :editable="isAdmin" :floor-align-to="maxRoomU"
                            :face="mergedFace" :shared-zoom="roomZoom"
                            @measured="onRackMeasured" @update:face="mergedFace = $event"
                            @pick-empty="onPickEmpty" />
            </div>
            <!-- 整排機櫃共用一個圖例（不用每櫃都重複） -->
            <div class="rack-legend-shared">
              <span v-for="ty in RACK_DEVICE_TYPES" :key="ty" class="legend-item"
                    :style="{ background: rackTypeFill(ty) }">{{ ty }}</span>
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
                  :title="isAdmin ? undefined : t('errors.create_needs_write')">
          <template #icon><n-icon><PlusIcon /></n-icon></template>
          {{ t("racks.add") }}
        </n-button>
        <ColumnPicker :all="columnPickerItems" :visible="visibleKeys"
                      @update:visible="setVisible" @reset="reset"
                      :order="order" @update:order="setOrder" />
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
        <n-button size="small" @click="checkedKeys = []"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.clear_selection") }}</n-button>
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
        <!-- 種類排在最前面：層數／編號方向／層板厚度這些欄位的意義都取決於它（GitHub issue #35）。
             「編號」是機房裡的左右排序，跟種類無關，放在機房旁邊。 -->
        <n-form-item :label="t('racks.kind')">
          <n-select v-model:value="form.kind" :options="kindOpts" style="width: 100%" />
          <template #feedback>
            <span class="field-hint">{{ t("racks.kind_hint") }}</span>
          </template>
        </n-form-item>
        <n-form-item v-if="hasPreset" :label="t('racks.preset')">
          <div style="width: 100%">
            <div class="preset-buttons">
              <n-button v-for="pre in presetsOf" :key="pre.label" size="small" @click="applyPreset(pre)">
                {{ pre.label }}
              </n-button>
            </div>
            <span class="field-hint">{{ presetHint }}</span>
          </div>
        </n-form-item>
        <n-form-item v-if="finishOpts.length" :label="t('racks.finish')">
          <n-select v-model:value="form.finish" :options="finishOpts" clearable
                    :placeholder="finishOpts[0]?.label" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="kindUsesLevels ? t('racks.level_numbering') : t('racks.numbering')">
          <div style="width: 100%">
            <n-select v-model:value="form.numbering" :options="numberingOpts" />
            <span class="field-hint">{{ t("racks.numbering_hint") }}</span>
          </div>
        </n-form-item>
        <n-form-item :label="kindUsesLevels ? t('racks.levels') : t('racks.u_height')">
          <div style="width: 100%">
            <div class="level-count-row">
              <n-input-number v-model:value="form.u_height" :min="1" :max="99" style="flex: 1 1 0; min-width: 0" />
              <!-- 層數調整：插入／刪除一層時上面的裝置整批跟著移（直接改層數不會搬裝置）。
                   只有既有的機櫃才有裝置可搬，新增時不出現。 -->
              <n-button v-if="editing && isAdmin" size="small" class="level-ops-btn" @click="openLevelOps"><template #icon><n-icon><SettingsIcon /></n-icon></template>
                {{ t("racks.level_ops") }}
              </n-button>
              <!-- 剛做完才出現的復原：照著回應帶的 undo 送回去即可 -->
              <n-button v-if="editing && levelUndo && levelUndo.rackId === editing.id"
                        size="small" type="warning" ghost :loading="levelBusy" @click="undoLevel"><template #icon><n-icon><RestoreIcon /></n-icon></template>
                {{ t("racks.level_undo") }}
              </n-button>
            </div>
            <span v-if="editing && isAdmin" class="field-hint">{{ t("racks.level_ops_hint") }}</span>
          </div>
        </n-form-item>
        <n-form-item v-if="kindUsesLevels" :label="t('racks.board_mm')">
          <n-input-number v-model:value="form.board_mm" :min="0" :max="200" :step="1"
                          clearable :placeholder="String(Math.round(kindBoard))" style="width: 100%">
            <template #suffix>mm</template>
          </n-input-number>
        </n-form-item>
        <n-form-item v-if="kindUsesLevels" :label="t('racks.floor_mm')">
          <n-input-number v-model:value="form.floor_mm" :min="0" :max="1000" :step="5"
                          clearable :placeholder="String(kindFloor)" style="width: 100%">
            <template #suffix>mm</template>
          </n-input-number>
        </n-form-item>
        <n-form-item v-if="kindUsesLevels" :label="t('racks.row_height_mm')">
          <div style="width: 100%">
            <!-- 開關放在輸入欄「上面」：先決定要不要逐層，再填值。
                 用 n-switch 與同一張表單的「對外嵌入」一致（n-checkbox 沒 import，
                 寫了只會渲染成一行純文字 —— 看起來像少了元件）。 -->
            <div class="per-level-toggle">
              <n-switch v-model:value="perLevel" size="small" />
              <span>{{ t("racks.per_level_heights") }}</span>
            </div>
            <n-input-number v-if="!perLevel" v-model:value="form.row_height_mm"
                            :min="10" :max="1000" :step="10" clearable
                            :placeholder="String(kindDefaults.row)" style="width: 100%">
              <template #suffix>mm</template>
            </n-input-number>
            <!-- 逐層高度：層架的層板一層一層可調，常見裝法是下面留高、上面壓矮 -->
            <div v-else class="level-rows">
              <div v-for="i in levelRowOrder" :key="'lv' + i" class="level-row">
                <span class="level-row__label">{{ t("racks.level_n", { n: i + 1 }) }}</span>
                <n-input-number v-model:value="levelRows[i]" :min="10" :max="1000" :step="10"
                                size="small" style="flex: 1 1 0; min-width: 0">
                  <template #suffix>mm</template>
                </n-input-number>
              </div>
            </div>
            <span class="field-hint">{{ t("racks.per_level_hint") }}</span>
          </div>
        </n-form-item>
        <n-form-item :label="t('racks.width_mm')">
          <div class="dim-field">
            <n-input-number v-model:value="form.width_mm" :min="100" :max="2000" :step="50"
                            clearable :placeholder="String(kindDefaults.width)" style="width: 100%">
              <template #suffix>mm</template>
            </n-input-number>
            <div class="preset-chips">
              <span class="preset-chips__label">{{ t("racks.common") }}</span>
              <button v-for="w in WIDTH_PRESETS" :key="w" type="button"
                      class="preset-chip" :class="{ 'preset-chip--on': form.width_mm === w }"
                      @click="form.width_mm = w">{{ w }}</button>
            </div>
            <span v-if="!kindUsesLevels" class="field-hint">{{ t("racks.width_outer_hint") }}</span>
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
        <n-form-item :label="t('racks.seq')">
          <n-input-number v-model:value="form.seq" :min="0" :max="9999" clearable
                          :placeholder="t('racks.seq_ph')" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('nav.locations')">
          <n-select v-model:value="form.location_id" :options="locationOptions"
                    clearable :placeholder="t('racks.room_placeholder')" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item :label="t('racks.embed_label')">
          <n-space vertical size="small" style="width: 100%">
            <n-switch v-model:value="form.expose_svg" />
            <!-- 機櫃圖會揭露裝置名稱與位置，開之前要讓人知道自己在開什麼 -->
            <span class="embed-hint">{{ t("racks.embed_hint") }}</span>
            <n-space v-if="editing && form.expose_svg" size="small" align="center">
              <!-- 剛把開關打開、還沒按儲存時，那串網址一定回 404 —— 先給得出來只會
                   讓人以為功能壞了。要等**存進去的**值也是開啟才放行。 -->
              <n-button size="small" :disabled="!embedCfg?.enabled || !editing.expose_svg"
                        @click="copyEmbedUrl"><template #icon><n-icon><CopyIcon /></n-icon></template>
                {{ t("racks.embed_copy") }}
              </n-button>
              <span v-if="embedCfg?.enabled && !editing.expose_svg" class="embed-hint">
                {{ t("racks.embed_save_first") }}
              </span>
              <span v-if="!embedCfg?.enabled" class="embed-hint">
                {{ t("racks.embed_disabled_hint") }}
              </span>
            </n-space>
          </n-space>
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
    <!-- 層數調整：刪除／插入一層，上面的裝置整批移位。先預覽、做完可復原。 -->
    <n-modal v-model:show="showLevelOps" preset="card" style="width: 520px"
             :title="t('racks.level_ops')">
      <n-space vertical :size="14">
        <span class="field-hint">{{ t("racks.level_hint") }}</span>
        <n-space align="center" :size="10">
          <n-radio-group v-model:value="levelOp" size="small" @update:value="previewLevel">
            <n-radio-button value="remove">{{ t("racks.level_remove") }}</n-radio-button>
            <n-radio-button value="insert">{{ t("racks.level_insert") }}</n-radio-button>
          </n-radio-group>
          <n-input-number v-model:value="levelAt" :min="1"
                          :max="levelOp === 'insert' ? (levelRack?.height ?? 1) + 1 : (levelRack?.height ?? 1)"
                          size="small" style="width: 120px" @update:value="previewLevel" />
          <span style="font-size: 13px; opacity: .7">{{ levelUnit(levelAt) }}</span>
        </n-space>

        <n-spin :show="levelBusy">
          <div v-if="levelPlan" class="level-plan">
            <div v-if="levelPlan.blockers.length" class="level-plan__blocked">
              <b>{{ t("racks.level_blocked") }}</b>
              <div v-for="b in levelPlan.blockers" :key="b.device_id">
                • {{ b.name }}（{{ levelUnit(b.u_position) }}）
                <span v-if="b.reason === 'spans'">{{ t("racks.level_blocked_spans") }}</span>
              </div>
            </div>
            <template v-else>
              <div>{{ levelPlan.old_height }} → {{ levelPlan.new_height }}</div>
              <div v-if="!levelPlan.moves.length">{{ t("racks.level_no_moves") }}</div>
              <template v-else>
                <div><b>{{ t("racks.level_moves", { n: levelPlan.moves.length }) }}</b></div>
                <div v-for="m in levelPlan.moves" :key="m.device_id" class="level-plan__move">
                  {{ m.name }}：{{ levelUnit(m.from) }} → {{ levelUnit(m.to) }}
                </div>
              </template>
            </template>
          </div>
        </n-spin>
      </n-space>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="showLevelOps = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button size="small" type="primary" :disabled="!levelPlan || !!levelPlan.blockers.length"
                    :loading="levelBusy" @click="applyLevel"><template #icon><n-icon><CheckIcon /></n-icon></template>
            {{ t("racks.level_apply") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal v-model:show="showDevicePick" preset="card" style="width: 420px"
             :title="t('racks.place_device') + (pickPosLabel ? ' · ' + pickPosLabel : '')">
      <n-form-item :label="t('nav.devices')">
        <n-select v-model:value="pickDeviceId" :options="pickDeviceOpts" filterable
                  :placeholder="t('racks.pick_device_ph')" />
      </n-form-item>
      <n-form-item :label="pickUsesLevels ? t('devices.level_size') : t('racks.u_size')">
        <n-input-number v-model:value="pickDeviceSize" :min="1" :max="20" style="width: 140px" />
      </n-form-item>
      <n-form-item :label="t('devices.rack_width')">
        <n-space :size="8">
          <n-select v-model:value="pickParts" :options="pickPartsOpts" style="width: 130px"
                    @update:value="pickPos = 1" />
          <n-select v-if="pickParts > 1" v-model:value="pickPos" :options="pickPosOpts"
                    style="width: 130px" />
        </n-space>
      </n-form-item>
      <p style="font-size:12px; opacity:.6; margin:0 0 8px">{{ pickUsesLevels ? t("racks.place_device_hint_level") : t("racks.place_device_hint") }}</p>
      <n-space justify="end">
        <n-button @click="showDevicePick = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
        <n-button type="primary" :disabled="!pickDeviceId" :loading="pickBusy" @click="confirmPickDevice"><template #icon><n-icon><CheckIcon /></n-icon></template>
          {{ t("common.confirm") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-space>
</template>

<style scoped>
.level-plan { font-size: 13px; line-height: 1.8; max-height: 300px; overflow-y: auto; }
.level-plan__move { opacity: 0.8; }
.level-plan__blocked { color: var(--error-color, #d03050); }

.per-level-toggle { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 13px; }

/* 逐層高度：層數多時要能捲，不然表單會被撐爆 */
.level-rows { display: flex; flex-direction: column; gap: 6px; max-height: 260px; overflow-y: auto; }
.level-row { display: flex; align-items: center; gap: 8px; }
.level-row__label { flex: 0 0 62px; font-size: 12px; opacity: 0.7; }

/* 說明文字與下一個欄位之間要留白，不然會黏在一起 */
.field-hint {
  display: block;
  font-size: 12px;
  opacity: 0.6;
  line-height: 1.5;
  padding: 2px 0 8px;
}

.embed-hint { font-size: 12px; opacity: 0.65; line-height: 1.5; }
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
/* 寬/深常見尺寸快選標籤 */
/* 寬/深欄位：輸入框 + 快選整欄上下排，輸入框占滿寬度，快選在下方一排 */
.dim-field {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.preset-buttons { display: flex; flex-wrap: wrap; gap: 6px; }
.level-count-row { display: flex; align-items: center; gap: 8px; }
.level-ops-btn { flex: 0 0 auto; }
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
