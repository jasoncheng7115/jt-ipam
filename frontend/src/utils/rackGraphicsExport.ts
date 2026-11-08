// 機櫃／層架示意圖的圖形匯出（SVG / PNG / draw.io）。
//
// 同一套幾何供「單一機櫃」與「合併單卡（整個機房並排）」共用：傳入 diagrams 陣列即可，
// 多櫃會像畫面一樣並排、**底部對齊**（矮的往下推）。
//
// ⚠️ 這支以前是「每列固定 24px、一台裝置佔滿整列」的模型，與畫面早就對不上了：
//   - 對齊基準 `alignTo` 在畫面上已改成**像素**（層架的列高是機櫃的好幾倍，用 U 數算會
//     把層架推出畫面），匯出這邊還當成 U 數去乘列高 → 圖高變成數萬 px，整張幾乎全空白。
//   - 橫向位置還在讀早就被 rack_slot/rack_slot_span 取代的 `rack_side`，層內上下疊放
//     （rack_vslot）更是完全沒有 → 並排的兩台互相蓋住、疊放的看不出來。
//   - 層架被當成機櫃畫：沒有層板、每層一樣高、放在頂板上面的那台整個不見。
// 現在幾何直接吃後端給的 render_* 像素值，與畫面同一套。
import { RACK_OTHER_COLOR, rackTypeColor as colorFor } from "@/utils/rackColors";
import { boardList } from "@/utils/rackSlots";
import { finishColors, normalizeFinish, PLY_COLOR, ANGLE_HOLE_PITCH_PX, ANGLE_PLY_PX } from "@/utils/rackFinish";
import { saveBlob } from "@/utils/saveFile";
import { groupBrackets } from "@/utils/rackGroups";

export type RackNameAlign = "left" | "center" | "right";

/** 匯出需要的機櫃資料 —— 就是 `/racks/{id}/diagram` 回的那份。 */
export interface ExportDiagram {
  name: string;
  u_height: number;
  devices: any[];
  kind?: string | null;
  numbering?: string | null;
  open_top?: boolean;
  render_row_px?: number;
  render_row_px_list?: number[];
  render_board_px?: number;
  render_width_px?: number;
  /** 機櫃兩側的走線空間各幾 px（19 吋設備區以外）；KALLAX／角鋼層架／LackRack 是兩側的
   *  外框／立柱／桌腳；其他層架是 0 */
  render_side_px?: number;
  /** 每一列底下那片板的厚度 px（由上往下）；沒給就是每片一樣厚 */
  render_board_px_list?: number[];
  /** 最上面那一列之上的厚度 px（LackRack 的桌面） */
  render_top_px?: number;
  finish?: string | null;
  render_cols?: number;
  render_divider_px?: number;
  /** 「20U」或「9 層」；文案在元件那一端翻譯，這裡只負責畫。 */
  rowsLabel?: string;
  /** 開放頂那一列的標籤（預設「頂」）。 */
  topLabel?: string;
}

export interface RowBox {
  /** 層號／U 號；開放頂那一列是 u_height + 1 */
  u: number;
  isTop: boolean;
  /** 距離機櫃頂端多少 px */
  y: number;
  /** 這一列的高度，**含**下緣那片層板 */
  h: number;
  /** 下緣那片層板多厚（KALLAX 的頂板、底板與 LackRack 的桌面比較厚） */
  board: number;
}

const SLOTS = 60;                     // 與後端 rack.py 的 RACK_SLOTS 一致
const GEO = { gutter: 32, pad: 12, headerH: 30 };
const COL_GAP = 40;                   // 機櫃之間的水平間距
const SHELF_KINDS = new Set(["shelf", "wire_shelf", "wood_shelf", "angle_shelf", "kallax"]);
/** 兩側的外框／立柱／桌腳就是 render_side_px 本身（不另外畫導軌或側架） */
const SIDE_IS_FRAME = new Set(["angle_shelf", "kallax", "lackrack"]);

function esc(s: string): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function boardPx(d: ExportDiagram): number {
  return Number(d.render_board_px ?? 0) || 0;
}

function colWidth(d: ExportDiagram): number {
  return Math.max(140, Number(d.render_width_px ?? 260) || 260);
}

/** 機櫃兩側的走線空間（px）。畫面用的是同一個值（RackDiagram 的 --rd-side）。 */
export function sidePx(d: ExportDiagram): number {
  const k = d.kind || "";
  return SHELF_KINDS.has(k) && !SIDE_IS_FRAME.has(k) ? 0 : Math.max(0, Number(d.render_side_px ?? 0) || 0);
}

