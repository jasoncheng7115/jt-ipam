<script setup lang="ts">
/**
 * 圖的縮圖總覽（左下角）：整張圖的縮影＋目前看到的範圍，點或拖曳就移過去。
 *
 * 原本只在 IP 變更評估的關係圖（ImpactRelationGraph）裡有；使用者 2026-11-07：「IP 拓樸圖也要，每一種模式都要」。
 * 抽成共用元件，兩張圖同一個做法。呼叫端在建好 cytoscape 之後 attach(cy)；節點搬動、隱藏、加減、排版跑完、
 * 平移縮放都會自己重畫（同一個畫格只畫一次）。
 */
import { onBeforeUnmount, reactive, ref } from "vue";
import type cytoscape from "cytoscape";

const props = withDefaults(defineProps<{
  /** 要畫哪些節點（外框類的群組節點不畫） */
  selector?: string;
  colorOf: (n: cytoscape.NodeSingular) => string;
  /** 圓角：回 null 就畫成圓形 */
  roundOf?: (n: cytoscape.NodeSingular) => number | null;
  testid?: string;
}>(), { selector: "node", roundOf: undefined, testid: "graph-minimap" });

const MINI_W = 180, MINI_H = 120, MINI_PAD = 8, MAX_SCALE = 0.25;
const mini = reactive({ nodes: [] as { id: string; x: number; y: number; w: number; h: number; round: number;
                                        color: string }[],
                        view: {} as Record<string, string> });
const dark = ref(false);
let cy: cytoscape.Core | null = null;
let scale = 1, ox = 0, oy = 0, bbox = { x1: 0, y1: 0, w: 1, h: 1 };
const isDark = () => document.documentElement.dataset.theme === "dark";

function visible(n: cytoscape.NodeSingular): boolean {
  return n.style("display") !== "none" && n.visible();
}

function draw(): void {
  if (!cy || cy.destroyed()) return;
  dark.value = isDark();
  const nodes = cy.nodes(props.selector).filter((n) => visible(n as cytoscape.NodeSingular));
  if (nodes.empty()) { mini.nodes = []; mini.view = {}; return; }
  const bb = nodes.boundingBox({ includeLabels: false });
  bbox = { x1: bb.x1, y1: bb.y1, w: Math.max(bb.w, 1), h: Math.max(bb.h, 1) };
  // 上限 25%：節點很少時不放大到塞滿整個縮圖（只有一台時會變成一大塊色塊）
  scale = Math.min((MINI_W - MINI_PAD * 2) / bbox.w, (MINI_H - MINI_PAD * 2) / bbox.h, MAX_SCALE);
  ox = (MINI_W - bbox.w * scale) / 2;
  oy = (MINI_H - bbox.h * scale) / 2;
  mini.nodes = nodes.map((el) => {
    const n = el as cytoscape.NodeSingular;
    const b = n.boundingBox({ includeLabels: false });
    const w = Math.max(b.w * scale, 3), h = Math.max(b.h * scale, 3);
    const r = props.roundOf ? props.roundOf(n) : null;
    return { id: n.id(), x: ox + (b.x1 - bbox.x1) * scale, y: oy + (b.y1 - bbox.y1) * scale, w, h,
             round: r ?? Math.min(w, h) / 2, color: props.colorOf(n) };
  });
  updateView();
}

function updateView(): void {
  if (!cy || cy.destroyed() || !mini.nodes.length) return;
  const ext = cy.extent();
  const left = ox + (ext.x1 - bbox.x1) * scale, top = oy + (ext.y1 - bbox.y1) * scale;
  const l = Math.max(0, left), tp = Math.max(0, top);
  const r = Math.min(MINI_W, left + ext.w * scale), b = Math.min(MINI_H, top + ext.h * scale);
  mini.view = { left: `${l}px`, top: `${tp}px`, width: `${Math.max(r - l, 6)}px`, height: `${Math.max(b - tp, 6)}px` };
}

let drawQueued = false, viewQueued = false;
function scheduleDraw(): void {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => { drawQueued = false; draw(); });
}
function scheduleView(): void {
  if (viewQueued || drawQueued) return;
  viewQueued = true;
  requestAnimationFrame(() => { viewQueued = false; updateView(); });
}

/** 綁到一張圖（重建圖之後再叫一次） */
function attach(next: cytoscape.Core): void {
  cy = next;
  cy.on("viewport", scheduleView);
  cy.on("add remove position style layoutstop", scheduleDraw);
  scheduleDraw();
}

let dragging = false;
function pan(ev: PointerEvent): void {
  if (!cy || cy.destroyed()) return;
  const rect = (ev.currentTarget as HTMLElement).getBoundingClientRect();
  const mx = (ev.clientX - rect.left - ox) / scale + bbox.x1;
  const my = (ev.clientY - rect.top - oy) / scale + bbox.y1;
  const z = cy.zoom();
  cy.pan({ x: cy.width() / 2 - mx * z, y: cy.height() / 2 - my * z });
}
function down(ev: PointerEvent): void {
  dragging = true;
  (ev.currentTarget as HTMLElement).setPointerCapture?.(ev.pointerId);
  pan(ev);
}
function move(ev: PointerEvent): void { if (dragging) pan(ev); }
function up(): void { dragging = false; }

onBeforeUnmount(() => { cy = null; });
defineExpose({ attach, redraw: draw });
</script>

<template>
  <div v-show="mini.nodes.length" class="gm" :class="{ 'gm--dark': dark }" :data-testid="testid"
       @pointerdown="down" @pointermove="move" @pointerup="up" @pointerleave="up">
    <svg :width="MINI_W" :height="MINI_H">
      <rect v-for="m in mini.nodes" :key="m.id" :x="m.x" :y="m.y" :width="m.w" :height="m.h"
            :rx="m.round" :fill="m.color" />
    </svg>
    <div class="gm-view" data-testid="graph-minimap-view" :style="mini.view" />
  </div>
</template>

<style scoped>
/* 左下角：右下角是 AI 對話的浮動按鈕 */
.gm { position: absolute; left: 12px; bottom: 12px; width: 180px; height: 120px; border-radius: 8px; z-index: 5;
  background: rgba(255, 255, 255, .85); border: 1px solid rgba(102, 126, 234, .3);
  box-shadow: 0 2px 8px rgba(0, 0, 0, .1); overflow: hidden; cursor: pointer; touch-action: none; }
.gm svg { display: block; pointer-events: none; }
.gm-view { position: absolute; box-sizing: border-box; border: 2px solid #667eea; background: rgba(102, 126, 234, .15);
  border-radius: 2px; pointer-events: none; }
.gm--dark { background: rgba(17, 24, 39, .85); border-color: rgba(129, 140, 248, .35); }
</style>
