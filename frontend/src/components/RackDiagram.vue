<script setup lang="ts">
/**
 * 機櫃 U 位視覺化 (phpIPAM 招牌功能)。
 *
 * 比 phpIPAM 改進：
 *  - 顏色按 device type 區分 (router/switch/firewall/server/...)
 *  - 越界 / 重疊衝突明顯標示
 *  - 點 device 跳詳情
 *  - U 編號從上到下標示，符合機房現場認知
 */
import { computed, ref, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NCard, NEmpty, NAlert, NSpace, NTooltip, NButton, NButtonGroup, NIcon, NDropdown } from "naive-ui";
import type { RackDiagram } from "@/api/racks";
import { rackTypeColor as colorFor } from "@/utils/rackColors";
import { exportTable, type ExportColumn } from "@/utils/tableExport";
import { ExportIcon } from "@/icons";
import { getRackNameAlign, type RackNameAlign } from "@/api/basic";

// 全域設定：機櫃中裝置名稱靠左/置中/靠右（管理員在系統設定調整）
const nameAlign = ref<RackNameAlign>("left");
onMounted(() => { void getRackNameAlign().then((a) => { nameAlign.value = a; }); });
const nameJustify = computed(() =>
  nameAlign.value === "center" ? "center" : nameAlign.value === "right" ? "flex-end" : "flex-start");

const { t } = useI18n();

const GEO = { rowH: 24, colW: 260, gutter: 32, pad: 12, headerH: 30 };
const esc = (s: unknown) => String(s ?? "").replace(/[<>&"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c] as string));

function devLabel(dev: any): string {
  // 機櫃示意圖（含匯出）只標裝置名稱，與畫面一致；不加類型 / IP
  return dev.name;
}

// 共用：產生機櫃 SVG 字串 + 尺寸
// 半 U（左/右）裝置在匯出時要各佔半寬，否則左右兩台會疊在一起
function partGeom(dev: any): { x: number; w: number; cx: number; half: boolean } {
  const { colW, gutter } = GEO;
  const side = dev.rack_side ?? "full";
  if (side === "left")
    return { x: gutter + 2, w: colW / 2 - 3, cx: gutter + colW / 4, half: true };
  if (side === "right")
    return { x: gutter + colW / 2 + 1, w: colW / 2 - 3, cx: gutter + (colW * 3) / 4, half: true };
  return { x: gutter + 2, w: colW - 4, cx: gutter + colW / 2, half: false };
}

function buildSvg(): { svg: string; W: number; H: number } | null {
  const d = props.diagram;
  if (!d) return null;
  const { rowH, colW, gutter, pad, headerH } = GEO;
  const U = d.u_height || 0;
  const W = gutter + colW + pad * 2;
  const H = headerH + U * rowH + pad * 2;
  const p: string[] = [];
  p.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" font-family="sans-serif">`);
  p.push(`<rect x="0" y="0" width="${W}" height="${H}" fill="#ffffff"/>`);
  p.push(`<text x="${pad}" y="${pad + 16}" font-size="14" font-weight="bold">Rack: ${esc(d.name)} (${U}U)</text>`);
  const top = headerH + pad;
  p.push(`<rect x="${gutter}" y="${top}" width="${colW}" height="${U * rowH}" fill="#f5f5f5" stroke="#888" stroke-width="1.5"/>`);
  for (let i = 0; i < U; i++) {
    const uNum = U - i;
    const y = top + i * rowH;
    p.push(`<text x="${gutter - 4}" y="${y + rowH / 2 + 4}" font-size="10" text-anchor="end" fill="#666">${uNum}</text>`);
    p.push(`<line x1="${gutter}" y1="${y}" x2="${gutter + colW}" y2="${y}" stroke="#dddddd" stroke-width="0.5"/>`);
  }
  for (const dev of (d.devices || [])) {
    if (!dev.u_position || !dev.u_size) continue;
    const uTop = dev.u_position + dev.u_size - 1;
    const yTop = top + (U - uTop) * rowH;
    const hgt = dev.u_size * rowH;
    const g = partGeom(dev);
    p.push(`<rect x="${g.x}" y="${yTop + 1}" width="${g.w}" height="${hgt - 2}" rx="3" fill="${colorFor(dev.type)}" stroke="rgba(0,0,0,0.3)"/>`);
    const a = nameAlign.value;
    // 半 U 太窄，一律置中；全寬才依名稱對齊偏好
    const tx = g.half ? g.cx : a === "center" ? gutter + colW / 2 : a === "right" ? gutter + colW - 10 : gutter + 10;
    const anchor = g.half ? "middle" : a === "center" ? "middle" : a === "right" ? "end" : "start";
    p.push(`<text x="${tx}" y="${yTop + hgt / 2 + 4}" text-anchor="${anchor}" font-size="11" font-weight="bold" fill="#ffffff">${esc(devLabel(dev))}</text>`);
    // 安裝於機櫃後側 → 右上角標一個 R 角標（前側為預設，不標）
    if (dev.rack_face === "rear") {
      const rx = g.x + g.w;
      p.push(`<path d="M${rx - 14} ${yTop + 1} L${rx} ${yTop + 1} L${rx} ${yTop + 15} Z" fill="rgba(0,0,0,0.55)"/>`);
      p.push(`<text x="${rx - 2}" y="${yTop + 11}" text-anchor="end" font-size="9" font-weight="bold" fill="#ffffff">R</text>`);
    }
  }
  p.push(`</svg>`);
  return { svg: p.join("\n"), W, H };
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function exportSvg() {
  const r = buildSvg();
  if (!r) return;
  download(new Blob([r.svg], { type: "image/svg+xml" }), `rack-${props.diagram!.name}.svg`);
}

// SVG → canvas → PNG（2x 解析度）
function exportPng() {
  const r = buildSvg();
  if (!r) return;
  const scale = 2;
  const img = new Image();
  const svgUrl = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(r.svg)));
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = r.W * scale; canvas.height = r.H * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    canvas.toBlob((blob) => { if (blob) download(blob, `rack-${props.diagram!.name}.png`); }, "image/png");
  };
  img.src = svgUrl;
}