/**
 * 由上而下的每一列（含「頂板上方」那一列）與它在圖上的 y。
 *
 * 與畫面的 cells 同一套規則：層高逐層可調、開放頂多一列且沿用最上層的高度、
 * bottom-up 時第 1 層畫在最上面，但「頂」永遠是實體最上面那一列。
 */
export function rowBoxes(d: ExportDiagram): RowBox[] {
  const real = Math.max(0, Number(d.u_height ?? 0));
  if (!real) return [];
  const openTop = Boolean(d.open_top);
  const n = real + (openTop ? 1 : 0);
  const base = Number(d.render_row_px ?? 28) || 28;
  const list = (d.render_row_px_list ?? []).slice(0, real).map((v) => Number(v) || base);
  while (list.length < real) list.push(base);
  const pxOf = (u: number) => list[u - 1] ?? list[list.length - 1] ?? base;

  const bottomUp = d.numbering === "bottom-up";
  const order = bottomUp
    ? Array.from({ length: n }, (_, i) => i + 1)
    : Array.from({ length: n }, (_, i) => n - i);
  if (openTop && bottomUp) {
    const i = order.indexOf(n);
    if (i > 0) order.unshift(...order.splice(i, 1));
  }
  const boards = boardList(d, order.length);
  const out: RowBox[] = [];
  // LackRack 最上面是桌面：列從桌面底下開始
  let y = Number(d.render_top_px ?? 0) || 0;
  order.forEach((u, i) => {
    const h = pxOf(u) + boards[i];
    out.push({ u, isTop: u > real, y, h, board: boards[i] });
    y += h;
  });
  return out;
}

/**
 * 側架／立柱的範圍。開放頂的層架**立柱只到最上面那片層板為止** —— 板子上面是開放的，
 * 東西就放在那裡，柱子不會再往上長（IVAR 實物就是這樣，畫面也是這樣畫的）。
 */
export function postSpan(d: ExportDiagram): { y: number; h: number } {
  const rows = rowBoxes(d);
  const total = rows.length ? rows[rows.length - 1].y + rows[rows.length - 1].h : 0;
  if (!rows.length || !rows[0].isTop) return { y: 0, h: total };
  // 第一列是「頂板上方」：柱子從那片板的**頂端**起算（板厚不扣，板本身是柱子撐著的）
  const y = Math.max(0, rows[0].y + rows[0].h - rows[0].board);
  return { y, h: total - y };
}

/** 一台機櫃畫出來多高（px）。並排對齊要用這個，不是 U 數。 */
export function rackHeightPx(d: ExportDiagram): number {
  const rows = rowBoxes(d);
  return rows.length ? rows[rows.length - 1].y + rows[rows.length - 1].h : 0;
}

/**
 * 一台裝置在機櫃內的方塊（x 以機櫃左緣為 0）。
 *
 * 橫向是「起始格 + 跨幾格」的 60 格網格；層內上下同一套，而且**由下往上**長
 * （裝置站在層板上）。放不進任何一列就回 null —— 寧可不畫，也不要畫到奇怪的地方。
 */
export function deviceBox(
  d: ExportDiagram, dev: any, colW: number,
): { x: number; y: number; w: number; h: number } | null {
  const pos = Number(dev?.u_position);
  const size = Number(dev?.u_size);
  if (!Number.isFinite(pos) || !Number.isFinite(size) || pos <= 0 || size <= 0) return null;
  const rows = rowBoxes(d);
  const mine = rows.filter((r) => r.u >= pos && r.u < pos + size);
  if (!mine.length) return null;
  const yTop = Math.min(...mine.map((r) => r.y));
  const total = mine.reduce((a, r) => a + r.h, 0);
  const content = Math.max(1, total - mine[mine.length - 1].board);   // 最下面那片板不是可放空間

  const s0 = Number(dev.rack_slot ?? 0) || 0;
  const ss = Number(dev.rack_slot_span ?? SLOTS) || SLOTS;
  const v0 = Number(dev.rack_vslot ?? 0) || 0;
  const vs = Number(dev.rack_vslot_span ?? SLOTS) || SLOTS;
  return {
    x: (s0 / SLOTS) * colW,
    w: (ss / SLOTS) * colW,
    h: (vs / SLOTS) * content,
    y: yTop + content - ((v0 + vs) / SLOTS) * content,
  };
}

/**
 * 裝置群組的括線（與畫面 RackDiagram.vue 的群組軌同一套規則，見 utils/rackGroups.ts）。
 * 匯出圖不分正背面，兩面的裝置都畫，所以兩面的成員都框進來。座標相對於機櫃頂端。
 */
