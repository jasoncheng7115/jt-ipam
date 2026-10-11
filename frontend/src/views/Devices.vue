<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
import { RACK_SLOTS, WIDTH_PARTS, spanFor, slotFor, partsFor, posFor, usesLevels,
  rackPickRows, rackRowIsTop, slotBoxPct, slotWhere } from "@/utils/rackSlots";
const _authBtn = useAuthStore();
import { computed, h, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import { apiClient, apiErrMsg } from "@/api/client";
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
import { resolveRackLocation } from "@/utils/rackLocation";
import {
  DevicesIcon, PlusIcon, EditIcon, UploadIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon, EyeIcon, LinkIcon, RacksIcon,
} from "@/icons";
import { cmpNatural } from "@/utils/sort";
import { useIpOptions } from "@/composables/useIpOptions";
import { listSubnets } from "@/api/subnets";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import DeviceImportModal from "@/components/DeviceImportModal.vue";
import { withExportValue } from "@/utils/tableExport";
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
// 伺服器端有多少筆（不是已載入的筆數）。畫面只載一頁，兩者不一樣時要講出來，
// 否則使用者會以為看到的就是全部 —— 客戶回報「新增的裝置看不到也搜不到」正是這個。
const totalOnServer = ref(0);
// 清單一次載入的上限。超過就靠伺服器端搜尋，並在畫面上明說只顯示了一部分。
const MAX_ROWS = 5000;
let searchTimer: ReturnType<typeof setTimeout> | null = null;

/** 搜尋交給後端做：只在已載入的那一頁上過濾，找不到超出範圍的裝置。 */
function onFilterInput() {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { void refresh(); }, 300);
}

const truncated = computed(() => totalOnServer.value > rows.value.length);
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const locations = ref<Location[]>([]);
const racks = ref<Rack[]>([]);
const loading = ref(false);
const show = ref(false);
// 裝置匯入（issue #46）
const showImport = ref(false);
function onImportQueued(taskId: string) {
  // 匯入在背景跑：右下角的「背景作業」面板顯示進度與結果，跑完重抓清單
  trackTask(taskId, { onDone: () => void refresh() });
}
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
  rack_slot: number; rack_slot_span: number;
  customer_id: string | null;
  primary_ip_id: string | null;
}>({
  name: "", fqdn: "", type: "server",
  vendor: "", model: "", serial: "",
  description: "",
  location_id: null, rack_id: null,
  u_position: null, u_size: null, rack_face: null, rack_slot: 0, rack_slot_span: RACK_SLOTS,
  customer_id: null,
  primary_ip_id: null,
});

const DEVICE_TYPES = ["server", "switch", "router", "firewall", "ap", "storage", "ipmi",
  "patch_panel", "pdu", "ups", "workstation", "other"];
const typeOpts = DEVICE_TYPES.map((v) => ({ label: t(`devices.type_${v}`), value: v }));
const rackFaceOpts = computed(() => [
  { label: t("devices.rack_face_front"), value: "front" },
  { label: t("devices.rack_face_rear"), value: "rear" },
]);
/** 所選機櫃是不是以「層」計 —— 表單標籤要跟著換，不然層架上會寫「U 位」。 */
const rackUsesLevels = computed(() =>
  usesLevels((racks.value.find((r) => r.id === form.value.rack_id) as any)?.kind));