// 匯出為 draw.io（.drawio）：mxGraphModel，機櫃框 + 每台裝置一個可編輯方塊
function exportDrawio() {
  const d = props.diagram;
  if (!d) return;
  const { rowH, colW, gutter, pad, headerH } = GEO;
  const U = d.u_height || 0;
  const top = headerH + pad;
  const cells: string[] = [];
  cells.push('<mxCell id="0"/>');
  cells.push('<mxCell id="1" parent="0"/>');
  // 標題（放外框上方，不與最上層裝置重疊）
  cells.push(`<mxCell id="title" value="${esc(`Rack: ${d.name} (${U}U)`)}" style="text;html=1;align=left;verticalAlign=middle;fontStyle=1;fontSize=14;" vertex="1" parent="1"><mxGeometry x="${gutter}" y="${pad}" width="${colW}" height="20" as="geometry"/></mxCell>`);
  // 機櫃外框：明確較粗框線（strokeWidth=2，對應示意圖的外框粗細）
  cells.push(`<mxCell id="rack" value="" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#888888;strokeWidth=2;" vertex="1" parent="1"><mxGeometry x="${gutter}" y="${top}" width="${colW}" height="${U * rowH}" as="geometry"/></mxCell>`);
  // 左側 U 數編號（與示意圖一致）
  for (let i = 0; i < U; i++) {
    const uNum = U - i;
    const y = top + i * rowH;
    cells.push(`<mxCell id="u${uNum}" value="${uNum}" style="text;html=1;align=right;verticalAlign=middle;fontSize=10;fontColor=#666666;" vertex="1" parent="1"><mxGeometry x="${gutter - 28}" y="${y}" width="24" height="${rowH}" as="geometry"/></mxCell>`);
  }
  let n = 0;
  for (const dev of (d.devices || [])) {
    if (!dev.u_position || !dev.u_size) continue;
    const uTop = dev.u_position + dev.u_size - 1;
    const yTop = top + (U - uTop) * rowH;
    const hgt = dev.u_size * rowH;
    const fill = colorFor(dev.type);
    const g = partGeom(dev);
    const align = g.half ? "center" : nameAlign.value;
    cells.push(`<mxCell id="dev${n++}" value="${esc(devLabel(dev))}" style="rounded=0;whiteSpace=wrap;html=1;fillColor=${fill};strokeColor=#000000;fontColor=#ffffff;fontStyle=1;align=${align};spacingLeft=6;spacingRight=6;" vertex="1" parent="1"><mxGeometry x="${g.x}" y="${yTop + 1}" width="${g.w}" height="${hgt - 2}" as="geometry"/></mxCell>`);
  }
  const xml =
    `<mxfile host="jt-ipam"><diagram name="${esc(d.name)}">` +
    `<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" math="0" shadow="0">` +
    `<root>${cells.join("")}</root></mxGraphModel></diagram></mxfile>`;
  download(new Blob([xml], { type: "application/xml" }), `rack-${d.name}.drawio`);
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
  { label: "純文字 (.txt)", key: "txt" },
]);
// 機櫃裝置清單的資料匯出（csv/xlsx/ods/md/txt）
function exportData(fmt: "csv" | "xlsx" | "ods" | "md" | "txt") {
  const d = props.diagram;
  if (!d) return;
  const cols: ExportColumn[] = [
    { key: "u_position", label: "U" },
    { key: "u_size", label: "U Size" },
    { key: "name", label: t("cols.name") },
    { key: "type", label: t("cols.type") },
    { key: "rack_face", label: t("racks.face") },
    { key: "primary_ip", label: "IP" },
    { key: "vendor", label: t("cols.vendor") },
    { key: "model", label: t("cols.model") },
  ];
  const rows = [...d.devices].sort((a, b) => (b.u_position ?? 0) - (a.u_position ?? 0));
  exportTable(fmt, `rack-${d.name}`, cols, rows as any, `Rack ${d.name}`);
}
function onExport(key: string) {
  if (key === "svg") exportSvg();
  else if (key === "png") exportPng();
  else if (key === "drawio") exportDrawio();
  else if (["csv", "xlsx", "ods", "md", "txt"].includes(key)) exportData(key as any);
}
const router = useRouter();
function goDevice(id: string) {
  router.push({ name: "device-detail", params: { id } });
}