const GROUP_LANE_PX = 10;
const GROUP_LABEL_PX = 130;
interface GroupMark { id: string; color: string; label: string; lane: number; y: number; h: number; ticks: number[] }
function groupMarks(d: ExportDiagram, colW: number): { marks: GroupMark[]; lanes: number } {
  const devs = (d.devices || []).map((x: any) => ({ ...x, rack_face: "front" as const }));
  const marks: GroupMark[] = [];
  for (const b of groupBrackets(devs, "front")) {
    const boxes = b.members.map((m) => deviceBox(d, m, colW)).filter((x): x is NonNullable<typeof x> => !!x);
    if (!boxes.length) continue;
    const y = Math.min(...boxes.map((x) => x.y));
    const bottom = Math.max(...boxes.map((x) => x.y + x.h));
    const name = b.name.length > 16 ? `${b.name.slice(0, 15)}…` : b.name;
    marks.push({ id: b.id, color: b.color, label: `${name} ${b.here}/${b.total}`, lane: b.lane, y,
                 h: Math.max(10, bottom - y), ticks: boxes.map((x) => x.y + x.h / 2 - y) });
  }
  return { marks, lanes: marks.reduce((a, m) => Math.max(a, m.lane + 1), 0) };
}
/** 有群組的機櫃右側要多留的寬度（括線軌道＋標籤） */
function groupRailW(d: ExportDiagram): number {
  const { lanes } = groupMarks(d, colWidth(d));
  return lanes ? lanes * GROUP_LANE_PX + GROUP_LABEL_PX : 0;
}

/** 並排版面：每台機櫃的左緣與頂端 y（底部對齊）。 */
function layout(diagrams: ExportDiagram[], alignToPx: number) {
  const { gutter, pad, headerH } = GEO;
  const heights = diagrams.map(rackHeightPx);
  const maxH = Math.max(alignToPx || 0, ...heights, 1);
  let x = pad;
  const blocks = diagrams.map((d, i) => {
    const colW = colWidth(d);
    const side = sidePx(d);
    const rackLeft = x + gutter + side;          // rackLeft＝19 吋設備區的左緣
    x += gutter + side * 2 + colW + COL_GAP + groupRailW(d);
    return { d, rackLeft, colW, side, top: headerH + pad + (maxH - heights[i]), h: heights[i] };
  });
  return { blocks, W: x - COL_GAP + pad, H: headerH + pad * 2 + maxH };
}

/** 層架的側架寬度（畫面上是 14px 的松木／鍍鉻立柱；機櫃是導軌）。 */
function sideWidth(kind: string | null | undefined): number {
  if (SIDE_IS_FRAME.has(kind || "")) return 0;       // 兩側就是 side 本身
  return SHELF_KINDS.has(kind || "") ? 12 : 8;
}