const rackSideOpts = computed(() => [
  // 層架上「整 U」要寫成「整層」—— 同一組選項在兩種機架上用字不同
  { label: rackUsesLevels.value ? t("devices.rack_height_full") : t("devices.rack_width_full"),
    value: 1 },
  ...WIDTH_PARTS.filter((n) => n > 1).map((n) => ({ label: t("devices.rack_width_nth", { n }), value: n })),
]);
// 介面上的「寬度 + 第幾格」，送出時換算成 rack_slot / rack_slot_span（issue #31）
const widthParts = ref<number>(1);
const widthPos = ref<number>(1);
const widthPosOpts = computed(() => Array.from({ length: widthParts.value }, (_, i) => ({
  label: t("devices.rack_pos_nth", { n: i + 1 }), value: i + 1,
})));
// 層內的「佔高 + 第幾格」—— 與佔寬同一套換算（只是軸換成垂直）。層架一層放得下疊起來
// 的兩三台，而且不一定放滿；機櫃沒有這個概念，所以只在層架類顯示。
const heightParts = ref<number>(1);
const heightPos = ref<number>(1);
const rackHeightOpts = computed(() => [
  { label: t("devices.rack_height_full"), value: 1 },
  ...WIDTH_PARTS.filter((n) => n > 1).map((n) => ({ label: t("devices.rack_width_nth", { n }), value: n })),
]);
const heightPosOpts = computed(() => Array.from({ length: heightParts.value }, (_, i) => ({
  label: t("devices.rack_vpos_nth", { n: i + 1 }), value: i + 1,
})));

// 主要 IP 選擇：搜尋走後端（設了會雙向連結，IP 清單/拓樸接得起來）。
// 原本一次載 500 筆再由前端過濾，超過 500 個位址的站台會「有這個 IP 卻選不到、
// 打關鍵字也找不到」（GitHub issue #27）。
const { options: ipOptions, loading: ipLoading, search: searchIps,
        onSearch: onIpSearch, ensure: ensureIp } = useIpOptions();
async function loadAddresses() {
  if (!ipOptions.value.length) await searchIps();
}

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

/** 分頁抓到完（上限 MAX_ROWS）。清單只抓第一頁的話，排序落在後面的裝置會整台消失。 */
async function fetchDevices(q?: string): Promise<{ items: Device[]; total: number }> {
  const all: Device[] = [];
  const big = 500;   // 後端 page_size 上限
  let total = 0;
  for (let p = 1; ; p += 1) {
    const res = await listDevices({ page: p, pageSize: big, q, subnetId: subnetFilter.value });
    total = res.total;
    all.push(...res.items);
    if (res.items.length === 0 || all.length >= res.total || all.length >= MAX_ROWS) break;
  }
  return { items: all, total };
}