interface Props {
  diagram: RackDiagram | null;
  showLegend?: boolean;   // 多機櫃並排時可關掉，由頁面放一個共用圖例
  editable?: boolean;     // admin：點空 U 位可挑裝置放入
  floorAlignTo?: number;  // 多機櫃並排時傳入該排最高 U 數 → 矮櫃頂端補空白，使底部(U1)靠下對齊
  highlightId?: string | null;  // 常駐高亮某裝置（裝置詳情頁標示本機在機櫃的位置）
  compact?: boolean;            // 較小列高（嵌在裝置詳情等空間有限處）
  bare?: boolean;               // 去掉卡片外框與標題（嵌入用）
  face?: "front" | "rear" | null;  // 外部強制指定檢視面（合併卡共用切換用）；null = 用自身切換
  controls?: boolean;              // 是否顯示自身的面切換 + 匯出（合併卡傳 false 改由外層統一）
}
const props = withDefaults(defineProps<Props>(), { showLegend: true, editable: false, floorAlignTo: 0, highlightId: null, compact: false, bare: false, face: null, controls: true });
const faceView = ref<"front" | "rear">("front");   // 機櫃正面 / 背面切換
// 實際採用的檢視面：外部有指定就用外部（合併卡共用），否則用自身切換
const effFace = computed(() => props.face ?? faceView.value);
const hasRear = computed(() => (props.diagram?.devices || []).some((d: any) => d.rack_face === "rear"));
const U_PX = 28;   // 每個 U 列高度（與 .u-row / .u-num-out 一致）
// 落地對齊：比該排最高櫃矮幾 U，就在頂端補幾 U 的空白
const floorPad = computed(() => {
  const u = props.diagram?.u_height ?? 0;
  return props.floorAlignTo > u ? (props.floorAlignTo - u) * U_PX : 0;
});
const emit = defineEmits<{ (e: "pick-empty", u: number, rackId: string, side?: "left" | "right"): void }>();
const hoveredId = ref<string | null>(null);   // hover 某 U → 整台裝置點亮+框線