/** 三種新型態的外觀（SVG）。回 [畫在裝置下面的, 畫在裝置上面的]，與後端 rack_svg.py 同一套畫法。 */
function newKindSvg(d: ExportDiagram, rows: RowBox[], rackLeft: number, colW: number, side: number,
                    top: number, h: number, defs: Set<string>): [string[], string[]] {
  const kind = d.kind ?? "";
  const c = finishColors(kind, d.finish);
  const under: string[] = [];
  const over: string[] = [];
  if (kind === "angle_shelf") {
    const fin = normalizeFinish(kind, d.finish);
    const cx = (side * 0.56).toFixed(2);
    const cy = (ANGLE_HOLE_PITCH_PX * 0.3).toFixed(2);
    defs.add(`<pattern id="keyholes-${fin}" width="${side.toFixed(3)}" height="${ANGLE_HOLE_PITCH_PX.toFixed(3)}" patternUnits="userSpaceOnUse">`
      + `<circle cx="${cx}" cy="${cy}" r="2.3" fill="${c.hole}"/>`
      + `<rect x="${(side * 0.56 - 0.8).toFixed(2)}" y="${cy}" width="1.6" height="${(ANGLE_HOLE_PITCH_PX * 0.32).toFixed(2)}" fill="${c.hole}"/></pattern>`);
    // 橫桿勾在兩支立柱之間、立柱在前面：先畫橫桿＋夾板，再畫立柱
    for (const r of rows) {
      if (r.board <= 0) continue;
      const y0 = top + r.y + r.h - r.board;
      const ply = Math.min(ANGLE_PLY_PX, r.board / 2);
      under.push(`<rect x="${rackLeft}" y="${y0 + ply}" width="${colW}" height="${r.board - ply}" fill="${c.beam}"/>`);
      under.push(`<rect class="angle-ply" x="${rackLeft}" y="${y0}" width="${colW}" height="${ply}" fill="${PLY_COLOR}"/>`);
    }
    const post = postSpan(d);
    for (const [x, ex] of [[rackLeft - side, rackLeft - side], [rackLeft + colW, rackLeft + colW + side - 2]]) {
      under.push(`<rect class="angle-post" x="${x}" y="${top + post.y}" width="${side}" height="${post.h}" fill="${c.post}"/>`);
      under.push(`<rect x="${x}" y="${top + post.y}" width="${side}" height="${post.h}" fill="url(#keyholes-${fin})"/>`);
      under.push(`<rect x="${ex}" y="${top + post.y}" width="2" height="${post.h}" fill="${c.edge}"/>`);
    }
  } else if (kind === "kallax") {
    const cols = Math.max(1, Number(d.render_cols ?? 1));
    const div = Number(d.render_divider_px ?? 0) || 0;
    const boxTop = top + rows[0].y + rows[0].h - rows[0].board;
    const boxBot = top + h;
    under.push(`<rect x="${rackLeft - side}" y="${boxTop}" width="${colW + 2 * side}" height="${boxBot - boxTop}" fill="${c.frame}" stroke="${c.line}" stroke-width="1"/>`);
    const cw = (colW - (cols - 1) * div) / cols;
    for (const r of rows) {
      if (r.isTop) continue;
      for (let k = 0; k < cols; k++) {
        under.push(`<rect class="kallax-cell" x="${rackLeft + k * (cw + div)}" y="${top + r.y}" width="${cw}" height="${r.h - r.board}" fill="${c.cell}"/>`);
      }
    }
    const innerTop = boxTop + rows[0].board;
    const innerBot = boxBot - rows[rows.length - 1].board;
    for (let k = 1; k < cols; k++) {
      over.push(`<rect class="kallax-divider" x="${rackLeft + k * (cw + div) - div}" y="${innerTop}" width="${div}" height="${innerBot - innerTop}" fill="${c.frame}"/>`);
    }
  } else if (kind === "lackrack") {
    // 最上面一列是桌面上方（可以放東西），它底下那片板＝最上面那張桌子的桌面；
    // 疊起來的桌子之間那片板是「上面那張的腳下空隙＋下面那張的桌面」，桌面在最下面那一截
    const slab = rows[0]?.board ?? 0;
    const legTop = top + (rows[0] ? rows[0].y + rows[0].h - slab : 0);
    for (const x of [rackLeft - side, rackLeft + colW]) {
      under.push(`<rect class="lack-leg" x="${x}" y="${legTop}" width="${side}" height="${top + h - legTop}" fill="${c.wood}" stroke="${c.line}" stroke-width="1"/>`);
    }
    for (const r of rows.filter((x) => x.board > 0)) {
      under.push(`<rect class="lack-top" x="${rackLeft - side}" y="${top + r.y + r.h - slab}" width="${colW + 2 * side}" height="${slab}" fill="${c.wood}" stroke="${c.line}" stroke-width="1"/>`);
    }
  }
  return [under, over];
}

function boardColor(kind: string | null | undefined): string {
  if (kind === "wood_shelf") return "#b5813f";
  if (kind === "wire_shelf") return "#b9bfc5";
  return "#9aa0a6";
}

function postColor(kind: string | null | undefined): string {
  if (kind === "wood_shelf") return "#d4ae7f";
  if (kind === "wire_shelf") return "#c8ced4";
  return "#c3c8ce";
}

