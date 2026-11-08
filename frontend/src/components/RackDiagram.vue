<script setup lang="ts">
/**
 * 機櫃 U 位視覺化 (phpIPAM 招牌功能)。
 *
 * 比 phpIPAM 改進：
 *  - 顏色按 device type 區分 (router/switch/firewall/server/...)
 *  - 越界 / 重疊衝突明顯標示
 *  - 點 device 跳詳細資料
 *  - U 編號從上到下標示，符合機房現場認知
 */
import { computed, ref, watch, onMounted, onBeforeUnmount } from "vue";
import { RACK_SLOTS, usesLevels, rackPixelHeight, slotNotation, boardList } from "@/utils/rackSlots";
import { finishColors, keyholeTile, PLY_COLOR, ANGLE_HOLE_PITCH_PX, ANGLE_PLY_PX } from "@/utils/rackFinish";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NCard, NEmpty, NAlert, NSpace, NTooltip, NButton, NButtonGroup, NIcon, NDropdown, NSlider, NPopover } from "naive-ui";
import { otherFaceHints, type FaceHint } from "@/utils/rackOtherFace";
import { groupBrackets, type GroupBracket, type RackGroupInfo } from "@/utils/rackGroups";
import type { RackDiagram } from "@/api/racks";
import { rackTypeFill as colorFor, RACK_DEVICE_TYPES } from "@/utils/rackColors";
import { exportRacksDrawio, exportRacksPng, exportRacksSvg,
  type ExportDiagram } from "@/utils/rackGraphicsExport";
import { exportTable, type ExportColumn } from "@/utils/tableExport";
import { ExportIcon, GroupIcon, RacksIcon, SyncIcon } from "@/icons";
import { useExportBusy } from "@/composables/useExportBusy";
import { getRackNameAlign, type RackNameAlign } from "@/api/basic";

// 全域設定：機櫃中裝置名稱靠左/置中/靠右（管理員在系統設定調整）
const nameAlign = ref<RackNameAlign>("left");
onMounted(() => { void getRackNameAlign().then((a) => { nameAlign.value = a; }); });
const nameJustify = computed(() =>
  nameAlign.value === "center" ? "center" : nameAlign.value === "right" ? "flex-end" : "flex-start");

const { t } = useI18n();

// 匯出的幾何與跳脫都搬到 utils/rackGraphicsExport.ts —— 單櫃與整排共用同一份，
// 才不會像之前那樣只有畫面跟上層架改版、匯出留在舊模型。

const pct = (n: number) => `${(n / RACK_SLOTS) * 100}%`;
// 機架型態（issue #30）。層架類的列叫「層」不叫 U，外觀也各自不同。
const rackKind = computed(() => (props.diagram as any)?.kind || "rack");
const isShelf = computed(() => usesLevels(rackKind.value));
const isWire = computed(() => rackKind.value === "wire_shelf");
const isIndustrial = computed(() => rackKind.value === "industrial");
const isWood = computed(() => rackKind.value === "wood_shelf");
// 三種新型態：角鋼層架（L 型立柱＋鋼橫桿＋夾板）、KALLAX 格子櫃、LackRack（LACK 邊桌當機櫃）
const isAngle = computed(() => rackKind.value === "angle_shelf");
const isKallax = computed(() => rackKind.value === "kallax");
const isLack = computed(() => rackKind.value === "lackrack");
/** 表面顏色換成 CSS 變數（配色與匯出、嵌入圖共用 utils/rackFinish.ts 那一份） */
const finishVars = computed<Record<string, string>>(() => {
  const c = finishColors(rackKind.value, (props.diagram as any)?.finish);
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(c)) out[`--fin-${k}`] = v;
  if (isAngle.value) {
    out["--ply"] = PLY_COLOR;
    out["--rd-ply"] = ANGLE_PLY_PX + "px";
    out["--rd-keyholes"] = keyholeTile(c.hole ?? "rgba(0,0,0,0.4)", sidePx.value);
    out["--rd-hole-pitch"] = ANGLE_HOLE_PITCH_PX + "px";
  }
  return out;
});
/**
 * 木質層架（IKEA IVAR）背面的 OBSERVATÖR 支撐桿：**一根**斜桿，不是 X。
 * 它是固定 100 公分的鋼條，所以跨幾層由層架寬度決定 —— 跨距由後端算好
 * （`brace_levels`），前端只負責畫在最底下那一段。
 */
const braceLevels = computed(() => (props.diagram as any)?.brace_levels ?? 0);
const braceStyle = computed(() => {
  const n = props.diagram?.u_height ?? 0;
  const lv = Math.min(braceLevels.value, n);
  if (!isWood.value || lv <= 0) return null;
  // 支撐桿在最底下那幾層。畫面由上往下依 cells 的順序排，所以直接把 cells 的高度
  // 加總 —— 各層高度可能不同，不能用「層數 × 列高」。
  const px = cells.value.map((c) => rowPxOf(c.u));
  const bottom = props.diagram?.numbering === "bottom-up";
  // bottom-up 時第 1 層畫在最上面，所以「最底下那幾層」在畫面上是最前面幾格
  const span = bottom ? px.slice(0, lv) : px.slice(px.length - lv);
  const before = bottom ? 0 : px.slice(0, px.length - lv).reduce((a, b) => a + b, 0);
  return {
    top: before + "px",
    height: span.reduce((a, b) => a + b, 0) + "px",
  };
});
/** 這一格在使用者語彙裡叫什麼：層架是「第 3 層」／最上面那列是「頂」，機櫃是「U3」。
 *  原本寫死 `Empty (U3)` —— 對層架是錯的字，而且是唯一沒進翻譯的一段。 */
function cellPosLabel(c: { u: number; isTop: boolean }): string {
  if (c.isTop) return t("racks.level_top");
  return isShelf.value ? t("racks.level_at", { n: c.u }) : t("racks.level_at_u", { n: c.u });
}
/** 「16U」或「4 層」—— 標題與匯出都叫這支，兩邊不會各寫各的。 */
function rowsText(n: number, kind?: string | null): string {
  return usesLevels(kind) ? t("racks.rows_levels", { n }) : `${n}U`;
}
const rowsLabel = computed(() => rowsText(props.diagram?.u_height ?? 0, rackKind.value));
// 層架不是機櫃 —— 標題寫「機櫃：」會讓人以為型態沒存到。標點交給各語系（中日文全形）。
const cardTitle = computed(() => t("racks.card_title", {
  kind: usesLevels(rackKind.value) ? t("racks.card_shelf") : t("racks.card_rack"),   // 單數（英文曾顯示「Racks: TYO-R01」）
  name: props.diagram?.name ?? "", rows: rowsLabel.value,
}));

/**
 * 顯示大小。高機櫃／多層層架在一個螢幕塞不下時用這個縮小。
 *
 * **不是改繪圖尺寸**：把列高壓小會直接毀掉比例（層架的高矮層會被夾成一樣高），
 * 所以走 CSS transform —— 連字一起等比縮，比例完全不動。
 */
/** 整排並列時不顯示逐櫃工具列：上方已經有共用的一排，而且工具列的寬度會讓卡片
 *  收不進來（窄機櫃旁邊留一大片空白）。 */
const showControls = computed(() => props.controls && !props.floorAlignTo);
const ZOOM_MIN = 0.35;
/**
 * 手機的預設比例依畫面算，不沿用桌機的 100%（使用者回報手機上太大）：取「寬度放得下、整座
 * 高度一個畫面看得完」的比例，上限 45%、下限 35%（42U 會落在 35%）。拉過拉桿就記住，
 * 跟桌機分開記 —— 桌機習慣的大小搬到手機上通常不合用。
 */
const MOBILE_ZOOM_MAX = 0.45;
const narrow = typeof window !== "undefined" && !!window.matchMedia?.("(max-width: 767px)").matches;
const ZOOM_KEY = narrow ? "jt.rackZoom.mobile" : "jt.rackZoom";
const zoom = ref(1);
let zoomFromUser = false;
let zoomAutoSetting = false;
try {
  const v = Number(localStorage.getItem(ZOOM_KEY));
  if (v >= ZOOM_MIN && v <= 1) { zoom.value = v; zoomFromUser = true; }
} catch { /* 隱私模式讀不到就用預設 */ }
// flush: sync —— 自動套用時要能分辨「程式設的」與「使用者拉的」，非同步的 watch 會錯過旗標
watch(zoom, (v: number) => {
  if (zoomAutoSetting) return;
  zoomFromUser = true;
  try { localStorage.setItem(ZOOM_KEY, String(v)); } catch { /* 忽略 */ }
}, { flush: "sync" });

/**
 * 一格裡的垂直定位。**只能在這裡算**：層內疊放要用行內 style 設 bottom/height，
 * 而行內樣式會蓋掉 CSS —— 之前用 CSS 補的「跨多 U 中間不要有分隔線」就是這樣被蓋掉的。
 *
 * - 有層內位置（層架疊放）→ 由下往上定位
 * - 沒有、且這格不是這台的最底格 → 往下多長 1px，蓋掉 .u-row 的分隔線，
 *   否則 3U 會被看成三台 1U（層架類不做：那條是層板，本來就該看得到）
 */
function partVStyle(p: DevPart): Record<string, string> {
  if (p.vslot || p.vspan < RACK_SLOTS) {
    return { top: "auto", bottom: pct(p.vslot), height: pct(p.vspan) };
  }
  if (!p.is_bottom && !isShelf.value) {
    return { top: "0", bottom: "auto", height: "calc(100% + 1px)" };
  }
  return { top: "0", bottom: "0", height: "auto" };
}

/**
 * 畫出來的列高與寬度。**只有這裡算**：以前是行內 style 設一個值、`.rd-compact` 的 CSS
 * 再設另一個值，而行內贏 CSS —— 結果縮圖的格子被 CSS 壓成 18px、左邊的層號卻吃行內的
 * 160px，號碼就掉到整頁下面去了。縮圖要變小就在這裡變，不要兩邊各設一次。
 */
/**
 * 每一層的高度 px，**由第 1 層起算**（不是畫面由上往下）。層架的層板一層一層可調，
 * 所以這是一個陣列；後端沒給（舊版）就用均一值補滿，畫出來與以前一樣。
 */
const rowPxList = computed<number[]>(() => {
  const n = props.diagram?.u_height ?? 0;
  const base = props.diagram?.render_row_px ?? 28;
  const raw = ((props.diagram as any)?.render_row_px_list ?? []) as number[];
  const list = raw.slice(0, n).map((v) => Number(v) || base);
  while (list.length < n) list.push(base);
  // 縮圖**不在這裡**壓扁。以前只縮列高，寬度、層板厚度、立柱寬、調整孔間距全都維持
  // 原尺寸 —— 層板變成很粗的橫條、一層只剩一個孔，側架整個走樣（客戶回報）。
  // 改成整張等比縮放（見 fitZoom），這裡只給自然尺寸。
  return list;
});
/** 層板畫出來多厚 px。層高填的是淨空高，板厚另外占位置。 */
const boardPx = computed(() => Number((props.diagram as any)?.render_board_px ?? 0) || 0);
/** 最上面那一列之上的厚度 px（LackRack 的桌面） */
const topPx = computed(() => Number((props.diagram as any)?.render_top_px ?? 0) || 0);
/** 層架的最上面那片板**上面**也放得了東西 → 多一列可放的位置。 */
const openTop = computed(() => Boolean((props.diagram as any)?.open_top));
/** 最下層板離地多高 px —— 立柱要往下長到地面，否則層架看起來像被齊平切掉。 */
const floorPx = computed(() => Number((props.diagram as any)?.render_floor_px ?? 0) || 0);
/** 第 u 層的淨空高度 px（u 是層號，1 起算）。超出的是「頂板上方」，比照最高層。 */
const rowPxOf = (u: number) =>
  rowPxList.value[u - 1] ?? rowPxList.value[rowPxList.value.length - 1] ?? 28;
/** 給 CSS 當退路用的代表值（層高均一時就是它）。 */
const rowPx = computed(() => rowPxList.value[0] ?? 28);
/**
 * 每一層邊界在畫面上的 y（含最上與最下）。鍍鉻層架的套環要裝在每一片層板的位置，
 * 層高可以一層一層不同，所以不能用「固定間距的重複漸層」去畫。
 */
