// 機櫃示意圖的圖形匯出（SVG / PNG / draw.io）。
// 同一套邏輯供「單一機櫃」與「合併單卡（整個機房多機櫃並排）」共用：
// 傳入 diagrams 陣列即可，多櫃會像畫面一樣並排、底部（U1）對齊（矮櫃頂端補空白）。
// 方塊一律用直角（rounded=0 / 無 rx），與畫面示意圖一致。
import { rackTypeColor as colorFor } from "@/utils/rackColors";

export type RackNameAlign = "left" | "center" | "right";

interface DiagramLike {
  name: string;
  u_height: number;
  devices: any[];
}

const GEO = { rowH: 24, colW: 260, gutter: 32, pad: 12, headerH: 30 };
const COL_GAP = 40; // 機櫃之間的水平間距

function esc(s: string): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// 半 U（左/右）各佔半寬；rackLeft = 此機櫃外框左緣 x
function partGeom(dev: any, rackLeft: number): { x: number; w: number; cx: number; half: boolean } {
  const { colW } = GEO;
  const side = dev.rack_side ?? "full";
  if (side === "left")
    return { x: rackLeft + 2, w: colW / 2 - 3, cx: rackLeft + colW / 4, half: true };
  if (side === "right")
    return { x: rackLeft + colW / 2 + 1, w: colW / 2 - 3, cx: rackLeft + (colW * 3) / 4, half: true };
  return { x: rackLeft + 2, w: colW - 4, cx: rackLeft + colW / 2, half: false };
}

function labelOf(dev: any): string {
  return dev.name ?? "";
}

// 計算每個機櫃的版面位置（並排 + 底部對齊）
function layout(diagrams: DiagramLike[], alignToU: number) {
  const { rowH, colW, gutter, pad, headerH } = GEO;
  const maxU = Math.max(alignToU, ...diagrams.map((d) => d.u_height || 0), 1);
  const blocks = diagrams.map((d, i) => {
    const blockX = pad + i * (gutter + colW + COL_GAP);
    const rackLeft = blockX + gutter;
    const u = d.u_height || 0;
    const top = headerH + pad + (maxU - u) * rowH; // 矮櫃往下推，使 U1 底部對齊
    return { d, rackLeft, top, u };
  });
  const W = pad * 2 + diagrams.length * (gutter + colW) + Math.max(0, diagrams.length - 1) * COL_GAP;
  const H = headerH + pad * 2 + maxU * rowH;
  return { blocks, W, H, maxU };
}

export function buildRacksSvg(
  diagrams: DiagramLike[], alignToU: number, nameAlign: RackNameAlign,
): { svg: string; W: number; H: number } | null {
  if (!diagrams.length) return null;
  const { rowH, colW, pad } = GEO;
  const { blocks, W, H } = layout(diagrams, alignToU);
  const p: string[] = [];
  p.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" font-family="sans-serif">`);
  p.push(`<rect x="0" y="0" width="${W}" height="${H}" fill="#ffffff"/>`);
  for (const { d, rackLeft, top, u } of blocks) {
    p.push(`<text x="${rackLeft}" y="${pad + 16}" font-size="14" font-weight="bold">${esc(d.name)} (${u}U)</text>`);
    p.push(`<rect x="${rackLeft}" y="${top}" width="${colW}" height="${u * rowH}" fill="#f5f5f5" stroke="#888" stroke-width="1.5"/>`);
    for (let i = 0; i < u; i++) {
      const uNum = u - i;
      const y = top + i * rowH;
      p.push(`<text x="${rackLeft - 4}" y="${y + rowH / 2 + 4}" font-size="10" text-anchor="end" fill="#666">${uNum}</text>`);
      p.push(`<line x1="${rackLeft}" y1="${y}" x2="${rackLeft + colW}" y2="${y}" stroke="#dddddd" stroke-width="0.5"/>`);
    }
    for (const dev of (d.devices || [])) {
      if (!dev.u_position || !dev.u_size) continue;
      const uTop = dev.u_position + dev.u_size - 1;
      const yTop = top + (u - uTop) * rowH;
      const hgt = dev.u_size * rowH;
      const g = partGeom(dev, rackLeft);
      // 直角方塊（無 rx），與畫面一致
      p.push(`<rect x="${g.x}" y="${yTop + 1}" width="${g.w}" height="${hgt - 2}" fill="${colorFor(dev.type)}" stroke="rgba(0,0,0,0.3)"/>`);
      const a = nameAlign;
      const tx = g.half ? g.cx : a === "center" ? rackLeft + colW / 2 : a === "right" ? rackLeft + colW - 10 : rackLeft + 10;
      const anchor = g.half ? "middle" : a === "center" ? "middle" : a === "right" ? "end" : "start";
      p.push(`<text x="${tx}" y="${yTop + hgt / 2 + 4}" text-anchor="${anchor}" font-size="11" font-weight="bold" fill="#ffffff">${esc(labelOf(dev))}</text>`);
      if (dev.rack_face === "rear") {
        const rx = g.x + g.w;
        p.push(`<path d="M${rx - 14} ${yTop + 1} L${rx} ${yTop + 1} L${rx} ${yTop + 15} Z" fill="rgba(0,0,0,0.55)"/>`);
        p.push(`<text x="${rx - 2}" y="${yTop + 11}" text-anchor="end" font-size="9" font-weight="bold" fill="#ffffff">R</text>`);
      }
    }
  }
  p.push(`</svg>`);
  return { svg: p.join("\n"), W, H };
}