interface DevPart {
  id: string;
  name: string;
  type: string;
  vendor: string | null;
  model: string | null;
  u_size: number;
  side: "full" | "left" | "right";
  is_top: boolean;     // device 最上格
  is_bottom: boolean;  // device 最下格
  is_mid: boolean;     // device 垂直中間格（顯示名字 → 跨多 U 置中）
  primary_ip: string | null;
}
interface Cell {
  u: number;                  // 1-based, top-most U
  full: DevPart | null;       // 整 U 全寬裝置
  left: DevPart | null;       // 半 U：左
  right: DevPart | null;      // 半 U：右
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
  const u_height = props.diagram.u_height;
  const bottomUp = props.diagram.numbering === "bottom-up";
  const map: Record<number, Cell> = {};
  for (let u = 1; u <= u_height; u++) map[u] = { u, full: null, left: null, right: null };
  const mk = (d: any, side: "full" | "left" | "right"): DevPart => ({
    id: d.device_id, name: d.name, type: d.type, vendor: d.vendor, model: d.model,
    u_size: d.u_size, side, is_top: false, is_bottom: false, is_mid: false,
    primary_ip: d.primary_ip,
  });
  for (const d of props.diagram.devices) {
    // 只顯示目前檢視面（正/背）的裝置；未標面者視為正面
    if (((d.rack_face ?? "front") as string) !== effFace.value) continue;
    const side = (d.rack_side ?? "full") as "full" | "left" | "right";
    for (let u = d.u_position; u < d.u_position + d.u_size; u++) {
      if (!map[u]) continue;
      if (side === "left") map[u].left = mk(d, "left");
      else if (side === "right") map[u].right = mk(d, "right");
      else map[u].full = mk(d, "full");
    }
  }
  // 顯示順序：top-down → 高 U 在上（u_height..1）；bottom-up → U1 在上（1..u_height）
  const order: Cell[] = bottomUp
    ? Array.from({ length: u_height }, (_, i) => map[i + 1])
    : Array.from({ length: u_height }, (_, i) => map[u_height - i]);
  // 三個占寬槽各自依「顯示上的鄰居」標出最上/最下/中間格
  for (const key of ["full", "left", "right"] as const) {
    const runs: Record<string, number[]> = {};
    order.forEach((c, i) => {
      const p = c[key];
      if (!p) return;
      const prev = order[i - 1]?.[key]?.id;
      const next = order[i + 1]?.[key]?.id;
      p.is_top = prev !== p.id;
      p.is_bottom = next !== p.id;
      (runs[p.id] ??= []).push(i);
    });
    for (const idxs of Object.values(runs)) {
      order[idxs[Math.floor((idxs.length - 1) / 2)]][key]!.is_mid = true;
    }
  }
  return order;
});
</script>