export function buildRacksSvg(
  diagrams: ExportDiagram[], alignToPx: number, nameAlign: RackNameAlign,
): { svg: string; W: number; H: number } | null {
  if (!diagrams.length) return null;
  const { pad } = GEO;
  const { blocks, W, H } = layout(diagrams, alignToPx);
  const p: string[] = [];
  const defs = new Set<string>();
  p.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" font-family="sans-serif">`);
  const defsAt = p.length;
  p.push(`<rect x="0" y="0" width="${W}" height="${H}" fill="#ffffff"/>`);
  for (const { d, rackLeft, colW, side, top, h } of blocks) {
    const kind = d.kind ?? "rack";
    const shelf = SHELF_KINDS.has(kind);
    const fresh = SIDE_IS_FRAME.has(kind);       // 三種新型態自己畫（見 newKindSvg）
    const rows = rowBoxes(d);
    const board = boardPx(d);
    const sw = sideWidth(kind);
    const label = d.rowsLabel ?? `${d.u_height}U`;
    p.push(`<text x="${rackLeft - sw - side}" y="${pad + 16}" font-size="14" font-weight="bold">${esc(d.name)} (${esc(label)})</text>`);
    const [under, over] = fresh ? newKindSvg(d, rows, rackLeft, colW, side, top, h, defs) : [[], []];
    p.push(...under);

    // 機櫃是箱體、層架只有兩支側架 + 層板
    if (!shelf && !fresh) {
      if (side > 0) {
        // 櫃體外殼＋兩側走線空間（刻線＝理線槽），與畫面的 --rd-side 同一個值
        const x0 = rackLeft - sw - side;
        p.push(`<rect x="${x0}" y="${top}" width="${colW + 2 * sw + 2 * side}" height="${h}" fill="#f7f8f9" stroke="#888" stroke-width="1.5"/>`);
        for (const cx of [x0, rackLeft + colW + sw]) {
          p.push(`<rect class="rack-channel" x="${cx}" y="${top}" width="${side}" height="${h}" fill="#eceef0"/>`);
          for (let ty = top + 7; ty < top + h; ty += 14) {
            p.push(`<line x1="${cx + 2}" y1="${ty}" x2="${cx + side - 2}" y2="${ty}" stroke="#c4c9cf" stroke-width="1.5"/>`);
          }
        }
      }
      p.push(`<rect x="${rackLeft}" y="${top}" width="${colW}" height="${h}" fill="#f5f5f5" stroke="#888" stroke-width="1.5"/>`);
      // 頂板與底座：整個櫃寬的實心板（與畫面、嵌入圖同一組尺寸：頂 50mm、底 75mm）
      const roof = Number(d.render_top_px ?? 0) || 0;
      const base = rows[rows.length - 1]?.board ?? 0;
      const [fill, line] = kind === "industrial" ? ["#8a9098", "#5a5f69"] : ["#cdd2d8", "#888"];
      const x0 = rackLeft - sw - side;
      const fw = colW + 2 * sw + 2 * side;
      if (roof > 0) p.push(`<rect class="rack-roof" x="${x0}" y="${top}" width="${fw}" height="${roof}" fill="${fill}" stroke="${line}" stroke-width="1"/>`);
      if (base > 0) p.push(`<rect class="rack-base" x="${x0}" y="${top + h - base}" width="${fw}" height="${base}" fill="${fill}" stroke="${line}" stroke-width="1"/>`);
    }
    const post = postSpan(d);
    for (const px of (fresh ? [] : [rackLeft - sw, rackLeft + colW])) {
      p.push(`<rect x="${px}" y="${top + post.y}" width="${sw}" height="${post.h}" fill="${postColor(kind)}" stroke="#8a8f95" stroke-width="0.5"/>`);
    }

    for (const r of rows) {
      const y = top + r.y;
      const num = r.isTop ? (d.topLabel ?? "頂") : String(r.u);
      // 置中在那一列自己的空間（扣掉底下那片板；機櫃最下面那一 U 底下是底座）
      p.push(`<text x="${rackLeft - sw - side - 4}" y="${y + (r.h - r.board) / 2 + 4}" font-size="10" text-anchor="end" fill="#666">${esc(num)}</text>`);
      if (fresh) {
        if (kind === "lackrack") p.push(`<line x1="${rackLeft}" y1="${y}" x2="${rackLeft + colW}" y2="${y}" stroke="#dddddd" stroke-width="0.5"/>`);
      } else if (shelf && board > 0) {
        // 層板畫在每一列的下緣（頂板上方那一列的下緣就是頂板）
        p.push(`<rect x="${rackLeft}" y="${y + r.h - board}" width="${colW}" height="${board}" fill="${boardColor(kind)}"/>`);
      } else if (!shelf) {
        p.push(`<line x1="${rackLeft}" y1="${y}" x2="${rackLeft + colW}" y2="${y}" stroke="#dddddd" stroke-width="0.5"/>`);
      }
    }

    for (const dev of (d.devices || [])) {
      const b = deviceBox(d, dev, colW);
      if (!b) continue;
      const x = rackLeft + b.x;
      const y = top + b.y;
      // 「其他」與沒分類的：灰底加斜紋，跟畫面與對外嵌入 SVG（rack_svg.py 的 rd-other）一樣
      let fill = colorFor(dev.type);
      if (fill === RACK_OTHER_COLOR) {
        defs.add(`<pattern id="rd-other" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">`
          + `<rect width="14" height="14" fill="${RACK_OTHER_COLOR}"/><rect width="2" height="14" fill="rgba(255,255,255,0.13)"/></pattern>`);
        fill = "url(#rd-other)";
      }
      p.push(`<rect x="${x + 1}" y="${y + 1}" width="${Math.max(1, b.w - 2)}" height="${Math.max(1, b.h - 2)}" fill="${fill}" stroke="rgba(0,0,0,0.3)"/>`);
      const narrow = b.w < colW - 1;
      const a = nameAlign;
      const tx = narrow ? x + b.w / 2
        : a === "center" ? x + b.w / 2 : a === "right" ? x + b.w - 10 : x + 10;
      const anchor = narrow ? "middle" : a === "center" ? "middle" : a === "right" ? "end" : "start";
      p.push(`<text x="${tx}" y="${y + b.h / 2 + 4}" text-anchor="${anchor}" font-size="11" font-weight="bold" fill="#ffffff">${esc(dev.name ?? "")}</text>`);
      if (dev.rack_face === "rear") {
        const rx = x + b.w;
        p.push(`<path d="M${rx - 14} ${y + 1} L${rx} ${y + 1} L${rx} ${y + 15} Z" fill="rgba(0,0,0,0.55)"/>`);
        p.push(`<text x="${rx - 2}" y="${y + 11}" text-anchor="end" font-size="9" font-weight="bold" fill="#ffffff">R</text>`);
      }
    }
    p.push(...over);
    // 裝置群組：機櫃右側（側板外）一條同色括線，開口朝機櫃，每台成員一個刻度，標籤在所有軌道右邊
    const { marks, lanes } = groupMarks(d, colW);
    const railX = rackLeft + colW + sw + side + 6;
    for (const m of marks) {
      const bx = railX + m.lane * GROUP_LANE_PX;
      const y0 = top + m.y;
      const path = `M${bx} ${y0} H${bx + 6} V${y0 + m.h} H${bx}`;
      const ticks = m.ticks.map((ty) => `<line x1="${bx}" y1="${y0 + ty}" x2="${bx + 6}" y2="${y0 + ty}" stroke="${m.color}" stroke-width="2"/>`).join("");
      const lx = railX + lanes * GROUP_LANE_PX + 4;
      const lw = Math.min(GROUP_LABEL_PX - 8, m.label.length * 6.4 + 12);
      const ly = y0 + m.h / 2;
      p.push(`<g class="rack-group"><path d="${path}" fill="none" stroke="${m.color}" stroke-width="2"/>${ticks}`
        // 外框樣式（同畫面）：群組色邊框＋淡底、深色字
        + `<rect x="${lx}" y="${ly - 9}" width="${lw}" height="18" rx="4" fill="${m.color}" fill-opacity="0.15" stroke="${m.color}"/>`
        + `<text x="${lx + 6}" y="${ly + 4}" font-size="11" font-weight="bold" fill="#1f2937">${esc(m.label)}</text></g>`);
    }
  }
  if (defs.size) p.splice(defsAt, 0, `<defs>${[...defs].join("")}</defs>`);
  p.push(`</svg>`);
  return { svg: p.join("\n"), W, H };
}