export function buildRacksDrawio(
  diagrams: DiagramLike[], alignToU: number, nameAlign: RackNameAlign, title: string,
): string | null {
  if (!diagrams.length) return null;
  const { rowH, colW, pad } = GEO;
  const { blocks } = layout(diagrams, alignToU);
  const cells: string[] = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>'];
  let n = 0;
  for (const { d, rackLeft, top, u } of blocks) {
    cells.push(`<mxCell id="t${n++}" value="${esc(`${d.name} (${u}U)`)}" style="text;html=1;align=left;verticalAlign=middle;fontStyle=1;fontSize=14;" vertex="1" parent="1"><mxGeometry x="${rackLeft}" y="${pad}" width="${colW}" height="20" as="geometry"/></mxCell>`);
    cells.push(`<mxCell id="r${n++}" value="" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#888888;strokeWidth=2;" vertex="1" parent="1"><mxGeometry x="${rackLeft}" y="${top}" width="${colW}" height="${u * rowH}" as="geometry"/></mxCell>`);
    for (let i = 0; i < u; i++) {
      const uNum = u - i;
      const y = top + i * rowH;
      cells.push(`<mxCell id="u${n++}" value="${uNum}" style="text;html=1;align=right;verticalAlign=middle;fontSize=10;fontColor=#666666;" vertex="1" parent="1"><mxGeometry x="${rackLeft - 28}" y="${y}" width="24" height="${rowH}" as="geometry"/></mxCell>`);
    }
    for (const dev of (d.devices || [])) {
      if (!dev.u_position || !dev.u_size) continue;
      const uTop = dev.u_position + dev.u_size - 1;
      const yTop = top + (u - uTop) * rowH;
      const hgt = dev.u_size * rowH;
      const g = partGeom(dev, rackLeft);
      const align = g.half ? "center" : nameAlign;
      // rounded=0 → 直角方塊，與畫面示意圖一致
      cells.push(`<mxCell id="dev${n++}" value="${esc(labelOf(dev))}" style="rounded=0;whiteSpace=wrap;html=1;fillColor=${colorFor(dev.type)};strokeColor=#000000;fontColor=#ffffff;fontStyle=1;align=${align};spacingLeft=6;spacingRight=6;" vertex="1" parent="1"><mxGeometry x="${g.x}" y="${yTop + 1}" width="${g.w}" height="${hgt - 2}" as="geometry"/></mxCell>`);
    }
  }
  return (
    `<mxfile host="jt-ipam"><diagram name="${esc(title)}">` +
    `<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" math="0" shadow="0">` +
    `<root>${cells.join("")}</root></mxGraphModel></diagram></mxfile>`
  );
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

export function exportRacksSvg(diagrams: DiagramLike[], alignToU: number, nameAlign: RackNameAlign, filename: string): void {
  const r = buildRacksSvg(diagrams, alignToU, nameAlign);
  if (!r) return;
  downloadBlob(new Blob([r.svg], { type: "image/svg+xml" }), `${filename}.svg`);
}

export function exportRacksPng(diagrams: DiagramLike[], alignToU: number, nameAlign: RackNameAlign, filename: string): void {
  const r = buildRacksSvg(diagrams, alignToU, nameAlign);
  if (!r) return;
  const scale = 2;
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = r.W * scale; canvas.height = r.H * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    canvas.toBlob((blob) => { if (blob) downloadBlob(blob, `${filename}.png`); }, "image/png");
  };
  img.src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(r.svg)));
}

export function exportRacksDrawio(diagrams: DiagramLike[], alignToU: number, nameAlign: RackNameAlign, filename: string): void {
  const xml = buildRacksDrawio(diagrams, alignToU, nameAlign, filename);
  if (!xml) return;
  downloadBlob(new Blob([xml], { type: "application/xml" }), `${filename}.drawio`);
}