/**
 * 立柱／側架的起點（距離框頂多少 px）。立柱只到**最上面那片層板**為止 —— 頂板上面是
 * 開放的，東西就放在那裡，柱子不會再往上長。沒有開放頂端時就從最上緣起算。
 */
const postTop = computed(() => {
  if (!openTop.value || !cells.value.length) return 0;
  // 減掉板厚：cells[0].px 是「頂板上方那一列」的高度（含它下緣的那片板），
  // 從板底起算會讓 22px 厚的板整片凸出在柱子之上。柱子要從板的**頂端**開始。
  return Math.max(0, cells.value[0].px - cells.value[0].board);
});
const boundaryTops = computed<number[]>(() => {
  const out = [-3];
  let acc = 0;
  for (const c of cells.value) { acc += c.px; out.push(acc - 3); }
  // 開放頂端時最上面那個邊界在立柱**之上**（那裡沒有層板也沒有柱子），
  // 不跳掉就會有一圈套環浮在半空中。與層板的處理一致。
  return openTop.value ? out.slice(1) : out;
});
// 縮圖不在這裡限寬：以前 compact 把寬度壓到 300、高度不動，寬的層架（KALLAX 2×2、90 公分鍍鉻層架）
// 就被擠成瘦長形。寬度上限改由 fitZoom 整張等比縮。
const colPx = computed(() => props.diagram?.render_width_px ?? 250);
// 機櫃兩側的走線空間（19 吋設備區以外、外寬多出來的部分）：600mm 每側約 35px、800mm 約 87px。
// KALLAX／角鋼層架／LackRack 借這個值畫兩側的外框／立柱／桌腳；其他層架是 0。
// 後端算好，畫面與匯出吃同一個值。
const sidePx = computed(() => {
  const v = Number((props.diagram as any)?.render_side_px ?? 0) || 0;
  return isShelf.value && !isKallax.value && !isAngle.value ? 0 : v;
});
/**
 * LackRack 的桌腳與桌面（y 從第一列的頂端起算）。跟匯出、嵌入圖一樣畫成有輪廓線的方塊：
 * 以前用一疊背景漸層拼，桌面與腳底都沒有邊，白色桌子在白色卡片上幾乎看不見，
 * 最下面那截桌腳也像是突出去（使用者回報）。每一片板的最下面那一截就是桌面，
 * 疊起來的桌子之間那片板上面多出來的是上面那張的腳下空隙。
 */
const lackParts = computed(() => {
  if (!isLack.value || !cells.value.length) return null;
  const slab = cells.value[0].board;
  const tops: number[] = [];
  let acc = 0;
  for (const c of cells.value) {
    acc += c.px;
    if (c.board > 0) tops.push(acc - slab);
  }
  return tops.length ? { slab, tops, legTop: tops[0] } : null;
});
/** KALLAX 直的內隔板：每一條的位置（占設備區寬的比例）、寬 px，以及從哪裡畫到哪裡。
 *  畫在裝置**上面**：資料上的橫向格位是整片寬度切 60 格，隔板不占格位，
 *  蓋在上面才看得出「裝置放在格子裡」。 */
const kallaxDividers = computed(() => {
  if (!isKallax.value || cells.value.length < 2) return null;
  const cols = Math.max(1, Number((props.diagram as any)?.render_cols ?? 1));
  const w = Number((props.diagram as any)?.render_divider_px ?? 0) || 0;
  const first = cells.value[0];
  const last = cells.value[cells.value.length - 1];
  const top = first.px;                       // 頂部那一列（含外框頂板）之下
  const total = cells.value.reduce((a, c) => a + c.px, 0);
  return {
    w, top, height: total - top - last.board,
    at: Array.from({ length: cols - 1 }, (_, i) => (i + 1) / cols),
  };
});

// 共用：產生機櫃 SVG 字串 + 尺寸
// 依橫向格位算寬度，否則同一個 U 的多台裝置會疊在一起
/** 匯出用的機櫃資料：畫面上的文案（「9 層」「頂」）要一起帶給共用的匯出器。
 *
 * 以前這裡自己再寫一份 SVG／draw.io 產生器，結果層架改版只改到畫面那一份 ——
 * 匯出出來的圖沒有層板、層高一律等分、放在頂板上面的裝置整個不見。幾何只留一份。 */
function exportDiagram(): ExportDiagram | null {
  const d = props.diagram as any;
  if (!d) return null;
  return { ...d, rowsLabel: rowsText(d.u_height, d.kind), topLabel: t("racks.level_top") };
}
function exportSvg() {
  const d = exportDiagram();
  if (d) exportRacksSvg([d], 0, nameAlign.value, `rack-${d.name}`);
}
function exportPng() {
  const d = exportDiagram();
  if (d) exportRacksPng([d], 0, nameAlign.value, `rack-${d.name}`);
}
function exportDrawio() {
  const d = exportDiagram();
  if (d) exportRacksDrawio([d], 0, nameAlign.value, `rack-${d.name}`);
}