export function buildRacksDrawio(
  diagrams: ExportDiagram[], alignToPx: number, nameAlign: RackNameAlign, title: string,
): string | null {
  if (!diagrams.length) return null;
  const { pad } = GEO;
  const { blocks } = layout(diagrams, alignToPx);
  const cells: string[] = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>'];
  let n = 0;
  for (const { d, rackLeft, colW, side, top, h } of blocks) {
    const kind = d.kind ?? "rack";
    const shelf = SHELF_KINDS.has(kind);
    const fresh = SIDE_IS_FRAME.has(kind);
    const rows = rowBoxes(d);
    const board = boardPx(d);
    const sw = sideWidth(kind);
    const label = d.rowsLabel ?? `${d.u_height}U`;
    const box = (id: string, x: number, y: number, w: number, hh: number, style: string) =>
      cells.push(`<mxCell id="${id}${n++}" value="" style="rounded=0;whiteSpace=wrap;html=1;${style}" vertex="1" parent="1"><mxGeometry x="${x}" y="${y}" width="${w}" height="${hh}" as="geometry"/></mxCell>`);
    const kallaxOver: (() => void)[] = [];
    if (fresh) {
      // 三種新型態：與 SVG 同一套形狀，draw.io 用純色方塊表示（葫蘆孔這類細節不畫）
      const c = finishColors(kind, d.finish);
      if (kind === "angle_shelf") {
        for (const r of rows) {
          if (r.board <= 0) continue;
          const y0 = top + r.y + r.h - r.board;
          const ply = Math.min(ANGLE_PLY_PX, r.board / 2);
          box("ab", rackLeft, y0 + ply, colW, r.board - ply, `fillColor=${c.beam};strokeColor=none;`);
          box("ap", rackLeft, y0, colW, ply, `fillColor=${PLY_COLOR};strokeColor=none;`);
        }
        const post = postSpan(d);
        for (const x of [rackLeft - side, rackLeft + colW]) {
          box("ag", x, top + post.y, side, post.h, `fillColor=${c.post};strokeColor=${c.edge};`);
        }
      } else if (kind === "kallax") {
        const cols = Math.max(1, Number(d.render_cols ?? 1));
        const div = Number(d.render_divider_px ?? 0) || 0;
        const boxTop = top + rows[0].y + rows[0].h - rows[0].board;
        box("kf", rackLeft - side, boxTop, colW + 2 * side, top + h - boxTop, `fillColor=${c.frame};strokeColor=${c.line};`);
        const cw = (colW - (cols - 1) * div) / cols;
        for (const r of rows) {
          if (r.isTop) continue;
          for (let k = 0; k < cols; k++) box("kc", rackLeft + k * (cw + div), top + r.y, cw, r.h - r.board, `fillColor=${c.cell};strokeColor=none;`);
        }
        const innerTop = boxTop + rows[0].board;
        const innerBot = top + h - rows[rows.length - 1].board;
        for (let k = 1; k < cols; k++) {
          kallaxOver.push(() => box("kd", rackLeft + k * (cw + div) - div, innerTop, div, innerBot - innerTop, `fillColor=${c.frame};strokeColor=none;`));
        }
      } else if (kind === "lackrack") {
        const slab = rows[0]?.board ?? 0;
        const legTop = top + (rows[0] ? rows[0].y + rows[0].h - slab : 0);
        for (const x of [rackLeft - side, rackLeft + colW]) box("ll", x, legTop, side, top + h - legTop, `fillColor=${c.wood};strokeColor=${c.line};`);
        for (const r of rows.filter((x) => x.board > 0)) {
          box("lt", rackLeft - side, top + r.y + r.h - slab, colW + 2 * side, slab, `fillColor=${c.wood};strokeColor=${c.line};`);
        }
      }
    }
    cells.push(`<mxCell id="t${n++}" value="${esc(`${d.name} (${label})`)}" style="text;html=1;align=left;verticalAlign=middle;fontStyle=1;fontSize=14;" vertex="1" parent="1"><mxGeometry x="${rackLeft - sw - side}" y="${pad}" width="${colW + 2 * side}" height="20" as="geometry"/></mxCell>`);
    if (!shelf && !fresh && side > 0) {
      // 櫃體外殼＋兩側走線空間
      const x0 = rackLeft - sw - side;
      cells.push(`<mxCell id="c${n++}" value="" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#f7f8f9;strokeColor=#888888;strokeWidth=2;" vertex="1" parent="1"><mxGeometry x="${x0}" y="${top}" width="${colW + 2 * sw + 2 * side}" height="${h}" as="geometry"/></mxCell>`);
      for (const cx of [x0, rackLeft + colW + sw]) {
        cells.push(`<mxCell id="m${n++}" value="" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#eceef0;strokeColor=#c4c9cf;dashed=1;" vertex="1" parent="1"><mxGeometry x="${cx}" y="${top}" width="${side}" height="${h}" as="geometry"/></mxCell>`);
      }
    }
    if (!shelf && !fresh) {
      cells.push(`<mxCell id="r${n++}" value="" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#888888;strokeWidth=2;" vertex="1" parent="1"><mxGeometry x="${rackLeft}" y="${top}" width="${colW}" height="${h}" as="geometry"/></mxCell>`);
      const roof = Number(d.render_top_px ?? 0) || 0;
      const base = rows[rows.length - 1]?.board ?? 0;
      const [fill, line] = kind === "industrial" ? ["#8a9098", "#5a5f69"] : ["#cdd2d8", "#888888"];
      const x0 = rackLeft - sw - side;
      if (roof > 0) box("rr", x0, top, colW + 2 * sw + 2 * side, roof, `fillColor=${fill};strokeColor=${line};`);
      if (base > 0) box("rb", x0, top + h - base, colW + 2 * sw + 2 * side, base, `fillColor=${fill};strokeColor=${line};`);
    }
    const post = postSpan(d);
    for (const px of (fresh ? [] : [rackLeft - sw, rackLeft + colW])) {
      cells.push(`<mxCell id="p${n++}" value="" style="rounded=0;whiteSpace=wrap;html=1;fillColor=${postColor(kind)};strokeColor=#8a8f95;" vertex="1" parent="1"><mxGeometry x="${px}" y="${top + post.y}" width="${sw}" height="${post.h}" as="geometry"/></mxCell>`);
    }
    for (const r of rows) {
      const y = top + r.y;
      const num = r.isTop ? (d.topLabel ?? "頂") : String(r.u);
      cells.push(`<mxCell id="u${n++}" value="${esc(num)}" style="text;html=1;align=right;verticalAlign=middle;fontSize=10;fontColor=#666666;" vertex="1" parent="1"><mxGeometry x="${rackLeft - sw - side - 28}" y="${y}" width="24" height="${r.h - r.board}" as="geometry"/></mxCell>`);
      if (shelf && !fresh && board > 0) {
        cells.push(`<mxCell id="b${n++}" value="" style="rounded=0;whiteSpace=wrap;html=1;fillColor=${boardColor(kind)};strokeColor=none;" vertex="1" parent="1"><mxGeometry x="${rackLeft}" y="${y + r.h - board}" width="${colW}" height="${board}" as="geometry"/></mxCell>`);
      }
    }
    for (const dev of (d.devices || [])) {
      const b = deviceBox(d, dev, colW);
      if (!b) continue;
      const align = b.w < colW - 1 ? "center" : nameAlign;
      cells.push(`<mxCell id="dev${n++}" value="${esc(dev.name ?? "")}" style="rounded=0;whiteSpace=wrap;html=1;fillColor=${colorFor(dev.type)};strokeColor=#000000;fontColor=#ffffff;fontStyle=1;align=${align};spacingLeft=6;spacingRight=6;" vertex="1" parent="1"><mxGeometry x="${rackLeft + b.x + 1}" y="${top + b.y + 1}" width="${Math.max(1, b.w - 2)}" height="${Math.max(1, b.h - 2)}" as="geometry"/></mxCell>`);
    }
    for (const f of kallaxOver) f();
    // 裝置群組：右開的括線（只畫上、右、下三邊）＋標籤
    const { marks, lanes } = groupMarks(d, colW);
    const railX = rackLeft + colW + sw + side + 6;
    for (const m of marks) {
      cells.push(`<mxCell id="grp${n++}" value="" style="shape=partialRectangle;whiteSpace=wrap;html=1;left=0;top=1;right=1;bottom=1;fillColor=none;strokeColor=${m.color};strokeWidth=2;" vertex="1" parent="1"><mxGeometry x="${railX + m.lane * GROUP_LANE_PX}" y="${top + m.y}" width="6" height="${m.h}" as="geometry"/></mxCell>`);
      cells.push(`<mxCell id="gl${n++}" value="${esc(m.label)}" style="rounded=1;whiteSpace=wrap;html=1;fillColor=${m.color};fillOpacity=15;strokeColor=${m.color};fontColor=#1f2937;fontStyle=1;fontSize=11;align=left;spacingLeft=6;" vertex="1" parent="1"><mxGeometry x="${railX + lanes * GROUP_LANE_PX + 4}" y="${top + m.y + m.h / 2 - 9}" width="${GROUP_LABEL_PX - 8}" height="18" as="geometry"/></mxCell>`);
    }
  }
  return (
    `<mxfile host="jt-ipam"><diagram name="${esc(title)}">` +
    `<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" math="0" shadow="0">` +
    `<root>${cells.join("")}</root></mxGraphModel></diagram></mxfile>`
  );
}