<template>
  <n-card v-if="diagram" class="rack-diagram-card" :class="{ 'rd-compact': compact, 'rd-bare': bare }"
          :bordered="!bare" :title="bare ? undefined : `${t('nav.racks')}: ${diagram.name} (${diagram.u_height}U)`">
    <template v-if="controls" #header-extra>
      <div style="display:flex; align-items:center; gap:8px">
        <n-button-group size="tiny">
          <n-button :type="faceView === 'front' ? 'primary' : 'default'" @click="faceView = 'front'">
            {{ t("racks.face_front") }}
          </n-button>
          <n-button :type="faceView === 'rear' ? 'primary' : 'default'" @click="faceView = 'rear'">
            {{ t("racks.face_rear") }}<span v-if="hasRear" style="margin-left:3px">•</span>
          </n-button>
        </n-button-group>
        <n-dropdown trigger="click" :options="exportOptions" @select="onExport">
          <n-button size="tiny" :title="t('rack_diagram.export_svg_hint')">
            <template #icon><n-icon><ExportIcon /></n-icon></template>
            {{ t("common.export") }}
          </n-button>
        </n-dropdown>
      </div>
    </template>
    <n-space vertical :size="12">
      <n-alert
        v-if="diagram.conflicts.length > 0"
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

      <div v-else class="rack-wrap" :style="floorPad ? { marginTop: floorPad + 'px' } : undefined">
        <!-- U 編號：機櫃框外左側 gutter -->
        <div class="u-gutter">
          <div v-for="cell in cells" :key="'g' + cell.u" class="u-num-out">{{ cell.u }}</div>
        </div>
        <div class="rack-frame">
          <template v-for="cell in cells" :key="cell.u">
            <!-- 整 U 全寬裝置：hover 即時彈出結構化資訊 -->
            <n-tooltip v-if="cell.full" trigger="hover" :delay="60" placement="right">
              <template #trigger>
                <div
                  class="u-row u-occupied"
                  :class="{ 'u-top': cell.full.is_top, 'u-bottom': cell.full.is_bottom, 'u-hl': hoveredId === cell.full.id || highlightId === cell.full.id, 'u-dim': !!highlightId && highlightId !== cell.full.id }"
                  :style="{ background: colorFor(cell.full.type), justifyContent: nameJustify }"
                  @mouseenter="hoveredId = cell.full.id"
                  @mouseleave="hoveredId = null"
                  @click="goDevice(cell.full.id)"
                >
                  <span v-if="cell.full.is_mid" class="d-name">{{ cell.full.name }}</span>
                </div>
              </template>
              <div class="rack-tip">
                <div class="rt-name">{{ cell.full.name }}</div>
                <div class="rt-row"><span>{{ t("cols.type") }}</span><b>{{ cell.full.type }}</b></div>
                <div v-if="cell.full.vendor" class="rt-row"><span>{{ t("cols.vendor") }}</span><b>{{ cell.full.vendor }}</b></div>
                <div v-if="cell.full.model" class="rt-row"><span>{{ t("cols.model") }}</span><b>{{ cell.full.model }}</b></div>
                <div v-if="cell.full.primary_ip" class="rt-row"><span>IP</span><b>{{ cell.full.primary_ip }}</b></div>
                <div class="rt-row"><span>{{ t("rack_diagram.height") }}</span><b>{{ cell.full.u_size }}U</b></div>
              </div>
            </n-tooltip>

            <!-- 半 U：左右各一格 -->
            <div v-else-if="cell.left || cell.right" class="u-row u-split">
              <template v-for="half in (['left','right'] as const)" :key="half">
                <n-tooltip v-if="cell[half]" trigger="hover" :delay="60" placement="right">
                  <template #trigger>
                    <div
                      class="u-half u-occupied"
                      :class="{ 'u-top': cell[half]!.is_top, 'u-bottom': cell[half]!.is_bottom, 'u-hl': hoveredId === cell[half]!.id || highlightId === cell[half]!.id, 'u-dim': !!highlightId && highlightId !== cell[half]!.id }"
                      :style="{ background: colorFor(cell[half]!.type) }"
                      @mouseenter="hoveredId = cell[half]!.id"
                      @mouseleave="hoveredId = null"
                      @click="goDevice(cell[half]!.id)"
                    >
                      <span v-if="cell[half]!.is_mid" class="d-name d-name-half">{{ cell[half]!.name }}</span>
                    </div>
                  </template>
                  <div class="rack-tip">
                    <div class="rt-name">{{ cell[half]!.name }}</div>
                    <div class="rt-row"><span>{{ t("cols.type") }}</span><b>{{ cell[half]!.type }}</b></div>
                    <div v-if="cell[half]!.vendor" class="rt-row"><span>{{ t("cols.vendor") }}</span><b>{{ cell[half]!.vendor }}</b></div>
                    <div v-if="cell[half]!.model" class="rt-row"><span>{{ t("cols.model") }}</span><b>{{ cell[half]!.model }}</b></div>
                    <div v-if="cell[half]!.primary_ip" class="rt-row"><span>IP</span><b>{{ cell[half]!.primary_ip }}</b></div>
                    <div class="rt-row"><span>{{ t("rack_diagram.height") }}</span><b>{{ cell[half]!.u_size }}U（{{ t('racks.rack_side_' + half) }}）</b></div>
                  </div>
                </n-tooltip>
                <div v-else class="u-half" :class="{ 'u-pickable': editable }"
                     :title="editable ? t('racks.pick_device_here') : `Empty (U${cell.u})`"
                     @click="editable && props.diagram && emit('pick-empty', cell.u, props.diagram.rack_id, half)">
                  <span v-if="editable" class="u-plus">＋</span>
                </div>
              </template>
            </div>

            <!-- 空位（整列） -->
            <div v-else class="u-row" :class="{ 'u-pickable': editable }"
                 :title="editable ? t('racks.pick_device_here') : `Empty (U${cell.u})`"
                 @click="editable && props.diagram && emit('pick-empty', cell.u, props.diagram.rack_id)">
              <span v-if="editable" class="u-plus">＋</span>
            </div>
          </template>
        </div>
      </div>

      <div v-if="showLegend" class="legend">
        <span class="legend-item" :style="{ background: colorFor('router') }">router</span>
        <span class="legend-item" :style="{ background: colorFor('switch') }">switch</span>
        <span class="legend-item" :style="{ background: colorFor('firewall') }">firewall</span>
        <span class="legend-item" :style="{ background: colorFor('server') }">server</span>
        <span class="legend-item" :style="{ background: colorFor('storage') }">storage</span>
        <span class="legend-item" :style="{ background: colorFor('ap') }">ap</span>
        <span class="legend-item" :style="{ background: colorFor('ipmi') }">ipmi</span>
        <span class="legend-note">{{ t("racks.rear_legend") }}</span>
      </div>
    </n-space>
  </n-card>