async function refresh() {
  loading.value = true;
  try {
    const [d, l, rk] = await Promise.all([
      fetchDevices(filterQ.value.trim() || undefined),
      listLocations(),
      listRacks(),
    ]);
    rows.value = d.items;
    totalOnServer.value = d.total;
    locations.value = l.items;
    racks.value = rk.items;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

// 匯出全部：分頁抓完整裝置清單（清單畫面預設只載前 200 筆）
async function fetchAllForExport(): Promise<Device[]> {
  const all: Device[] = [];
  const big = 500;   // 後端 page_size 上限
  let p = 1;
  for (;;) {
    const res = await listDevices({ page: p, pageSize: big, subnetId: subnetFilter.value });
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
    u_position: null, u_size: null, rack_face: null, rack_slot: 0, rack_slot_span: RACK_SLOTS, customer_id: null, primary_ip_id: null,
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
    rack_slot: (r as any).rack_slot ?? 0,
    rack_slot_span: (r as any).rack_slot_span ?? RACK_SLOTS,
    customer_id: r.customer_id ?? null,
    primary_ip_id: (r as any).primary_ip_id ?? null,
  };
  // 既有資料還原成介面上的「寬度 + 第幾格」
  widthParts.value = partsFor((r as any).rack_slot_span);
  widthPos.value = posFor((r as any).rack_slot, widthParts.value);
  heightParts.value = partsFor((r as any).rack_vslot_span);
  heightPos.value = posFor((r as any).rack_vslot, heightParts.value);
  void ensureCustomersLoaded();
  // 先載第一批，再確保「目前這台的主要 IP」也在選項裡 —— 搜尋改走後端之後，
  // 那筆若不在第一批結果內，下拉會顯示空白（看起來像資料掉了）。
  void loadAddresses().then(() => ensureIp(form.value.primary_ip_id));
  show.value = true;
}

// 切 location 時清掉 rack(避免選到別 location 的 rack)
function onLocationChange() {
  const rackStillValid = racks.value.find((r) => r.id === form.value.rack_id)?.location_id === form.value.location_id;
  if (!rackStillValid) { form.value.rack_id = null; uPickerDiagram.value = null; }
}
function onRackChange(rackId: string | null) {
  uPickerDiagram.value = null;
  // 選了機櫃就把地點帶出來 —— 機櫃本來就屬於某個地點，不該再要求使用者選一次。
  const rack = racks.value.find((r) => r.id === rackId);
  if (rack?.location_id) form.value.location_id = rack.location_id;
}

// ── 迷你機櫃 U 位挑選器 ──
const showUPicker = ref(false);
const uPickerDiagram = ref<RackDiagram | null>(null);
const uPickerLoading = ref(false);
// 每個 U 的左/右半占用（full 裝置占兩半）。半 U 裝置只占一半，另一半仍可放。
/** 每個 U 的逐格占用：slots[i] = 佔住第 i 格的裝置名稱（null = 空）。 */
/**
 * 每一列上已經有誰，連**佔哪一塊**一起記（橫向 h、層內垂直 v 兩個區間）。
 *
 * 以前只記橫向、而且是逐格塗名字：層架上「只佔下半層」的裝置會被當成整層都滿，
 * 同一層想再放一台就選不到那一列。判斷改成二維區間相交，與後端的重疊規則一致。
 */
interface Occupant { name: string; h0: number; h1: number; v0: number; v1: number }
const uHalf = computed<Record<number, Occupant[]>>(() => {
  const m: Record<number, Occupant[]> = {};
  for (const d of uPickerDiagram.value?.devices ?? []) {
    if (editing.value && d.device_id === editing.value.id) continue;  // 編輯中的自己不算占用
    const h0 = Number((d as any).rack_slot ?? 0);
    const h1 = h0 + Number((d as any).rack_slot_span ?? RACK_SLOTS);
    const v0 = Number((d as any).rack_vslot ?? 0);
    const v1 = v0 + Number((d as any).rack_vslot_span ?? RACK_SLOTS);
    for (let u = d.u_position; u < d.u_position + d.u_size; u++)
      (m[u] ??= []).push({ name: d.name, h0, h1, v0, v1 });
  }
  return m;
});
/** 這台「將要佔的那一塊」——橫向依佔寬、垂直依佔高（機櫃沒有佔高＝整格）。 */
function wantBox() {
  const h0 = slotFor(widthParts.value, widthPos.value);
  const v0 = rackUsesLevels.value ? slotFor(heightParts.value, heightPos.value) : 0;
  return {
    h0, h1: h0 + spanFor(widthParts.value),
    v0, v1: v0 + (rackUsesLevels.value ? spanFor(heightParts.value) : RACK_SLOTS),
  };
}
// 此列對「目前要放的占寬 + 佔高」是否可選：兩個方向都相交才算撞到
function uPickable(u: number): boolean {
  const occ = uHalf.value[u];
  if (!occ || !occ.length) return true;
  const w = wantBox();
  return !occ.some((o) => w.h0 < o.h1 && o.h0 < w.h1 && w.v0 < o.v1 && o.v0 < w.v1);
}
/** 小地圖上的一塊；百分比換算與「垂直由下往上」都交給 slotBoxPct，兩支表單共用同一份。 */
function blkStyle(o: { h0: number; h1: number; v0: number; v1: number }): Record<string, string> {
  const b = slotBoxPct(o);
  return { left: `${b.left}%`, width: `${b.width}%`, bottom: `${b.bottom}%`, height: `${b.height}%` };
}
/** 「nas2（右半）」—— 只列名字的話，同一層放兩台就分不出誰在左誰在右。 */
function occupantText(o: Occupant): string {
  const where = slotWhere(o).map((d) => {
    if (d.parts === 2) {
      return d.axis === "h" ? t(d.pos === 1 ? "devices.pos_left" : "devices.pos_right")
                            : t(d.pos === 1 ? "devices.pos_lower" : "devices.pos_upper");
    }
    return t(d.axis === "h" ? "devices.pos_of_h" : "devices.pos_of_v",
             { n: d.parts, k: d.pos });
  });
  return where.length ? t("devices.occupant_at", { name: o.name, where: where.join("·") }) : o.name;
}
function uCellText(u: number): string {
  const occ = uHalf.value[u];
  if (!occ || !occ.length) return t("devices.u_free");
  // 由左而右、同一格由上而下 —— 照畫面上的順序唸，才對得起來
  const ordered = occ.slice().sort((a, b) => a.h0 - b.h0 || b.v0 - a.v0);
  return Array.from(new Set(ordered.map(occupantText))).join("、");
}
const uRows = computed(() => rackPickRows(uPickerDiagram.value as any));
/** 列首的字：開放頂多出來的那一列標「頂」，其餘標層號／U 號。 */
function uRowLabel(u: number): string {
  return rackRowIsTop(uPickerDiagram.value as any, u) ? t("racks.level_top") : String(u);
}
async function openUPicker() {
  if (!form.value.rack_id) return;
  uPickerLoading.value = true;
  showUPicker.value = true;
  try { uPickerDiagram.value = await getRackDiagram(form.value.rack_id); }
  catch (e) { msg.error(apiErrMsg(e)); }
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
  // 機櫃本身就掛在地點上 —— 能推的就別叫使用者再講一次（見 utils/rackLocation）
  const loc = resolveRackLocation(form.value.rack_id, form.value.location_id, racks.value);
  if (!loc.ok) { msg.error(t("devices.error_location_mismatch")); return; }
  form.value.location_id = loc.location_id;
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
      rack_slot: form.value.rack_id ? slotFor(widthParts.value, widthPos.value) : 0,
      rack_slot_span: form.value.rack_id ? spanFor(widthParts.value) : RACK_SLOTS,
      // 層架才有層內位置；機櫃一律整層佔滿，與改版前行為相同
      rack_vslot: (form.value.rack_id && rackUsesLevels.value)
        ? slotFor(heightParts.value, heightPos.value) : 0,
      rack_vslot_span: (form.value.rack_id && rackUsesLevels.value)
        ? spanFor(heightParts.value) : RACK_SLOTS,
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

// 依子網路篩選裝置：「這個網段要停電維護，會影響哪些機器」是每次維護前都要問的事。
// 篩選在**後端**做（EXISTS on ip_addresses），不是把整份清單抓回來再過濾 ——
// 這個專案已經因為「只載第一頁再前端過濾」讓客戶看不到自己的裝置一次。
const subnetFilter = ref<string | null>(null);
const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const rows = await listSubnets({ pageSize: 500 });
    const items = Array.isArray(rows) ? rows : (rows as any).items ?? [];
    subnetOptions.value = items.map((x: any) => ({
      label: x.description ? `${x.cidr}（${x.description}）` : x.cidr, value: x.id }));
  } catch { /* 沒權限就不顯示選項，篩選仍可留空 */ }
}

const { visibleKeys, setVisible, reset, order, setOrder, orderColumns } = useColumnPrefs(
  "devices",
  // 全部可選欄位 / 預設顯示的欄位。**新增欄位要兩份都加** —— 只加到 catCols 的話，
  // 欄位存在卻不在預設清單裡，使用者得自己去「欄位」勾才看得到（真實瀏覽器巡檢抓到）。
  ["name", "ip", "fqdn", "type", "is_virtual", "vendor", "model", "location_id", "rack_id",
   "customer_id", "actions"],
  ["name", "ip", "type", "is_virtual", "vendor", "model", "location_id", "rack_id",
   "customer_id", "actions"],
);
const columnPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "ip", label: "IP" },
  { key: "fqdn", label: "FQDN" },
  { key: "type", label: t("cols.type") },
  { key: "is_virtual", label: t("devices.virtuality") },
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
    // 主欄位要有最小寬度：沒設時固定 scroll-x 下它分到的最少，手機上「sw-demo-core」這種名稱會被斷成三行
    title: t("common.name"), key: "name", width: 160,
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
  withExportValue({
    // 給足寬度：最長的標籤是「無線基地台 (AP)」，沒有寬度時會溢出、壓到隔壁的「虛實」
    title: t("devices.type"), key: "type", width: 148,
    render: (r: Device) => h(NTag, { size: "small", type: "info" }, () => t(`devices.type_${r.type}`)),
    sorter: (a: Device, b: Device) => a.type.localeCompare(b.type),
  // 匯出寫顯示文字（匯入認得三種語言的文字，也認得代碼）
  }, (r: Device) => t(`devices.type_${r.type}`)),
  withExportValue({
    // 虛擬 / 實體：同步進來的虛擬機在清單上與實體機長得一模一樣，
    // 分不出來的話，「這台可以斷電維護嗎」這種問題就得逐台去查。
    title: t("devices.virtuality"), key: "is_virtual", width: 92,
    render: (r: Device) => h(NTag, { size: "small", type: r.is_virtual ? "warning" : "default",
                             bordered: false },
      () => t(r.is_virtual ? "devices.virtual" : "devices.physical")),
    sorter: (a: Device, b: Device) => Number(!!a.is_virtual) - Number(!!b.is_virtual),
  }, (r: Device) => t(r.is_virtual ? "devices.virtual" : "devices.physical")),
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
  withExportValue({
    title: t("devices.location"), key: "location_id",
    render: (r) => links.location(r.location_id, locations.value.find((l) => l.id === r.location_id)?.name ?? "—"),
    sorter: (a, b) => {
      const an = locations.value.find((l) => l.id === a.location_id)?.name ?? "";
      const bn = locations.value.find((l) => l.id === b.location_id)?.name ?? "";
      return an.localeCompare(bn);
    },
  // 匯出寫名稱：以前匯出的是內部 UUID，檔案看不懂、也匯不回來（issue #46）
  }, (r: Device) => locations.value.find((l) => l.id === r.location_id)?.name ?? ""),
  withExportValue({
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
  }, (r: Device) => racks.value.find((x) => x.id === r.rack_id)?.name ?? ""),
  withExportValue({
    title: t("nav.customers"), key: "customer_id", width: 160,
    ellipsis: { tooltip: true },
    render: (r) => links.customer(r.customer_id, customerLabelFor(r.customer_id)),
    sorter: (a, b) => customerLabelFor(a.customer_id).localeCompare(customerLabelFor(b.customer_id)),
  }, (r: Device) => (r.customer_id ? customerLabelFor(r.customer_id) : "")),
  {
    // 釘在右側 + 放得下四顆（連結 IP／檢視／編輯／刪除）：欄位一多表格就橫向溢出，
    // 最後一顆會被推到可視範圍外（實機回報「刪除鈕跑出右邊」）。
    title: t("common.actions"), key: "actions", className: "col-actions",
    width: 172, fixed: "right",
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
  orderColumns(allCols.value.filter((c: any) => c.type === "selection" || visibleKeys.value.includes(c.key))),
);