export function downloadBlob(blob: Blob, filename: string): void {
  saveBlob(filename, blob, blob.type, { source: "rack-graphics" });
}

export function exportRacksSvg(
  diagrams: ExportDiagram[], alignToPx: number, nameAlign: RackNameAlign, filename: string,
): void {
  const r = buildRacksSvg(diagrams, alignToPx, nameAlign);
  if (!r) return;
  downloadBlob(new Blob([r.svg], { type: "image/svg+xml" }), `${filename}.svg`);
}

export function exportRacksPng(
  diagrams: ExportDiagram[], alignToPx: number, nameAlign: RackNameAlign, filename: string,
): void {
  const r = buildRacksSvg(diagrams, alignToPx, nameAlign);
  if (!r) return;
  const scale = 2;
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = r.W * scale;
    canvas.height = r.H * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    canvas.toBlob((blob) => { if (blob) downloadBlob(blob, `${filename}.png`); }, "image/png");
  };
  img.src = "data:image/svg+xml;base64," +
    btoa(unescape(encodeURIComponent(r.svg)));
}

export function exportRacksDrawio(
  diagrams: ExportDiagram[], alignToPx: number, nameAlign: RackNameAlign, filename: string,
): void {
  const xml = buildRacksDrawio(diagrams, alignToPx, nameAlign, filename);
  if (!xml) return;
  downloadBlob(new Blob([xml], { type: "application/xml" }), `${filename}.drawio`);
}