</template>

<style scoped>
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
  width: 26px;
  padding-right: 6px;
  font: bold 12px ui-monospace, SFMono-Regular, Menlo, monospace;
  color: rgba(127, 127, 127, 0.75);
}
.rack-frame {
  border: 2px solid rgba(127, 127, 127, 0.5);
  border-radius: 4px;
  padding: 4px;
  width: 250px;
  background: rgba(127, 127, 127, 0.04);
}
.u-row {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  height: 28px;
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
/* hover 任一 U → 整台裝置點亮 + 框線（左右框；最上/最下格補上下框） */
.u-row.u-hl {
  filter: brightness(1.18) saturate(1.25);
  box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24;
  z-index: 1;
}
.u-row.u-hl.u-top { box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24, inset 0 2px 0 #fbbf24; }
.u-row.u-hl.u-bottom { box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24, inset 0 -2px 0 #fbbf24; }
.u-row.u-hl.u-top.u-bottom { box-shadow: inset 0 0 0 2px #fbbf24; }
/* 半 U：一列拆左右兩格。容器列不畫底線（否則會橫跨在多 U 半 U 裝置中間），
   分隔交給內層 .u-half 各自的上下框處理。 */
.u-row.u-split { padding: 0; align-items: stretch; border-bottom: none; }
.u-half {
  box-sizing: border-box; flex: 1 1 50%; min-width: 0;
  display: flex; align-items: center; justify-content: center;
  height: 100%; font-size: 11px; font-family: monospace; color: white; position: relative;
  border-bottom: 1px dashed rgba(127, 127, 127, 0.2);
}
.u-half:first-child { border-right: 1px dashed rgba(127, 127, 127, 0.28); }
.u-half:not(.u-occupied) { color: rgba(127, 127, 127, 0.5); }
.u-half.u-occupied {
  border-bottom: none;
  border-left: 2px solid rgba(0, 0, 0, 0.32);
  border-right: 2px solid rgba(0, 0, 0, 0.32);
  cursor: pointer;
}
.u-half.u-occupied.u-top { border-top: 2px solid rgba(0, 0, 0, 0.32); }
.u-half.u-occupied.u-bottom { border-bottom: 2px solid rgba(0, 0, 0, 0.32); }
.u-half.u-hl { filter: brightness(1.18) saturate(1.25); box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24; z-index: 1; }
.u-half.u-pickable { cursor: pointer; color: var(--n-text-color-3, #999); }
.u-half.u-pickable:hover { background: rgba(24, 160, 88, 0.14); color: var(--primary-color, #18a058); }
.u-half.u-pickable:hover .u-plus { opacity: 1; }
.d-name-half { max-width: 100%; padding: 0 3px; }
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
  max-width: 110px;
}
.d-ip {
  margin-left: auto;
  font-size: 11px;
  opacity: 0.85;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 90px;
}
/* bare：去掉卡片外框/底色/標題與內距，純嵌入 */
.rd-bare { background: transparent; box-shadow: none; border: none; }
.rd-bare :deep(.n-card__content) { padding: 0; }
.rd-bare :deep(.n-card-header) { display: none; }
/* 聚焦模式：設了 highlightId 時，其他裝置淡化，只突顯本裝置 */
.u-dim { opacity: 0.32; filter: grayscale(0.4); }
.u-dim .d-name { opacity: 0.7; }
/* compact：較小列高，給裝置詳情側欄用 */
.rd-compact .u-row { height: 18px; font-size: 10px; }
.rd-compact .u-num-out { height: 18px; font-size: 9px; }
.rd-compact .d-name { font-size: 10px; max-width: 90px; }
.rd-compact .d-name-half { font-size: 9px; }
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