import { useRoute } from "vue-router";
const route = useRoute();
onMounted(async () => {
  void loadSubnetOptions();
  await refresh();
  void ensureCustomersLoaded();
  // 從別處帶關鍵字進來（例如 AI 巡檢的依據資料點裝置名稱、找不到精確裝置時的退路）——
  // 沒有這段的話，連結會把人帶到未篩選的整份清單，等於什麼都沒做
  if (typeof route.query.q === "string" && route.query.q) filterQ.value = route.query.q;
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
      <n-input v-model:value="filterQ" :placeholder="t('devices.search_ph')" clearable
               style="width: 220px" @update:value="onFilterInput" />

        <!-- 尺寸跟著同一列的搜尋框與按鈕走：漏寫 size 會退回元件預設，同一列就出現兩種高度 -->
        <n-select v-model:value="subnetFilter" clearable filterable
                  style="width: 220px" :options="subnetOptions"
                  :placeholder="t('devices.filter_subnet_all')"
                  @update:value="() => refresh()" />
      <!-- 只載入一頁時要明講還有多少沒顯示，不能讓人以為這就是全部 -->
      <n-tag v-if="truncated" size="small" type="warning" :bordered="false">
        {{ t("devices.truncated", { shown: rows.length, total: totalOnServer }) }}
      </n-tag>
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <ColumnPicker :all="columnPickerItems" :visible="visibleKeys"
                    @update:visible="setVisible" @reset="reset"
                    :order="order" @update:order="setOrder" />
      <ExportButton :columns="cols" :rows="rows" :fetch-all="fetchAllForExport"
                    filename="devices" :title="t('nav.devices')" />
      <n-button v-if="_authBtn.me?.is_admin" data-testid="device-import-open" @click="showImport = true">
        <template #icon><n-icon><UploadIcon /></n-icon></template>
        {{ t("device_import.open") }}
      </n-button>
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
      :scroll-x="1180"
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
                    remote :loading="ipLoading" @search="onIpSearch"
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
                      :placeholder="t('devices.rack_placeholder')"
                      style="width: 100%"
                      @update:value="onRackChange" />
          </n-form-item>
        </div>
        <div class="dev-row">
          <n-form-item :label="rackUsesLevels ? t('devices.level_position') : t('devices.u_position')">
            <n-input-group>
              <n-input-number v-model:value="form.u_position" :min="1" :max="99" clearable
                              :disabled="!form.rack_id" style="flex: 1" />
              <n-button :disabled="!form.rack_id" @click="openUPicker"
                      :title="rackUsesLevels ? t('devices.pick_level') : t('devices.pick_u')">
                <template #icon><n-icon><RacksIcon /></n-icon></template>
              </n-button>
            </n-input-group>
          </n-form-item>
          <n-form-item :label="rackUsesLevels ? t('devices.level_size') : t('devices.u_size')">
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
          <n-form-item class="slot-col" :label="t('devices.rack_width')">
            <!-- 兩個下拉併在同一列（順序與編輯視窗一致：先選幾分之一，再選第幾格） -->
            <div class="slot-pair">
              <n-select v-model:value="widthParts" :options="rackSideOpts" @update:value="widthPos = 1"
                        :disabled="!form.rack_id" :consistent-menu-width="false" />
              <n-select v-if="widthParts > 1" v-model:value="widthPos" :options="widthPosOpts"
                        :disabled="!form.rack_id" :consistent-menu-width="false" />
            </div>
          </n-form-item>
          <!-- 層內的上下位置：層架一層放得下疊起來的兩三台，也可以不放滿。 -->
          <n-form-item v-if="rackUsesLevels" class="slot-col" :label="t('devices.rack_height')">
            <div class="slot-pair">
              <n-select v-model:value="heightParts" :options="rackHeightOpts" @update:value="heightPos = 1"
                        :disabled="!form.rack_id" :consistent-menu-width="false" />
              <n-select v-if="heightParts > 1" v-model:value="heightPos" :options="heightPosOpts"
                        :disabled="!form.rack_id" :consistent-menu-width="false" />
            </div>
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
    <n-modal v-model:show="showUPicker" preset="card" style="width: 400px"
             :title="rackUsesLevels ? t('devices.pick_level') : t('devices.pick_u')">
      <n-spin :show="uPickerLoading">
        <p style="font-size:12px; opacity:.65; margin:0 0 8px">{{ rackUsesLevels ? t("devices.pick_level_hint") : t("devices.pick_u_hint") }}</p>
        <div class="upick-rack">
          <div v-for="u in uRows" :key="u" class="upick-row"
               :class="{ occupied: !uPickable(u), cur: form.u_position === u }"
               @click="uPickable(u) && pickU(u)">
            <span class="upick-u">{{ uRowLabel(u) }}</span>
            <span class="upick-map" :title="uCellText(u)">
              <!-- 照實際的佔寬／層內位置畫，才看得出「這一層只被占了一半、旁邊還放得下」 -->
              <span v-for="(o, i) in uHalf[u] ?? []" :key="i" class="upick-blk" :style="blkStyle(o)" />
              <span v-if="uPickable(u)" class="upick-want" :style="blkStyle(wantBox())" />
            </span>
            <span class="upick-body">{{ uCellText(u) }}</span>
          </div>
        </div>
      </n-spin>
    </n-modal>
    <DeviceImportModal v-model:show="showImport" @queued="onImportQueued" />
  </n-card>
</template>

<style scoped>
/* 佔寬的「幾分之一 + 第幾格」要併在同一列 */
.slot-pair { display: flex; gap: 6px; width: 100%; }
/* 左邊只放「1/2」這種短字串，右邊要放「第 3 格（由下往上）」——
   對半分會把右邊擠成「第 …」，所以左邊給固定窄寬、剩下都給右邊。 */
.slot-pair > *:first-child { flex: 0 0 76px; min-width: 0; }
.slot-pair > *:last-child { flex: 1 1 auto; min-width: 0; }

/* 地點/機櫃、U位/佔用U數：兩欄等寬 */
.dev-row { display: flex; gap: 12px; }
.dev-row > * { flex: 1 1 0; min-width: 0; }
/* 「佔寬／佔高」欄位裡是兩個下拉併排，需要的寬度比單一下拉多；平均分會把右邊那個
   擠成「第 …」。連同下拉選單的 consistent-menu-width=false，兩邊都看得到全文。 */
.dev-row > .slot-col { flex: 1.6 1 0; }
/* 迷你機櫃 U 位挑選 */
.upick-rack { border: 1px solid var(--n-border-color, rgba(127,127,127,.25)); border-radius: 8px; overflow: hidden; max-height: 60vh; overflow-y: auto; }
.upick-row { display: flex; align-items: center; gap: 8px; height: 26px; padding: 0 8px; font-size: 12px; border-bottom: 1px dashed rgba(127,127,127,.18); cursor: pointer; }
.upick-row:last-child { border-bottom: none; }
.upick-u { width: 28px; text-align: right; opacity: .55; font-variant-numeric: tabular-nums; }
.upick-body { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
              color: var(--n-text-color-3, #888); }
/* 這一層的小地圖：整條＝一整層，塊＝已經占住的那一塊，虛線＝目前這台會放進去的位置。
   只列名字看不出「一層只被占了一半」，而那正是層架跟機櫃最大的差別。 */
.upick-map { position: relative; flex: 0 0 84px; height: 18px; border-radius: 4px;
             background: rgba(127,127,127,.10); overflow: hidden; }
.upick-blk { position: absolute; border-radius: 2px; background: rgba(127,127,127,.45); }
.upick-want { position: absolute; border: 1px dashed rgba(24,160,88,.95);
              background: rgba(24,160,88,.18); border-radius: 2px; }
.upick-row:not(.occupied):hover { background: rgba(24,160,88,.12); }
.upick-row:not(.occupied):hover .upick-body { color: var(--primary-color, #18a058); font-weight: 600; }
.upick-row.occupied { cursor: not-allowed; background: rgba(127,127,127,.12); }
.upick-row.occupied .upick-body { color: var(--n-text-color-2, #555); font-weight: 600; }
.upick-row.cur { box-shadow: inset 3px 0 0 var(--primary-color, #18a058); }
</style>