const exportOptions = computed(() => [
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
// 機櫃裝置清單的資料匯出（csv/xlsx/ods/md/txt）
function exportData(fmt: "csv" | "xlsx" | "ods" | "md" | "txt") {
  const d = props.diagram;
  if (!d) return;
  // 欄位標題要跟著型態走（層架是「層」不是 U），而且同一列可以放好幾台 ——
  // 少了位置欄，匯出檔裡並排的兩台看起來就是重複資料。
  const unit = isShelf.value ? t("racks.levels") : "U";
  const cols: ExportColumn[] = [
    { key: "u_position", label: unit },
    { key: "u_size", label: `${unit} Size` },
    { key: "slot", label: t("devices.rack_width") },
    { key: "name", label: t("cols.name") },
    { key: "type", label: t("cols.type") },
    { key: "rack_face", label: t("racks.face") },
    { key: "primary_ip", label: "IP" },
    { key: "vendor", label: t("cols.vendor") },
    { key: "model", label: t("cols.model") },
  ];
  const rows = [...d.devices]
    .sort((a, b) => (b.u_position ?? 0) - (a.u_position ?? 0)
                 || (a.rack_slot ?? 0) - (b.rack_slot ?? 0))
    .map((dev: any) => ({ ...dev, slot: slotNotation(dev) }));
  exportTable(fmt, `rack-${d.name}`, cols, rows as any, `Rack ${d.name}`);
}
const { exporting, run: runExport } = useExportBusy();
function onExport(key: string) { void runExport(() => doExport(key)); }
function doExport(key: string) {
  if (key === "svg") exportSvg();
  else if (key === "png") exportPng();
  else if (key === "drawio") exportDrawio();
  else if (["csv", "xlsx", "ods", "md", "txt"].includes(key)) exportData(key as any);
}
const router = useRouter();
function goDevice(id: string) {
  // 帶上來源機櫃：裝置頁的「返回」與刪除後的導向要回到**點進來的那一頁**，
  // 不是一律丟回裝置清單。機櫃已不存在時裝置頁會自己退回清單。
  const rack = props.diagram?.rack_id;
  router.push({
    name: "device-detail", params: { id },
    query: rack ? { from: "rack", rack: String(rack) } : undefined,
  });
}

interface Props {
  diagram: RackDiagram | null;
  showLegend?: boolean;   // 多機櫃並排時可關掉，由頁面放一個共用圖例
  editable?: boolean;     // admin：點空 U 位可挑裝置放入
  floorAlignTo?: number;  // 多機櫃並排時傳入該排最高的「畫出來 px 高度」→ 矮的頂端補空白，底部對齊
  highlightId?: string | null;  // 常駐高亮某裝置（裝置詳細資料頁標示本機在機櫃的位置）
  compact?: boolean;            // 較小列高（嵌在裝置詳細資料等空間有限處）
  bare?: boolean;               // 去掉卡片外框與標題（嵌入用）
  face?: "front" | "rear" | null;  // 外部強制指定檢視面（合併卡共用切換用）；null = 用自身切換
  controls?: boolean;              // 是否顯示自身的面切換 + 匯出（合併卡傳 false 改由外層統一）
  sharedZoom?: number | null;      // 外部指定的顯示大小（機房整排共用一條拉桿）；null = 用自身的
  /** compact 並排時（儀表板）傳入該排最高那台的自然高度 px：整排用同一個縮放比例。
   *  不傳就各自縮到放得下 —— 高的被縮、矮的不縮，並排時大小比例就失真了（使用者回報）。 */
  fitTo?: number | null;
}
const props = withDefaults(defineProps<Props>(), { showLegend: true, editable: false, floorAlignTo: 0, highlightId: null, compact: false, bare: false, face: null, controls: true, sharedZoom: null, fitTo: null });
const faceView = ref<"front" | "rear">("front");   // 機櫃正面 / 背面切換
// 實際採用的檢視面：外部有指定就用外部（合併卡共用），否則用自身切換
const effFace = computed(() => props.face ?? faceView.value);
const hasRear = computed(() => (props.diagram?.devices || []).some((d: any) => d.rack_face === "rear"));
/**
 * 這張圖實際畫出來多高。
 *
 * `rackPixelHeight()` 只加總「各層高度＋層板」，**沒有**外框的 padding、邊框，也沒有
 * 立柱往下突出的腳 —— 拿它當縮放容器的高度會少算，容器一裁就把腳切掉、不裁就壓到下面的
 * 圖例。所以實際量元素的自然高度，量不到（還沒掛載）才退回算的值。
 */
const wrapEl = ref<HTMLElement | null>(null);
const measuredPx = ref(0);
const measuredW = ref(0);
const ownPx = computed(() => measuredPx.value || rackPixelHeight(props.diagram as any));
/** 未縮放的自然寬度。縮放後**版面寬度不會自己縮**，卡片會停在原寬度、右邊空一塊。 */
const ownW = computed(() => measuredW.value || (colPx.value + 2 * sidePx.value + 40));
onMounted(() => {
  if (!wrapEl.value || typeof ResizeObserver === "undefined") return;
  const ro = new ResizeObserver(() => {
    // 量的是**未縮放**的自然尺寸：transform 不影響 offsetWidth / offsetHeight
    if (!wrapEl.value) return;
    measuredPx.value = wrapEl.value.offsetHeight;
    // 寬度要**無條件進位**：offsetWidth 會四捨五入，KALLAX 實際 428.28px 卻量成 428，外層就窄了
    // 0.28px → 「一律顯示捲軸」的 Mac 上每個 KALLAX 底下都多一條橫向捲軸（使用者回報）
    const scale = effZoom.value || 1;
    measuredW.value = Math.max(wrapEl.value.offsetWidth,
                               Math.ceil(wrapEl.value.getBoundingClientRect().width / scale - 0.01));
    if (props.diagram) emit("measured", props.diagram.rack_id, measuredPx.value);
    autoFitMobile();
  });
  ro.observe(wrapEl.value);
  onBeforeUnmount(() => ro.disconnect());
});
const scrollEl = ref<HTMLElement | null>(null);
function autoFitMobile() {
  if (!narrow || zoomFromUser || !showControls.value || props.sharedZoom != null || props.compact) return;
  const natW = measuredW.value;
  const natH = measuredPx.value;
  const availW = scrollEl.value?.clientWidth || 0;
  if (!natW || !natH || !availW) return;
  const availH = Math.max(240, window.innerHeight - 170);   // 扣掉頂列、工具列與卡片標題
  const fit = Math.floor(Math.min(availW / natW, availH / natH) * 20) / 20;
  const z = Math.min(MOBILE_ZOOM_MAX, Math.max(ZOOM_MIN, fit));
  if (z === zoom.value) return;
  zoomAutoSetting = true;
  zoom.value = z;
  zoomAutoSetting = false;
}
/**
 * 落地對齊：比該排最高的那台矮多少，就在上面補多少空白。
 *
 * 兩個踩過的坑：
 * 1. 用 **U 數**算 → 20U 機櫃列高 28px、10 層層架近 100px，補白會把層架推到畫面外。
 * 2. 補白加在**被縮放的元素上** → 會跟著一起縮，縮小後就對不齊。
 * 所以：用 px 算，而且加在縮放容器的外層（見模板的 rack-zoom margin-top）。
 */
/** 縮圖放得下的高度（px）。超過就整張等比縮小 —— 比例不動，字級再由 --rd-fit 補回來。 */
const COMPACT_MAX_PX = 460;
/** 單獨一張縮圖（裝置詳細資料側欄）放得下的寬度：超過也是整張等比縮，不壓扁 */
const COMPACT_MAX_W = 300;
const fitZoom = computed(() => {
  if (!props.compact) return 1;
  if (props.fitTo) return props.fitTo > COMPACT_MAX_PX ? COMPACT_MAX_PX / props.fitTo : 1;
  const nat = ownPx.value || rackPixelHeight(props.diagram as any) || 1;
  const byH = nat > COMPACT_MAX_PX ? COMPACT_MAX_PX / nat : 1;
  return colPx.value > COMPACT_MAX_W ? Math.min(byH, COMPACT_MAX_W / colPx.value) : byH;
});
/** 真正套上去的縮放：縮圖先縮到放得下，再乘上使用者拉的那一段。 */
const effZoom = computed(() => fitZoom.value * (props.sharedZoom ?? zoom.value));

const floorPad = computed(() =>
  Math.max(0, (props.floorAlignTo || 0) - ownPx.value));
const emit = defineEmits<{
  (e: "pick-empty", u: number, rackId: string, slot?: number): void;
  /** 量到的自然高度（px）。並排落地對齊的基準必須用**量出來的**值 —— 由資料推算的高度
   *  少了外框的 border/padding，而那是逐型態不同的，於是最高的那一台會比別人低個幾 px。 */
  (e: "measured", rackId: string, px: number): void;
  /** 外部控制檢視面時（機房整排），點提示的「切換到背面」要請外層切 */
  (e: "update:face", face: "front" | "rear"): void;
}>();
const hoveredId = ref<string | null>(null);   // hover 某 U → 整台裝置點亮+框線

interface DevPart {
  id: string;
  name: string;
  type: string;
  vendor: string | null;
  model: string | null;
  u_size: number;
  /** 這台在畫面上占幾格（用來讓名稱跨整台置中） */
  run: number;
  /** 這台跨過那幾格的**高度總和** px。層高可以一層一層不同，所以不能用 run × 列高。 */
  runPx: number;
  /** 最下面那一格底下那片板多厚 —— 名稱置中要扣掉它。以前扣的是整台共用的板厚，
   *  KALLAX 的外框、LackRack 的桌面比共用值厚，名稱就被推到板子裡。 */
  runBoard: number;
  /** 層內的垂直位置（0 貼著層板，往上長）與佔幾格 —— 層架一層可以疊放、也可不放滿 */
  vslot: number;
  vspan: number;
  slot: number;        // 起始格（0..RACK_SLOTS-1）
  span: number;        // 跨幾格
  is_top: boolean;     // device 最上格
  is_bottom: boolean;  // device 最下格
  is_mid: boolean;     // device 垂直中間格（顯示名字 → 跨多 U 置中）
  primary_ip: string | null;
  group: RackGroupInfo | null;   // 裝置群組（堆疊／HA／叢集）
}
interface Cell {
  u: number;                       // 1-based, top-most U
  px: number;                      // 這一層畫出來多高（淨空高 + 層板厚度）
  board: number;                   // 這一列底下那片板多厚 px（KALLAX 外框、LackRack 桌面會比較厚）
  isTop: boolean;                  // 是不是「頂板上方」那一列（層架才有）
  parts: DevPart[];                // 這個 U 上的裝置（依起始格排序）
  gaps: { slot: number; span: number }[];   // 沒被佔到的空隙（可點來新增）
}

// 衝突訊息：整理成好讀的繁中句子（不直接吐 JSON）
const conflictLines = computed<string[]>(() => {
  const d = props.diagram;
  if (!d) return [];
  const nameOf = new Map(d.devices.map((s) => [String(s.device_id), s.name]));
  return d.conflicts.map((c: any) => {
    if (c.type === "overlap") {
      const names = (c.device_ids ?? [])
        .map((id: string) => nameOf.get(String(id)) ?? String(id).slice(0, 8)).join("、");
      return t("rack_diagram.conflict_overlap", { u: c.u, names });
    }
    if (c.type === "out_of_bounds") {
      return t("rack_diagram.conflict_oob",
        { name: c.name, u: c.u_position, size: c.u_size, h: c.rack_u_height });
    }
    if (c.type === "unpositioned") {
      return t("rack_diagram.conflict_unpos", { name: c.name });
    }
    return JSON.stringify(c);
  });
});

const cells = computed<Cell[]>(() => {
  if (!props.diagram) return [];
  // 層架多一列：最上面那片板的**上面**（第 u_height + 1 層），標「頂」不標數字
  const u_height = props.diagram.u_height + (openTop.value ? 1 : 0);
  const real = props.diagram.u_height;
  const bottomUp = props.diagram.numbering === "bottom-up";
  const map: Record<number, Cell> = {};
  for (let u = 1; u <= u_height; u++)
    map[u] = { u, px: rowPxOf(u) + boardPx.value, board: boardPx.value, isTop: u > real, parts: [], gaps: [] };
  const mk = (d: any): DevPart => ({
    id: d.device_id, name: d.name, type: d.type, vendor: d.vendor, model: d.model,
    u_size: d.u_size, is_top: false, is_bottom: false, is_mid: false, run: 1, runPx: 0, runBoard: 0,
    slot: Number(d.rack_slot ?? 0), span: Number(d.rack_slot_span ?? RACK_SLOTS),
  vslot: Number(d.rack_vslot ?? 0), vspan: Number(d.rack_vslot_span ?? RACK_SLOTS),
    primary_ip: d.primary_ip, group: d.group ?? null,
  });
  for (const d of props.diagram.devices) {
    // 只顯示目前檢視面（正/背）的裝置；未標面者視為正面
    if (((d.rack_face ?? "front") as string) !== effFace.value) continue;
    for (let u = d.u_position; u < d.u_position + d.u_size; u++) {
      if (!map[u]) continue;
      map[u].parts.push(mk(d));
    }
  }
  // 顯示順序：top-down → 高 U 在上（u_height..1）；bottom-up → U1 在上（1..u_height）
  const order: Cell[] = bottomUp
    ? Array.from({ length: u_height }, (_, i) => map[i + 1])
    : Array.from({ length: u_height }, (_, i) => map[u_height - i]);
  // 「頂板上方」在實體上永遠是**最上面**那一列，跟編號方向無關。bottom-up 時編號由上
  // 往下遞增，照編號排會把它排到最底下 —— 那是地板不是頂板，所以要另外提到最前面。
  if (openTop.value && bottomUp) {
    const i = order.findIndex((c) => c.isTop);
    if (i > 0) order.unshift(...order.splice(i, 1));
  }
  // 每一列底下那片板的厚度照**畫面順序**（由上往下）套上：KALLAX 的頂板、底板是外框，
  // LackRack 每張桌子之間有一片桌面。其他型態每片一樣厚，結果跟以前相同。
  const boards = boardList(props.diagram as any, order.length);
  order.forEach((c, i) => { c.board = boards[i]; c.px = rowPxOf(c.u) + boards[i]; });

  for (const c of order) {
    c.parts.sort((a, b) => a.slot - b.slot);
    // 沒被任何裝置佔到的橫向空隙 → 可點擊新增。逐格掃過去再併成連續區段，
    // 比「左半/右半」那套通用，重疊的資料也不會讓它算爆。
    const taken = new Array(RACK_SLOTS).fill(false);
    for (const p of c.parts)
      for (let i = p.slot; i < Math.min(p.slot + p.span, RACK_SLOTS); i++) taken[i] = true;
    let i = 0;
    while (i < RACK_SLOTS) {
      if (taken[i]) { i++; continue; }
      const from = i;
      while (i < RACK_SLOTS && !taken[i]) i++;
      c.gaps.push({ slot: from, span: i - from });
    }
  }

  // 垂直連續段（跨多 U 的同一台）：**依裝置 id** 判斷，與它佔哪些格無關。
  const runs: Record<string, number[]> = {};
  order.forEach((c, i) => {
    for (const p of c.parts) {
      const prev = order[i - 1]?.parts.some((x) => x.id === p.id);
      const next = order[i + 1]?.parts.some((x) => x.id === p.id);
      p.is_top = !prev;
      p.is_bottom = !next;
      (runs[p.id] ??= []).push(i);
    }
  });
  for (const [id, idxs] of Object.entries(runs)) {
    // 名稱畫在**最上面**那一格，再用絕對定位跨滿整台的高度置中。
    //
    // 原本是挑「中間那一格」來畫（`Math.floor((len-1)/2)`），但偶數 U 沒有正中間的
    // 一格：2U 會取到上面那格，名稱因此偏高半格。跨整台置中就與 U 數的奇偶無關。
    const head = order[idxs[0]].parts.find((x) => x.id === id);
    if (head) head.is_mid = true;
    const runPx = idxs.reduce((a, i) => a + order[i].px, 0);
    const runBoard = order[idxs[idxs.length - 1]].board;
    for (const i of idxs)
      for (const p of order[i].parts) if (p.id === id) { p.run = idxs.length; p.runPx = runPx; p.runBoard = runBoard; }
  }
  return order;
});

/**
 * 另一面的裝置提示（2026-10-21 設計「側邊提示軌＋背面數量」）：看正面時右側提示軌標「背 ×N」，
 * 看背面時反過來標「前 ×N」。固定看得到（不靠滑鼠停留），也不遮住這一面的裝置：
 * 標準機櫃與層架放在框外右側的提示軌；格櫃放在對應的格位，那一格這一面有東西時才退回提示軌。
 * 縮圖（裝置詳細資料、儀表板）不畫：空間不夠，那裡的重點是本機的位置。
 */
interface PlacedHint extends FaceHint { key: string; top: number; height: number; inCell: boolean }
const kallaxCols = computed(() => (isKallax.value ? Math.max(1, Number((props.diagram as any)?.render_cols ?? 1)) : 1));
const otherFace = computed<"front" | "rear">(() => (effFace.value === "front" ? "rear" : "front"));
const faceHints = computed<PlacedHint[]>(() => {
  if (!props.diagram || props.compact) return [];
  const hints = otherFaceHints(props.diagram.devices as any, effFace.value, rackKind.value, kallaxCols.value);
  if (!hints.length) return [];
  // 每一列在畫面上的 y（由上往下累加）
  const pos = new Map<number, { top: number; px: number; board: number; cell: Cell }>();
  let acc = 0;
  for (const c of cells.value) { pos.set(c.u, { top: acc, px: c.px, board: c.board, cell: c }); acc += c.px; }
  const width = RACK_SLOTS / kallaxCols.value;
  const out: PlacedHint[] = [];
  for (const h of hints) {
    const rows = [...pos.entries()].filter(([u]) => u >= h.from && u <= h.to).map(([, v]) => v);
    if (!rows.length) continue;
    const top = Math.min(...rows.map((r) => r.top));
    const bottom = Math.max(...rows.map((r) => r.top + r.px - r.board));
    let inCell = false;
    if (h.col != null) {
      const lo = h.col * width, hi = lo + width;
      inCell = !rows[0].cell.parts.some((p) => p.slot < hi && p.slot + p.span > lo);
    }
    out.push({ ...h, key: `${h.from}-${h.to}-${h.col ?? ""}`, top, height: Math.max(12, bottom - top), inCell });
  }
  return out;
});
const railHints = computed(() => faceHints.value.filter((h) => !h.inCell));
const cellHints = computed(() => faceHints.value.filter((h) => h.inCell));
function hintLabel(h: FaceHint): string {
  return t(effFace.value === "front" ? "racks.rear_hint" : "racks.front_hint", { n: h.devices.length });
}
function hintTitle(h: FaceHint): string {
  const where = isShelf.value
    ? (h.from > (props.diagram?.u_height ?? 0) ? t("racks.level_top") : t("racks.level_at", { n: h.from }))
    : (h.from === h.to ? `U${h.from}` : `U${h.from}–U${h.to}`);
  return t(effFace.value === "front" ? "racks.rear_devices_at" : "racks.front_devices_at", { where });
}
/** 切到另一面並保留位置：版面高度不隨檢視面改變，捲動位置本來就在；被提示的裝置短暫亮起 */
const flashIds = ref<Set<string>>(new Set());
let flashTimer: ReturnType<typeof setTimeout> | null = null;
const openHint = ref<string | null>(null);
function switchToOther(h: FaceHint) {
  openHint.value = null;
  const target = otherFace.value;
  if (props.face == null) faceView.value = target;
  else emit("update:face", target);
  flashIds.value = new Set(h.devices.map((d) => String(d.device_id)));
  if (flashTimer) clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { flashIds.value = new Set(); }, 2400);
}

/**
 * 裝置群組的括線（客戶 2026-11-05：兩台 N2048P 堆疊要標得出來）。框外右側的「群組軌」，
 * 每個群組一條同色括線從最上面的成員拉到最下面的成員，每台成員一個小刻度，標籤寫「名稱 本櫃台數/總台數」。
 * 範圍重疊的群組分到不同軌道。縮圖不畫（同另一面提示）。
 */
interface PlacedGroup extends GroupBracket { top: number; height: number; ticks: number[] }
const GROUP_LANE_PX = 10;
const GROUP_LABEL_PX = 150;
const placedGroups = computed<PlacedGroup[]>(() => {
  if (!props.diagram || props.compact) return [];
  const brackets = groupBrackets(props.diagram.devices as any, effFace.value);
  if (!brackets.length) return [];
  const pos = new Map<number, { top: number; px: number; board: number }>();
  let acc = 0;
  for (const c of cells.value) { pos.set(c.u, { top: acc, px: c.px, board: c.board }); acc += c.px; }
  const span = (lo: number, hi: number) => {
    const rows = [...pos.entries()].filter(([u]) => u >= lo && u <= hi).map(([, v]) => v);
    if (!rows.length) return null;
    const top = Math.min(...rows.map((r) => r.top));
    return { top, bottom: Math.max(...rows.map((r) => r.top + r.px - r.board)) };
  };
  const out: PlacedGroup[] = [];
  for (const b of brackets) {
    const whole = span(b.from, b.to);
    if (!whole) continue;
    const ticks = b.members.map((m) => span(m.u_position, m.u_position + Math.max(1, m.u_size) - 1))
      .filter((x): x is { top: number; bottom: number } => !!x)
      .map((x) => (x.top + x.bottom) / 2 - whole.top);
    out.push({ ...b, top: whole.top, height: Math.max(12, whole.bottom - whole.top), ticks });
  }
  return out;
});
const groupLanes = computed(() => placedGroups.value.reduce((a, g) => Math.max(a, g.lane + 1), 0));
/**
 * 括線右邊是一欄共用的標籤欄：另一面提示（背 ×N）與群組標籤都放這裡，對齊各自的 U 位。
 * 以前是兩條軌並排，群組先占掉一大塊，背面提示被推到很遠，同一排沒有群組的機櫃又貼著機櫃，
 * 整排對不齊（使用者 2026-11-06 截圖）。現在有群組的機櫃只多讓出括線那幾 px。
 */
const labelColX = computed(() => (groupLanes.value ? groupLanes.value * GROUP_LANE_PX + 4 : 0));
// 群組標籤與背面提示的晶片同一條左緣（背面提示前面有 8px 自己的小括線）
const sideRailW = computed(() => labelColX.value + (placedGroups.value.length ? GROUP_LABEL_PX + 8 : 58));
const showSideRail = computed(() => placedGroups.value.length > 0 || railHints.value.length > 0);
const railHeight = computed(() => cells.value.reduce((a, c) => a + c.px, 0));
/** 群組標籤的中心 y：預設在括線中間；跟背面提示或別的群組標籤撞在同一高度時，往上下找空位 */
const groupLabelY = computed(() => {
  const HALF = 12;
  const taken: number[] = railHints.value.map((h) => h.top + h.height / 2);
  const out = new Map<string, number>();
  for (const g of placedGroups.value) {
    const center = g.top + g.height / 2;
    const cands = [center];
    for (let k = 1; k <= 40; k++) cands.push(center + 2 * HALF * k, center - 2 * HALF * k);
    const ok = (y: number) => y >= HALF && y <= railHeight.value - HALF && taken.every((c) => Math.abs(c - y) >= 2 * HALF);
    const inSpan = cands.find((y) => y >= g.top && y <= g.top + g.height && ok(y));
    const y = inSpan ?? cands.find(ok) ?? center;
    out.set(g.id, y);
    taken.push(y);
  }
  return out;
});
const groupHl = ref<Set<string>>(new Set());
const openGroup = ref<string | null>(null);
function groupLabel(g: GroupBracket): string {
  return `${g.name} ${g.here}/${g.total}`;
}
function groupKind(kind: string): string {
  return t(`device_groups.kind_${kind}`);
}
function memberMeta(m: any): string {
  const g = m.group as RackGroupInfo | null;
  const bits = [g?.role ? t(`device_groups.role_${g.role}`) : "", g?.order ? `#${g.order}` : "", m.model ?? ""];
  return bits.filter(Boolean).join(" · ");
}
function goRack(id: string) {
  openGroup.value = null;
  router.push({ name: "racks", query: { rack: id } });
}
function goGroup(id: string) {
  openGroup.value = null;
  router.push({ name: "devices", query: { tab: "groups", group: id } });
}
</script>

<template>
  <n-card v-if="diagram" class="rack-diagram-card" :class="{ 'rd-compact': compact, 'rd-bare': bare }"
          :bordered="!bare">
    <!-- 卡片標題要有圖示（同全站；以前用 title 屬性放不進圖示） -->
    <template v-if="!bare" #header>
      <span class="rd-card-title"><n-icon :size="18"><RacksIcon /></n-icon>{{ cardTitle }}</span>
    </template>
    <n-space vertical :size="12">
      <!-- 控制列：自標題列搬到內文最上方。
           用 flex 而不是 n-space：n-space 的每個項目是 block 包 inline-flex，靠 baseline
           對齊，而兩顆按鈕的 line-height 不同（22.4px vs 12px），就會差個 1~2px 對不齊。 -->
      <div v-if="showControls" class="rd-toolbar">
        <n-button-group size="tiny">
          <n-button :type="faceView === 'front' ? 'primary' : 'default'" @click="faceView = 'front'">
            {{ t("racks.face_front") }}
          </n-button>
          <n-button :type="faceView === 'rear' ? 'primary' : 'default'" @click="faceView = 'rear'">
            {{ t("racks.face_rear") }}<span v-if="hasRear" style="margin-left:3px">•</span>
          </n-button>
        </n-button-group>
        <span class="zoom-ctl" :title="t('rack_diagram.zoom')">
          <n-slider v-model:value="zoom" :min="ZOOM_MIN" :max="1" :step="0.05"
                    :format-tooltip="(v: number) => Math.round(v * 100) + '%'" style="width: 110px" />
          <span class="zoom-ctl__val">{{ Math.round(zoom * 100) }}%</span>
        </span>
        <n-dropdown trigger="click" :options="exportOptions" :disabled="exporting" @select="onExport">
          <n-button size="tiny" :loading="exporting" :disabled="exporting" :title="t('rack_diagram.export_svg_hint')">
            <template #icon><n-icon><ExportIcon /></n-icon></template>
            {{ t("common.export") }}
          </n-button>
        </n-dropdown>
      </div>
      <!-- 整排並列時不顯示逐櫃的衝突提示：那是概覽，而且提示會把有衝突的那一櫃整個往下
           推，害整排底部對不齊（差多少就正好是提示多高）。點進單櫃才看得到。
           `floorAlignTo` 有值就代表「正在跟別的機櫃並排對齊」。 -->
      <n-alert
        v-if="diagram.conflicts.length > 0 && !bare && !floorAlignTo"
        type="warning"
        :title="t('rack_diagram.conflict_title', { n: diagram.conflicts.length })"
      >
        <ul class="conflict-list">
          <li v-for="(line, i) in conflictLines" :key="i">{{ line }}</li>
        </ul>
      </n-alert>

      <!-- 只要機櫃有設定 U 數，即使沒有任何 device 也畫出空機櫃框 -->
      <n-empty
        v-if="!diagram.u_height"
        :description="t('rack_diagram.empty')"
      />

      <!-- 比卡片寬（手機）時在這一層左右捲：以前整張溢出卡片、頁面又不能橫向捲，右半邊看不到 -->
      <div v-else ref="scrollEl" class="rack-scroll">
      <div class="rack-zoom"
           :style="{ height: ownPx * effZoom + 'px', width: ownW * effZoom + 'px',
                     marginTop: floorPad * effZoom + 'px', '--rd-fit': String(fitZoom) }">
       <div ref="wrapEl" class="rack-wrap"
            :style="{ transform: effZoom === 1 ? undefined : `scale(${effZoom})` }">
        <!-- U 編號：機櫃框外左側 gutter -->
        <div class="u-gutter" :style="topPx ? { paddingTop: 6 + topPx + 'px' } : undefined">
          <!-- 編號置中在那一列**自己的**空間（扣掉底下那片板）：機櫃最下面那一 U 底下是 75mm 的底座，
               連板一起置中的話編號會掉進底座裡（使用者回報）。 -->
          <div v-for="cell in cells" :key="'g' + cell.u" class="u-num-out"
               :style="{ height: cell.px + 'px', paddingBottom: cell.board + 'px', boxSizing: 'border-box' }">{{ cell.isTop ? t("racks.level_top") : cell.u }}</div>
        </div>
        <div class="rack-frame"
             :class="{ 'is-shelf': isShelf, 'is-wire': isWire, 'is-industrial': isIndustrial, 'is-wood': isWood,
                       'is-angle': isAngle, 'is-kallax': isKallax, 'is-lack': isLack }"
             :style="{ '--rd-col-w': colPx + 'px', '--rd-side': sidePx + 'px', '--rd-row-h': rowPx + 'px',
                       '--rd-board': boardPx + 'px', '--rd-post-top': postTop + 'px',
                       '--rd-floor': floorPx + 'px', '--rd-top': topPx + 'px',
                       '--rd-base': (isShelf || isLack ? 0 : (cells[cells.length - 1]?.board ?? 0)) + 'px',
                       ...finishVars }">
          <!-- KALLAX：外框（兩側與頂板、底板的顏色）＋直的內隔板。外框從頂板開始，
               頂板上面那一列是開放的（KALLAX 頂部可以放東西）。 -->
          <i v-if="isKallax" class="kallax-box" :style="{ top: postTop + 'px' }" aria-hidden="true" />
          <template v-if="kallaxDividers">
            <i v-for="(f, i) in kallaxDividers.at" :key="'kd' + i" class="kallax-div" aria-hidden="true"
               :style="{ top: kallaxDividers.top + 'px', height: kallaxDividers.height + 'px',
                         width: kallaxDividers.w + 'px',
                         left: `calc(var(--rd-side) + (100% - 2 * var(--rd-side)) * ${f} - ${kallaxDividers.w / 2}px)` }" />
          </template>
          <!-- LackRack：兩支桌腳從最上面那張的桌面一路到地面，每張桌子一片桌面蓋在腳上
               （疊起來的桌子因此看得出各自的腳）。都有輪廓線，跟匯出、嵌入圖同一種畫法。 -->
          <template v-if="lackParts">
            <i class="lack-leg is-left" :style="{ top: lackParts.legTop + 'px' }" aria-hidden="true" />
            <i class="lack-leg is-right" :style="{ top: lackParts.legTop + 'px' }" aria-hidden="true" />
            <i v-for="(y, i) in lackParts.tops" :key="'lt' + i" class="lack-top"
               :style="{ top: y + 'px', height: lackParts.slab + 'px' }" aria-hidden="true" />
          </template>
          <!-- 頂板：CSS 是用每一列的 border-bottom 畫層板，最上面那片畫不出來 ——
               層架頂端幾乎一定有一片板，少了就像少一層（SVG 那邊是多畫一片解決的）。 -->
          <!-- 開放頂端時**不畫**這片：最上面那一列就是頂板的上面，那裡沒有板。
               11 列的 border-bottom 剛好給出 11 片板（10 層 + 頂板）。 -->
          <div v-if="isShelf && !openTop" class="shelf-top" aria-hidden="true" />
          <!-- 鍍鉻層架的套環：每一片層板的位置各一個（層高逐層可調，不能用固定間距畫） -->
          <i v-for="(y, i) in (isWire ? boundaryTops : [])" :key="'c' + i"
             class="wire-collar" :style="{ top: y + 'px' }" aria-hidden="true" />
          <!-- 背面的 OBSERVATÖR 支撐桿。放在最前面＝畫在裝置後面，空層才看得到。 -->
          <div v-if="braceStyle" class="wood-brace" :style="braceStyle" aria-hidden="true" />
          <template v-for="cell in cells" :key="cell.u">
            <!-- 一個 U = 12 格的橫向網格；裝置與空隙都用百分比絕對定位，
                 所以整 U / 1/2 / 1/3 / 1/4 / 1/6 走的是同一條渲染路徑（issue #31）。 -->
            <div class="u-row u-slots" :class="{ 'is-top': cell.isTop, 'has-board': cell.board > 0 }"
                 :style="{ height: cell.px + 'px', '--rd-board': cell.board + 'px' }">
              <n-tooltip v-for="p in cell.parts" :key="p.id + '@' + p.slot"
                         trigger="hover" :delay="60" placement="right">
                <template #trigger>
                  <div
                    class="u-part u-occupied"
                    :class="{ 'u-top': p.is_top, 'u-bottom': p.is_bottom, 'u-cont': !p.is_bottom, 'u-hl': hoveredId === p.id || flashIds.has(String(p.id)) || groupHl.has(String(p.id)), 'u-self': !!highlightId && highlightId === p.id }"
                    :style="{ background: colorFor(p.type), left: pct(p.slot), width: pct(p.span),
                              ...partVStyle(p),
                              justifyContent: p.span >= 12 ? nameJustify : 'center' }"
                    @mouseenter="hoveredId = p.id"
                    @mouseleave="hoveredId = null"
                    @click.stop="goDevice(p.id)"
                  >
                    <span v-if="p.is_mid" class="d-name-span"
                          :class="{ 'd-name-span-half': p.span < 12 }"
                          :style="{ height: (p.vslot || p.vspan < RACK_SLOTS)
                                              ? '100%' : (p.runPx - p.runBoard) + 'px' }">
                      <span class="d-name" :class="{ 'd-name-half': p.span < 12 }">{{ p.name }}</span>
                    </span>
                  </div>
                </template>
                <div class="rack-tip">
                  <div class="rt-name">{{ p.name }}</div>
                  <div class="rt-row"><span>{{ t("cols.type") }}</span><b>{{ p.type }}</b></div>
                  <div v-if="p.vendor" class="rt-row"><span>{{ t("cols.vendor") }}</span><b>{{ p.vendor }}</b></div>
                  <div v-if="p.model" class="rt-row"><span>{{ t("cols.model") }}</span><b>{{ p.model }}</b></div>
                  <div v-if="p.primary_ip" class="rt-row"><span>IP</span><b>{{ p.primary_ip }}</b></div>
                  <div class="rt-row"><span>{{ t("rack_diagram.height") }}</span><b>{{ p.u_size }}U</b></div>
                  <div v-if="p.group" class="rt-row">
                    <span>{{ t("device_groups.group") }}</span>
                    <b>{{ p.group.name }}{{ p.group.role ? ` · ${t(`device_groups.role_${p.group.role}`)}` : "" }}{{ p.group.order ? ` #${p.group.order}` : "" }}</b>
                  </div>
                  <div v-if="p.span < 12" class="rt-row">
                    <span>{{ t("rack_diagram.width") }}</span><b>{{ p.span }}/12</b>
                  </div>
                </div>
              </n-tooltip>

              <!-- 格櫃：另一面的提示放在對應的格位（這一面那一格是空的才放進來，不遮裝置） -->
              <template v-for="h in cellHints" :key="'ch' + h.key">
                <n-popover v-if="h.from === cell.u" trigger="click" placement="right" :width="300"
                           :show="openHint === h.key" @update:show="(v: boolean) => (openHint = v ? h.key : null)">
                  <template #trigger>
                    <button type="button" class="face-chip face-chip--cell" data-testid="rack-face-hint"
                            :style="{ left: `calc(${pct((h.col ?? 0) * (RACK_SLOTS / kallaxCols) + RACK_SLOTS / kallaxCols)} - 6px)` }"
                            @click.stop>{{ hintLabel(h) }}</button>
                  </template>
                  <div class="face-pop">
                    <div class="face-pop__t">{{ hintTitle(h) }}</div>
                    <div class="face-pop__n">{{ t("racks.devices_total", { n: h.devices.length }) }}</div>
                    <button v-for="d in h.devices" :key="d.device_id" type="button" class="face-pop__d" @click="goDevice(String(d.device_id))">
                      <i class="face-pop__sw" :style="{ background: colorFor(d.type) }" />
                      <span class="face-pop__name">{{ d.name }}</span>
                      <span class="face-pop__m">{{ [t(`devices.type_${d.type}`), d.model].filter(Boolean).join(" · ") }}</span>
                    </button>
                    <n-button size="small" type="primary" block @click="switchToOther(h)"><template #icon><n-icon><SyncIcon /></n-icon></template>
                      {{ t(otherFace === "rear" ? "racks.switch_to_rear" : "racks.switch_to_front") }}
                    </n-button>
                  </div>
                </n-popover>
              </template>
              <!-- 空隙：點了就帶著「哪一個 U、哪一格」去新增 -->
              <div v-for="g in cell.gaps" :key="'gap' + g.slot"
                   class="u-part u-gap" :class="{ 'u-pickable': editable }"
                   :style="{ left: pct(g.slot), width: pct(g.span) }"
                   :title="editable
                     ? t(isShelf ? 'racks.pick_device_here_level' : 'racks.pick_device_here')
                     : t('racks.empty_at', { pos: cellPosLabel(cell) })"
                   @click="editable && props.diagram && emit('pick-empty', cell.u, props.diagram.rack_id, g.slot)">
                <span v-if="editable && g.span >= 3" class="u-plus">＋</span>
              </div>
            </div>
          </template>
        </div>
        <!-- 框外右側：裝置群組的括線貼著機櫃，右邊一欄共用的標籤欄放背面提示與群組標籤（見 labelColX） -->
        <div v-if="showSideRail" class="side-rail" data-testid="rack-side-rail"
             :style="{ paddingTop: 6 + topPx + 'px', width: sideRailW + 'px' }">
          <div class="side-rail__in" :style="{ height: railHeight + 'px' }">
            <template v-for="g in placedGroups" :key="g.id">
              <i class="group-bracket" aria-hidden="true"
                 :style="{ top: g.top + 'px', height: g.height + 'px', left: g.lane * GROUP_LANE_PX + 'px', '--grp': g.color }">
                <i v-for="(y, i) in g.ticks" :key="i" class="group-tick" :style="{ top: y + 'px' }" />
              </i>
              <n-popover trigger="click" placement="right" :width="300"
                         :show="openGroup === g.id" @update:show="(v: boolean) => (openGroup = v ? g.id : null)">
                <template #trigger>
                  <button type="button" class="group-chip" data-testid="rack-group-chip" :title="groupLabel(g)"
                          :style="{ top: (groupLabelY.get(g.id) ?? g.top + g.height / 2) + 'px', left: labelColX + 8 + 'px', '--grp': g.color }"
                          @mouseenter="groupHl = new Set(g.members.map((m) => String(m.device_id)))"
                          @mouseleave="groupHl = new Set()">
                    <span class="group-chip__name">{{ g.name }}</span><span class="group-chip__n">{{ g.here }}/{{ g.total }}</span>
                  </button>
                </template>
                <div class="face-pop" data-testid="rack-group-pop">
                  <div class="face-pop__t">{{ g.name }}</div>
                  <div class="face-pop__n">{{ groupKind(g.kind) }} · {{ t("device_groups.rack_here", { here: g.here, total: g.total }) }}</div>
                  <button v-for="m in g.members" :key="m.device_id" type="button" class="face-pop__d" @click="goDevice(String(m.device_id))">
                    <i class="face-pop__sw" :style="{ background: colorFor(m.type) }" />
                    <span class="face-pop__name">{{ m.name }}</span>
                    <span class="face-pop__m">{{ memberMeta(m) || t(`devices.type_${m.type}`) }}</span>
                  </button>
                  <div v-if="g.here > g.members.length" class="face-pop__n">
                    {{ t("device_groups.rack_other_face", { n: g.here - g.members.length }) }}
                  </div>
                  <button v-for="r in g.elsewhere" :key="r.rack_id" type="button" class="group-pop__link"
                          data-testid="rack-group-elsewhere" @click="goRack(r.rack_id)">
                    {{ t("device_groups.rack_elsewhere", { n: r.count, rack: r.rack_name ?? "?" }) }}
                  </button>
                  <div v-if="g.unracked" class="face-pop__n">{{ t("device_groups.rack_unracked", { n: g.unracked }) }}</div>
                  <n-button size="small" block @click="goGroup(g.id)">
                    <template #icon><n-icon><GroupIcon /></n-icon></template>
                    {{ t("device_groups.manage") }}
                  </n-button>
                </div>
              </n-popover>
            </template>
            <!-- 另一面的提示：對齊 U 位／層，跨多 U 用括線 -->
            <n-popover v-for="h in railHints" :key="h.key" trigger="click" placement="right" :width="300"
                       :show="openHint === h.key" @update:show="(v: boolean) => (openHint = v ? h.key : null)">
              <template #trigger>
                <button type="button" class="face-hint" data-testid="rack-face-hint"
                        :class="{ 'is-span': h.height > 30 }"
                        :style="{ top: h.top + 'px', height: h.height + 'px', left: labelColX + 'px' }">
                  <span class="face-chip">{{ hintLabel(h) }}</span>
                </button>
              </template>
              <div class="face-pop">
                <div class="face-pop__t">{{ hintTitle(h) }}</div>
                <div class="face-pop__n">{{ t("racks.devices_total", { n: h.devices.length }) }}</div>
                <button v-for="d in h.devices" :key="d.device_id" type="button" class="face-pop__d" @click="goDevice(String(d.device_id))">
                  <i class="face-pop__sw" :style="{ background: colorFor(d.type) }" />
                  <span class="face-pop__name">{{ d.name }}</span>
                  <span class="face-pop__m">{{ [t(`devices.type_${d.type}`), d.model].filter(Boolean).join(" · ") }}</span>
                </button>
                <n-button size="small" type="primary" block @click="switchToOther(h)"><template #icon><n-icon><SyncIcon /></n-icon></template>
                  {{ t(otherFace === "rear" ? "racks.switch_to_rear" : "racks.switch_to_front") }}
                </n-button>
              </div>
            </n-popover>
          </div>
        </div>
       </div>
      </div>
      </div>

      <div v-if="showLegend" class="legend">
        <!-- 圖例用裝置類型的顯示名稱（以前直接印 router／patch panel 這些代碼） -->
        <span v-for="ty in RACK_DEVICE_TYPES" :key="ty" class="legend-item" :style="{ background: colorFor(ty) }">
          {{ t(`devices.type_${ty}`) }}
        </span>
        <span class="legend-note">{{ t("racks.rear_legend") }}</span>
      </div>
    </n-space>
  </n-card>
</template>

<style scoped>
.rd-card-title { display: inline-flex; align-items: center; gap: 8px; }
/* 裝置群組軌：括線開口朝左（朝向機櫃裡的成員），每台成員一個刻度；標籤在所有軌道的右邊 */
.side-rail { flex: 0 0 auto; }
.side-rail__in { position: relative; }
.group-bracket { position: absolute; width: 7px; border: 2px solid var(--grp); border-left: 0;
  border-radius: 0 3px 3px 0; box-sizing: border-box; }
.group-tick { position: absolute; left: -2px; width: 7px; height: 2px; margin-top: -1px; background: var(--grp); }
/* 外框樣式（群組色的邊框＋淡底）：跟實心藍的「背 ×N」分得開，也不像警示 */
.group-chip { position: absolute; transform: translateY(-50%); display: inline-flex; gap: 4px; align-items: center;
  max-width: 146px; border: 1px solid var(--grp); border-radius: 4px; padding: 3px 6px; color: inherit; cursor: pointer;
  background: color-mix(in srgb, var(--grp) 18%, transparent); font: 600 11px/1 system-ui, sans-serif; z-index: 1; }
.group-chip__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.group-chip__n { flex: 0 0 auto; opacity: .9; font-variant-numeric: tabular-nums; }
.group-chip:hover, .group-chip:focus-visible { background: color-mix(in srgb, var(--grp) 34%, transparent); }
.group-pop__link { text-align: left; background: none; border: 0; padding: 2px 0; cursor: pointer; font: inherit;
  color: var(--primary-color, #18a058); text-decoration: none; }
.group-pop__link:hover { text-decoration: underline; }
/* 另一面的提示軌（背 ×N／前 ×N）：框外右側，對齊 U 位；跨多 U 用左側括線 */
.face-hint { position: absolute; display: flex; align-items: center; padding: 0 0 0 8px;
  background: none; border: 0; cursor: pointer; font: inherit; }
.face-hint::before { content: ""; position: absolute; left: 0; top: 2px; bottom: 2px; width: 3px; border-radius: 2px;
  background: #5b6ee1; }
.face-hint.is-span::before { width: 6px; background: transparent; border: 2px solid #5b6ee1; border-right: 0; border-radius: 3px 0 0 3px; }
.face-chip { display: inline-block; background: #5b6ee1; color: #fff; font: 600 11px/1 system-ui, sans-serif;
  padding: 4px 6px; border-radius: 4px; white-space: nowrap; }
.face-hint:hover .face-chip, .face-hint:focus-visible .face-chip { background: #4357d6; box-shadow: 0 0 0 2px rgba(91, 110, 225, .35); }
/* 格櫃：放在那一格的右下角 */
.face-chip--cell { position: absolute; bottom: 4px; transform: translateX(-100%); border: 0; cursor: pointer; z-index: 2; }
.face-pop { display: grid; gap: 8px; }
.face-pop__t { font-weight: 700; font-size: 14px; }
.face-pop__n { font-size: 12px; opacity: .7; margin-top: -6px; }
.face-pop__d { display: grid; grid-template-columns: 14px minmax(0, 1fr); column-gap: 8px; text-align: left;
  border: 1px solid rgba(127, 127, 127, .25); border-radius: 6px; padding: 6px 8px; background: none; cursor: pointer;
  font: inherit; color: inherit; }
.face-pop__d:hover { border-color: #5b6ee1; }
.face-pop__sw { grid-row: span 2; width: 14px; height: 14px; border-radius: 3px; margin-top: 2px; }
.face-pop__name { font-weight: 600; overflow-wrap: anywhere; }
.face-pop__m { font-size: 12px; opacity: .7; }

/* 衝突清單：繁中可讀句子（取代原本的 JSON dump） */
.conflict-list { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.7; }

/* 多機櫃並排落地對齊：矮櫃由 floorPad（inline margin-top）在頂端補空白，使各櫃底部(U1)
   對齊同一條地板線；補白後各櫃內容等高，卡片自然等高。 */
.rack-wrap { display: flex; align-items: flex-start; gap: 6px; }
/* 左側 U 編號 gutter：頂端內距 = 機櫃框 border(2)+padding(4) = 6px，讓每個編號與
   右側對應 U 列等高(28px)且垂直置中對齊。 */
.u-gutter { display: flex; flex-direction: column; padding-top: 6px; flex: 0 0 auto; }
.u-num-out {
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  min-width: 26px;                 /* 不寫死寬度：英文的「Top」曾被擠成「To / p」 */
  white-space: nowrap;
  padding-right: 6px;
  font: bold 12px ui-monospace, SFMono-Regular, Menlo, monospace;
  color: rgba(127, 127, 127, 0.75);
}
.rack-frame {
  /* 一格 U 的高度。名稱要跨整台置中，得知道一格多高 —— 所以放成變數，
     compact 模式只要改這一個值。 */
  --rd-row-h: 28px;
  /* 裝置外框的線寬（見 .u-occupied）。名稱置中要用它把邊框的厚度補回去。 */
  --rd-border: 2px;
  border: 2px solid rgba(127, 127, 127, 0.5);
  border-radius: 4px;
  padding: 4px;
  width: var(--rd-col-w, 250px);
  background: rgba(127, 127, 127, 0.04);
  /* 腳（離地）是用 ::after 往框外畫的，不占版面 —— 不留這段，量出來的高度就不含腳，
     腳會壓到下面的圖例（LackRack 桌下 44mm 最明顯），並排時也會以框的下緣而不是地面對齊。 */
  margin-bottom: var(--rd-floor, 0px);
}
/* 標準機櫃：畫成箱體 —— 兩側是有安裝孔的立柱（19 吋機櫃的方孔條），
   不是一條細框線。孔距 1U 三孔是實物的樣子，這裡用等距近似即可。 */
.rack-frame:not(.is-shelf):not(.is-industrial):not(.is-lack) {
  border-width: 2px;
  border-color: rgba(120, 126, 134, 0.85);
  /* 外框＝櫃體側板；側板與立柱之間是走線空間（--rd-side，由外寬算出），立柱與設備區在正中 */
  padding-left: calc(9px + var(--rd-side, 0px)); padding-right: calc(9px + var(--rd-side, 0px));
  position: relative;
  background:
    /* 頂板（--rd-top）與底座（--rd-base，最下面那一 U 底下那片板）：整個櫃寬的實心板。
       以前只有外框那條線，使用者說「近乎只有一條線，不合理」。畫在最上層蓋住立柱與走線刻線。 */
    linear-gradient(180deg, #dfe2e6, #c3c8ce) 0 0 / 100% calc(4px + var(--rd-top, 0px)) no-repeat,
    linear-gradient(180deg, #c3c8ce, #aab0b7) 0 100% / 100% calc(4px + var(--rd-base, 0px)) no-repeat,
    /* 安裝孔**只在兩側立柱上**。第一版寫成整片 100% 寬的橫向漸層，
       結果整個櫃體都是橫條紋 —— 圖磚要限制在 8px 的立柱寬度內。 */
    radial-gradient(circle at 4px 7px,
      rgba(40, 44, 50, 0.5) 0 1.3px, transparent 1.6px) var(--rd-side, 0px) 0 / 8px 14px repeat-y,
    radial-gradient(circle at 4px 7px,
      rgba(40, 44, 50, 0.5) 0 1.3px, transparent 1.6px) calc(100% - var(--rd-side, 0px)) 0 / 8px 14px repeat-y,
    linear-gradient(90deg, #cfd3d8 0, #e8ebee 3px, #c3c8ce 8px)
      var(--rd-side, 0px) 0 / 8px 100% no-repeat,
    linear-gradient(270deg, #cfd3d8 0, #e8ebee 3px, #c3c8ce 8px)
      calc(100% - var(--rd-side, 0px)) 0 / 8px 100% no-repeat,
    /* 走線空間：淡底＋每 14px 一道理線槽的刻線，看得出「這裡是走線用的」 */
    repeating-linear-gradient(180deg, rgba(120, 126, 134, 0.35) 0 2px, transparent 2px 14px)
      0 0 / var(--rd-side, 0px) 100% no-repeat,
    repeating-linear-gradient(180deg, rgba(120, 126, 134, 0.35) 0 2px, transparent 2px 14px)
      100% 0 / var(--rd-side, 0px) 100% no-repeat,
    linear-gradient(rgba(127, 127, 127, 0.10), rgba(127, 127, 127, 0.10)) 0 0 / var(--rd-side, 0px) 100% no-repeat,
    linear-gradient(rgba(127, 127, 127, 0.10), rgba(127, 127, 127, 0.10)) 100% 0 / var(--rd-side, 0px) 100% no-repeat,
    rgba(127, 127, 127, 0.04);
}
/* 機櫃的頂板與底座要占高度：頂板＝內距上緣，底座＝最下面那一 U 的下框（透明，
   露出上面那層底座背景）。U 與 U 之間沒有板，照舊是一條虛線。 */
.rack-frame:not(.is-shelf):not(.is-lack) { padding-top: calc(4px + var(--rd-top, 0px)); }
.rack-frame:not(.is-shelf):not(.is-lack) .u-row.has-board {
  border-bottom: var(--rd-board) solid transparent;
}
/* 機櫃也要有腳：底部兩隻短腳，高度與層架的離地一致，否則底部看起來像被齊平切掉。 */
.rack-frame:not(.is-shelf)::after {
  content: "";
  position: absolute;
  left: 0; right: 0;
  bottom: calc(-1 * var(--rd-floor, 7px));
  height: var(--rd-floor, 7px);
  background: linear-gradient(90deg,
    #b3b9c1 0 9px, transparent 9px,
    transparent calc(100% - 9px), #b3b9c1 calc(100% - 9px));
  pointer-events: none;
}
.rack-frame.is-industrial::after {
  background: linear-gradient(90deg,
    #6f767e 0 11px, transparent 11px,
    transparent calc(100% - 11px), #6f767e calc(100% - 11px));
}

/* 工業機櫃：箱體更厚重，立柱也更寬 */
.rack-frame.is-industrial {
  padding-left: calc(11px + var(--rd-side, 0px)); padding-right: calc(11px + var(--rd-side, 0px));
  background:
    linear-gradient(180deg, #9aa1a9, #7d848c) 0 0 / 100% calc(4px + var(--rd-top, 0px)) no-repeat,
    linear-gradient(180deg, #7d848c, #656c74) 0 100% / 100% calc(4px + var(--rd-base, 0px)) no-repeat,
    linear-gradient(90deg, #838a93 0, #a7aeb6 4px, #6f767e 10px)
      var(--rd-side, 0px) 0 / 10px 100% no-repeat,
    linear-gradient(270deg, #838a93 0, #a7aeb6 4px, #6f767e 10px)
      calc(100% - var(--rd-side, 0px)) 0 / 10px 100% no-repeat,
    repeating-linear-gradient(180deg, rgba(90, 95, 105, 0.35) 0 2px, transparent 2px 14px)
      0 0 / var(--rd-side, 0px) 100% no-repeat,
    repeating-linear-gradient(180deg, rgba(90, 95, 105, 0.35) 0 2px, transparent 2px 14px)
      100% 0 / var(--rd-side, 0px) 100% no-repeat,
    rgba(90, 95, 105, 0.10);
}
/* 一般層架（issue #30）：沒有機櫃導軌，畫成層板 —— 每一層下緣一條實線，兩側不封邊。 */
.rack-frame.is-shelf {
  border-left: none; border-right: none; border-radius: 0;
  background: transparent;
}
.rack-frame.is-shelf .u-row { border-bottom: var(--rd-board, 2px) solid rgba(127, 127, 127, 0.55); }

/* 工業機櫃：箱體，比標準機櫃厚重 —— 粗外框 + 深色底。 */
.rack-frame.is-industrial {
  border: 4px solid rgba(90, 95, 105, 0.85);
  border-radius: 3px;
  /* 不設 background：上面那段畫立柱與走線區。以前這裡的 background 蓋掉了它，
     工業機櫃的立柱從來沒畫出來過。 */
}

/* 鍍鉻層架：兩側圓管立柱 + 網狀層板。立柱疊三層背景 ——
   (1) 每層層板位置的套環、(2) 整根立柱的細溝槽環、(3) 圓柱高光漸層。
   套環與溝槽是鍍鉻層架最好認的特徵：沒有它們，立柱只會像兩根灰色長條。
   套環的間距直接綁 --rd-row-h，所以層高改變時會自己對齊到每一層。 */
.rack-frame.is-wire {
  position: relative;
  border: none; border-radius: 0; background: transparent;
  padding-left: 14px; padding-right: 14px;
}
.rack-frame.is-wire::before,
.rack-frame.is-wire::after {
  content: ""; position: absolute; width: 13px;
  top: calc(var(--rd-post-top, 0px) - 7px); bottom: calc(-1 * var(--rd-floor, 7px));
  border-radius: 7px;
  background:
    /* 整根的細溝槽環。套環改用 .wire-collar 元素畫在每片層板的位置 ——
       層高可以一層一層不同，固定間距的重複漸層會對不準。 */
    repeating-linear-gradient(180deg,
      rgba(70, 76, 82, 0.20) 0 1px, transparent 1px 5px),
    /* (3) 圓柱高光 */
    linear-gradient(90deg,
      #5f646a 0%, #90969c 16%, #e9edf0 36%, #ffffff 47%,
      #cfd4d9 60%, #969ca2 80%, #55595e 100%);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.22), 0 1px 3px rgba(0, 0, 0, 0.18);
}
.rack-frame.is-wire::before { left: 0; }
.rack-frame.is-wire::after { right: 0; }
/* 套環：裝在每一片層板的位置，左右立柱各一個 */
.wire-collar {
  position: absolute; left: 0; right: 0; height: 6px; pointer-events: none;
}
.wire-collar::before,
.wire-collar::after {
  content: ""; position: absolute; top: 0; width: 13px; height: 6px;
  background: linear-gradient(90deg,
    #5f646a 0%, #90969c 16%, #e9edf0 36%, #ffffff 47%,
    #cfd4d9 60%, #969ca2 80%, #55595e 100%);
  box-shadow: 0 0 0 0.8px rgba(60, 66, 72, 0.5);
}
.wire-collar::before { left: 0; }
.wire-collar::after { right: 0; }

/* 網狀層板：只畫前緣的鍍鉻橫桿。空層原本鋪十字網格表示網面，但整片格子在畫面上
   只是雜訊（尤其縮小之後），拿掉。 */
.rack-frame.is-wire .u-row {
  /* 層板厚度來自機櫃設定（層高填的是淨空高，板厚另計） */
  border-bottom: var(--rd-board, 6px) solid transparent;
  border-image: linear-gradient(180deg,
    #ffffff 0%, #dfe4e8 25%, #a8aeb4 70%, #71767b 100%) 1;
}
/* 木質層架（IKEA IVAR）：松木側架 + 松木層板 + 背面的鋼製交叉支撐桿。
   側架整根畫在層板外側（實物就是層板架在兩片側架之間），上面那排調整孔是最好認的地方。 */
.rack-frame.is-wood {
  position: relative;
  border: none; border-radius: 0; background: transparent;
  padding-left: 15px; padding-right: 15px;
}
.rack-frame.is-wood::before,
.rack-frame.is-wood::after {
  /* 方柱：沒有圓角、也沒有圓管那種中央高光，只有一面受光一面暗 */
  content: ""; position: absolute; width: 14px;
  top: calc(var(--rd-post-top, 0px) - 8px); bottom: calc(-1 * var(--rd-floor, 8px));
  background:
    /* 調整孔：一排**圓孔**。用橫向色帶畫會變成一圈一圈的條紋 —— 那是圓管的樣子，
       IVAR 的側架是平板 + 圓孔，所以要用 radial-gradient。 */
    /* 孔距 32mm、孔徑 7mm（IVAR 實物規格），照層架的比例尺換算（寬高同一個比例，
       250px／482.6mm，見後端 rack.v_px_per_mm）→ 間距 16.58px、直徑 3.63px。
       與後端 rack_svg.py 的 PEG_PITCH_MM 一致。 */
    radial-gradient(circle at 50% 50%,
      rgba(74, 48, 24, 0.5) 0 1.8px, rgba(255, 245, 230, 0.35) 1.8px 2.3px,
      transparent 2.4px) 0 0 / 14px 16.58px,
    /* 直的木紋（不是橫紋） */
    repeating-linear-gradient(90deg,
      rgba(120, 80, 40, 0.08) 0 1px, transparent 1px 7px),
    /* 平板：幾乎同色，只有右緣一條暗邊當厚度 */
    linear-gradient(90deg, #dcb98d 0%, #d4ae7f 80%, #b38a58 100%);
  box-shadow: 0 0 0 1px rgba(90, 60, 30, 0.4);
  /* 兩根柱子都要蓋住背面的交叉桿。`::before` 是元素的**第一個**子節點、`::after` 是最後一個，
     所以不指定層級時右柱蓋得住交叉桿、左柱蓋不住 —— 左邊的桿子會穿出側架，右邊不會。 */
  z-index: 1;
}
.rack-frame.is-wood::before { left: 0; }
.rack-frame.is-wood::after { right: 0; }
/* 層板：實心松木，比鍍鉻的橫桿厚 */
.rack-frame.is-wood .u-row {
  border-bottom: var(--rd-board, 6px) solid transparent;
  border-image: linear-gradient(180deg,
    #e9c99d 0%, #d2a56f 55%, #a5763f 100%) 1;
}
/* 支撐桿：兩根交叉成 X（官方商品圖就是 X），高度＝它實際跨的層數。
   注意漸層方向：色帶是「垂直於」漸層軸的，所以兩條軸分別指向右下與右上。 */
.wood-brace {
  position: absolute;
  left: 0; right: 0;
  pointer-events: none;
  background-image:
    linear-gradient(to bottom right, transparent calc(50% - 1.3px),
      rgba(150, 156, 162, 0.9) calc(50% - 1.3px) calc(50% + 1.3px), transparent calc(50% + 1.3px)),
    linear-gradient(to top right, transparent calc(50% - 1.3px),
      rgba(150, 156, 162, 0.9) calc(50% - 1.3px) calc(50% + 1.3px), transparent calc(50% + 1.3px));
}

/* 角鋼層架（台灣最常見的免螺絲角鋼）：兩支 40mm 的 L 型立柱、整排葫蘆孔（孔距 30mm）；
   每一層是一條鋼橫桿，上面跨放一片 9mm 夾板。橫桿勾在兩支立柱之間，所以立柱畫在前面。
   顏色（黑／白／鍍鋅）來自 --fin-*，與匯出、嵌入圖同一份配色。 */
.rack-frame.is-angle {
  position: relative;
  border: none; border-radius: 0; background: transparent;
  padding-left: var(--rd-side); padding-right: var(--rd-side);
}
.rack-frame.is-angle::before,
.rack-frame.is-angle::after {
  content: ""; position: absolute; width: var(--rd-side);
  /* +4px：絕對定位從內距框頂端算，列從 4px 內距之下才開始 —— 不補的話立柱比頂板高出一截 */
  top: calc(var(--rd-post-top, 0px) + 4px); bottom: calc(-1 * var(--rd-floor, 7px));
  background:
    var(--rd-keyholes) 0 0 / var(--rd-side) var(--rd-hole-pitch) repeat-y,
    /* 另一片翼：從正面看是立柱外緣的一條暗邊 */
    linear-gradient(90deg, var(--fin-edge) 0 2px, var(--fin-post) 2px);
  z-index: 1;
}
.rack-frame.is-angle::before { left: 0; }
/* 右邊那支左右鏡射：暗邊在外側 */
.rack-frame.is-angle::after { right: 0; transform: scaleX(-1); }
/* 每一層：底下那片「板」＝上面一條夾板＋下面的鋼橫桿。用背景畫（背景會鋪到邊框底下）——
   border-image 的漸層只會取最底下 1px 拉長，畫不出上下兩段。 */
.rack-frame.is-angle .u-row {
  border-bottom: var(--rd-board, 6px) solid transparent;
  /* 漸層要以**含邊框**的整格為準（最後的 border-box）：預設是內距框，100% 會停在邊框之上，
     橫桿就畫進了格子裡、被裝置蓋住（第一版就是這樣）。寫在簡寫裡 —— 另外寫
     background-origin 會被後面的 background 簡寫重設回去。 */
  background: linear-gradient(180deg,
    transparent calc(100% - var(--rd-board)),
    var(--ply) calc(100% - var(--rd-board)),
    var(--ply) calc(100% - var(--rd-board) + min(var(--rd-ply), var(--rd-board) / 2)),
    var(--fin-beam) calc(100% - var(--rd-board) + min(var(--rd-ply), var(--rd-board) / 2)))
    border-box;
}

/* IKEA KALLAX：外框（40mm）比內隔板（15mm）厚，一格一格。沒有背板、沒有腳，直接落地。
   外框用一片墊在最底下的色塊畫（兩側＋頂板＋底板），格子裡面是更深一點的內側。 */
.rack-frame.is-kallax {
  position: relative;
  isolation: isolate;   /* 讓 .kallax-box 的 z-index:-1 留在框裡，不會掉到卡片背景後面 */
  border: none; border-radius: 0; background: transparent;
  padding-left: var(--rd-side); padding-right: var(--rd-side);
}
.kallax-box {
  position: absolute; left: 0; right: 0; bottom: 4px;
  margin-top: 4px;
  background: var(--fin-frame);
  box-shadow: 0 0 0 1px var(--fin-line);
  z-index: -1; pointer-events: none;
}
.rack-frame.is-kallax .u-row { border-bottom: var(--rd-board, 4px) solid var(--fin-frame); }
.rack-frame.is-kallax .u-row:not(.is-top) { background: var(--fin-cell); }
.kallax-div {
  position: absolute; margin-top: 4px;
  background: var(--fin-frame);
  z-index: 1; pointer-events: none;
}

/* LackRack：LACK 邊桌當機櫃。最上面一列是「桌面上方」（可以放東西），它底下那片板是桌面；
   兩側是 50mm 的桌腳（設備耳朵鎖在桌腳正面），從桌面頂端往下到地面。桌腳與桌面是模板裡
   的 .lack-leg／.lack-top 方塊。下內距是 0：最下面那一 U 到地面剛好是桌下的 44mm。 */
.rack-frame.is-lack {
  position: relative;
  border: none; border-radius: 0; background: transparent;
  padding: 4px var(--rd-side) 0;
}
.rack-frame.is-lack::after { content: none; }
.lack-leg, .lack-top {
  position: absolute; margin-top: 4px;
  box-sizing: border-box;
  background: var(--fin-wood);
  border: 1px solid var(--fin-line);
  pointer-events: none;
}
.lack-leg { width: var(--rd-side); bottom: calc(-1 * var(--rd-floor, 0px)); }
.lack-leg.is-left { left: 0; }
.lack-leg.is-right { right: 0; }
.lack-top { left: 0; right: 0; }
/* 設備區只有 U 的那一段有底色；桌下的空隙（上面那張的腳下、最下面那張離地）都留白，
   兩處看起來才一樣 —— 以前上面那段有底色、最下面沒有，最下面那截桌腳就像突出去。 */
.rack-frame.is-lack .u-row:not(.is-top) { background: rgba(127, 127, 127, 0.04) padding-box; }
.rack-frame.is-lack .u-row.has-board { border-bottom: var(--rd-board) solid transparent; }

/* 卡片寬度跟著機櫃走：窄機櫃（例如 42 公分的層架）不要再撐滿整欄，旁邊留一大片空白。
   下限是工具列本身的寬度，否則正面／背面、拉桿、匯出會被擠到換行。 */
.rack-diagram-card {
  width: fit-content;
  min-width: 0;
  max-width: 100%;
}
/* 有工具列時才需要撐到工具列的寬度，否則按鈕會被擠到換行 */
.rack-diagram-card:has(.rd-toolbar) { min-width: min(320px, 100%); }

/* 顯示大小拉桿 */
.zoom-ctl { display: flex; align-items: center; gap: 8px; }
.zoom-ctl__val { font-size: 11px; opacity: 0.6; min-width: 32px; text-align: right; }
/* 縮放：transform 會脫離版面流，外層要跟著縮高度，否則下面會留一大塊空白。
   ⚠️ **不可以 overflow:hidden** —— 那個高度只算了各層高度＋層板，沒算外框的 padding、
   邊框與柱腳往下突出的部分，一裁就把三種機架的「腳」都切掉了（看起來像最下一層之後
   就沒有東西）。讓它溢出即可，反正水平方向沒有東西會跑出去。 */
.rack-zoom { overflow: visible; }
/* 機櫃比卡片寬（手機、放大）時的左右捲動層。高度已含離地的腳（.rack-frame 的 margin-bottom），
   下面再留一點空間給邊框陰影，負 margin 抵掉，桌機上版面不變 */
.rack-scroll {
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 6px;
  margin-bottom: -6px;
  -webkit-overflow-scrolling: touch;
  touch-action: pan-x pan-y;
}
.rack-zoom > .rack-wrap {
  transform-origin: top left;
  /* 寬度必須由**內容**決定，不能跟著外層走：外層的寬度是用這一層量出來的，
     若這一層又跟著外層縮，就會一路互相縮到 0（ResizeObserver 迴授迴圈，實際踩過）。 */
  width: max-content;
}

/* 控制列：flex 對齊，不吃 baseline */
.rd-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  /* 要能換行：靠右對齊又不換行時，放不下的部分會往**左邊**溢出卡片（手機上「正面」凸出去） */
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

/* 頂板：與各型態的層板同色同厚 */
.shelf-top {
  position: absolute;
  left: 0; right: 0; top: 0;
  pointer-events: none;
}
.rack-frame.is-shelf .shelf-top {
  height: 2px; background: #8b9096;
}
.rack-frame.is-wire .shelf-top {
  height: var(--rd-board, 6px);
  background: linear-gradient(180deg, #ffffff 0%, #dfe4e8 25%, #a8aeb4 70%, #71767b 100%);
}
.rack-frame.is-wood .shelf-top {
  height: var(--rd-board, 6px);
  background: linear-gradient(180deg, #e9c99d 0%, #d2a56f 55%, #a5763f 100%);
  box-shadow: 0 0 0 0.6px rgba(90, 60, 30, 0.35);
}

.u-row {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  height: var(--rd-row-h, 28px);
  border-bottom: 1px dashed rgba(127, 127, 127, 0.2);
  padding: 0 8px;
  font-size: 12px;
  font-family: monospace;
  color: white;
  position: relative;
}
.u-row:last-child {
  border-bottom: none;
}
.u-row.u-pickable { cursor: pointer; color: var(--n-text-color-3, #999); justify-content: center; }
.u-row.u-pickable:hover { background: rgba(24,160,88,0.14); color: var(--primary-color, #18a058); }
.u-plus { font-size: 13px; opacity: 0; }
.u-row.u-pickable:hover .u-plus { opacity: 1; }
.u-row:not(.u-occupied) {
  color: rgba(127, 127, 127, 0.5);
  background: transparent;
}
/* 裝置外框：每台（含多 U）都框起來，多 U 之間不畫內線 → 一眼看出佔幾 U */
.u-occupied {
  border-bottom: none;
  border-left: 2px solid rgba(0, 0, 0, 0.32);
  border-right: 2px solid rgba(0, 0, 0, 0.32);
  cursor: pointer;
}
.u-occupied.u-top { border-top: 2px solid rgba(0, 0, 0, 0.32); }
.u-occupied.u-bottom { border-bottom: 2px solid rgba(0, 0, 0, 0.32); }
/* hover 任一 U → 整台裝置點亮 + 框線（左右框；最上/最下格補上下框）
   ⚠️ **不要用 `filter` 或 `opacity` 來打亮**：那兩個都會讓每一格變成獨立的堆疊環境，
   跨多 U 的名稱（絕對定位在最上面那一格、往下延伸）就跳不出去，會被下面幾格蓋掉 ——
   實機症狀是「游標移過去，裝置名稱就不見了」。改用疊一層半透明白色，不建立堆疊環境。 */
.u-row.u-hl {
  box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24;
}
.u-row.u-hl::after,
.u-part.u-hl::after {
  content: "";
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.16);
  pointer-events: none;
}
.u-row.u-hl.u-top { box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24, inset 0 2px 0 #fbbf24; }
.u-row.u-hl.u-bottom { box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24, inset 0 -2px 0 #fbbf24; }
.u-row.u-hl.u-top.u-bottom { box-shadow: inset 0 0 0 2px #fbbf24; }
/* 橫向分格（issue #31）：一個 U 是 12 格的網格，裝置與空隙都用百分比絕對定位。
   整 U 也走同一條路（span=12），所以只有一套幾何要維護。 */
.u-row.u-slots { padding: 0; position: relative; }
.u-part {
  position: absolute; top: 0; bottom: 0;
  display: flex; align-items: center;
  box-sizing: border-box;
  /* ⚠️ 這裡**不能**設 overflow:hidden。跨多 U 的名稱是靠 .d-name-span 絕對定位往下
     溢出自己那一格來置中的（見下方註解），一加 overflow 就會被裁掉 ——
     實測症狀正是「2U 名稱被切一半、4U 完全看不見」。
     橫向的截斷由內層 .d-name 的 ellipsis 負責，不需要在這層裁。 */
}
.u-part + .u-part { border-left: 1px dashed rgba(127, 127, 127, 0.28); }
/* 頂板**上面**那一列沒有層板也沒有導軌，橫向分隔線在那裡沒有東西可以分；那一列又比
   放在上面的裝置高，線就從裝置上緣一路畫到半空中（客戶回報「分隔線畫太高了」）。 */
.u-row.is-top .u-part + .u-part { border-left: none; }
/* 裝置要擋住背面的東西（IVAR 的 X 支撐桿）。裝置底色是刻意半透明的（0.6~0.85），
   直接畫上去桿子會透出來 —— 在它後面墊一層不透明的底，顏色仍然疊在白底上，
   看起來完全一樣，但背後的桿子被擋住了。 */
.u-part.u-occupied::before {
  content: "";
  position: absolute;
  inset: 0;
  background: var(--n-card-color, var(--rd-under, #fff));
  z-index: -1;
}
/* 不在卡片裡的機櫃圖（裝置詳細頁的小圖）拿不到 --n-card-color，以前一律墊白色：
   深色主題下半透明的設備色疊在白底上整個變淺、白字看不到（10-23 客戶）。墊跟深色卡片一樣的底 */
:global(html[data-theme="dark"]) .rack-frame { --rd-under: #0f1825; }
.u-part.u-occupied { color: #fff; font-size: 12px; }
.u-part.u-occupied.u-top { border-top: 2px solid rgba(0, 0, 0, 0.32); }
.u-part.u-occupied.u-bottom { border-bottom: 2px solid rgba(0, 0, 0, 0.32); }
/* 「跨多 U 中間不要有分隔線」由 partVStyle() 用行內 style 處理 ——
   行內樣式會蓋掉這裡的 CSS，寫在這層是沒有用的。 */
.u-part.u-hl { box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24; }
.u-part.u-gap { color: rgba(127, 127, 127, 0.5); justify-content: center; }
.u-part.u-pickable { cursor: pointer; }
.u-part.u-pickable:hover { background: rgba(24, 160, 88, 0.14); color: var(--primary-color, #18a058); }
.u-part.u-pickable:hover .u-plus { opacity: 1; }
.d-name-half { max-width: 100%; padding: 0 3px; }
/* 名稱跨整台裝置置中。
   **一定要絕對定位並貼齊 top**：只給高度的話，它會以所在的那一格為中心上下溢出，
   文字仍然停在第一格的中央 —— 也就是原本 2U 偏高半格的老問題，換個寫法而已。 */
.d-name-span {
  position: absolute;
  left: 0;
  right: 0;
  /* 跨多 U 時名稱會溢出「自己那一格」，而下面幾格是後面才畫的兄弟元素 ——
     沒有這個 z-index，2U 的名稱會被下一格蓋掉一半、4U 的整個看不見（實測）。 */
  z-index: 2;
  /* 絕對定位是相對「內距框」，但裝置的可見範圍是「邊框框」——
     不補回上邊框的厚度，名稱會整個往下偏一個邊框的量（1U 實測差 2px）。 */
  top: calc(-1 * var(--rd-border, 2px));
  padding: 0 8px;
  display: flex;
  align-items: center;
  justify-content: inherit;
  pointer-events: none;
}
.d-name-span-half { padding: 0 3px; }
.u-num {
  display: inline-block;
  width: 22px;
  text-align: right;
  margin-right: 6px;
  opacity: 0.8;
  font-weight: bold;
  flex-shrink: 0;
}
.d-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* #32：用實際裝置寬度（外層 .d-name-span 已 left:0/right:0 撐滿該格），
     不要再套半 U 的固定上限，否則全寬裝置的名稱會在半 U 處就被截斷。 */
  max-width: 100%;
}
/* ⚠️ `max-width` 只能放在**內層**：放在絕對定位的外層時，`left:0; right:0` 與
   `max-width` 同時成立會讓瀏覽器保留 left、丟掉 right，整個名稱框被釘在左邊 ——
   畫面上就是「對齊設定選了置中卻沒反應」（實測 span 寬 126、整列寬 250）。 */
.d-ip {
  margin-left: auto;
  font-size: 11px;
  opacity: 0.85;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 90px;
}
/* bare：去掉卡片外框/底色/標題與內距，純嵌入。
   選擇器要比 App.vue 的 `html[data-theme="dark"] .n-card`（全站卡片外框）更明確，否則深色主題下
   外框還在（儀表板機櫃卡片看得出來，2026-10-01） */
.n-card.rd-bare { background: transparent; box-shadow: none; border: none; }
.rd-bare :deep(.n-card__content) { padding: 0; }
.rd-bare :deep(.n-card-header) { display: none; }
/* 聚焦模式（裝置詳細頁標示本機）：其他裝置**維持原色**，本機加粗琥珀框。
   以前是把其他裝置疊一層 62% 白色淡化，結果整櫃變成深淺不一的灰、白字看不到（10-23 客戶）。
   同上：不用 opacity／filter（會建立堆疊環境、跨 U 的名稱被蓋掉）。 */
.u-part.u-self { box-shadow: inset 3px 0 0 #f59e0b, inset -3px 0 0 #f59e0b; }
.u-part.u-self.u-top { box-shadow: inset 3px 0 0 #f59e0b, inset -3px 0 0 #f59e0b, inset 0 3px 0 #f59e0b; }
.u-part.u-self.u-bottom { box-shadow: inset 3px 0 0 #f59e0b, inset -3px 0 0 #f59e0b, inset 0 -3px 0 #f59e0b; }
.u-part.u-self.u-top.u-bottom { box-shadow: inset 0 0 0 3px #f59e0b; }
.u-part.u-self .d-name { font-weight: 800; }
/* compact：較小列高，給裝置詳細資料側欄用 */
/* 高度由上面的 rowPx 決定（行內 style），這裡只調字級 —— 兩邊各設一次就會打架 */
/* 整張是用 transform 等比縮的，字也會跟著縮 —— 先除以縮放倍率，畫出來剛好是想要的字級。
   （--rd-fit 只含「自動縮到放得下」那一段；使用者拉的縮放本來就該讓字一起變小。） */
.rd-compact .u-row { font-size: calc(10px / var(--rd-fit, 1)); }
/* 縮圖整張等比縮小、字級用 --rd-fit 補回來；編號欄的寬度與內距也要一起補，否則兩位數擠不下會折成兩行，
   十位數疊到下一格（42U 縮圖在裝置頁上，客戶 2026-10-12 回報）。 */
.rd-compact .u-num-out { font-size: calc(9px / var(--rd-fit, 1)); min-width: calc(22px / var(--rd-fit, 1));
  padding-right: calc(4px / var(--rd-fit, 1)); white-space: nowrap; }
.rd-compact .d-name { font-size: calc(10px / var(--rd-fit, 1)); max-width: 100%; }
.rd-compact .d-name-half { font-size: calc(9px / var(--rd-fit, 1)); }
.rd-compact :deep(.n-card-header) { padding: 10px 14px; }
.rd-compact :deep(.n-card-header__main) { font-size: 13px; }
.rack-tip { font-size: 12px; line-height: 1.6; min-width: 150px; }
.rack-tip .rt-name { font-weight: 700; margin-bottom: 4px; font-size: 13px; }
.rack-tip .rt-row { display: flex; justify-content: space-between; gap: 16px; }
.rack-tip .rt-row > span { opacity: 0.65; }
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
}
.legend-item {
  padding: 2px 8px;
  border-radius: 3px;
  color: white;
  font-family: monospace;
}
.legend-note {
  font-size: 12px;
  opacity: 0.7;
  align-self: center;
}
</style>
