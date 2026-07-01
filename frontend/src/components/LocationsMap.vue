<script setup lang="ts">
/**
 * 機房 / 地點世界地圖 — 依全域「地圖供應商」設定切換兩種模式：
 *
 *  • builtin（預設）/ google → 內建 Natural Earth 陸地輪廓（public domain，已預先投影成 SVG path），
 *    等距圓柱投影標記。**完全內建、零外部請求**（隔離網路可用、不洩漏、CSP/COEP 最嚴格）。
 *    （Google 圖磚因服務條款不可代理；頁內預覽用內建圖，外部「開啟」連結才走 Google Maps。）
 *  • osm → OpenStreetMap 圖磚 slippy 視圖，但圖磚走**本機後端代理** `/api/v1/system/map-tile/{z}/{x}/{y}`，
 *    瀏覽器不直連外部 → 維持 CSP `img-src 'self'` + COEP require-corp，ZAP 乾淨。
 */
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { WORLD_LAND_PATH } from "@/assets/world-land";

interface Pt { id: string; name: string; lat: number; lng: number; }
const props = withDefaults(defineProps<{ points: Pt[]; provider?: "builtin" | "osm" | "google" }>(), {
  provider: "builtin",
});
const emit = defineEmits<{ (e: "select", id: string): void }>();
const { t } = useI18n();

const isSlippy = computed(() => props.provider === "osm");
const boxRef = ref<HTMLDivElement | null>(null);
const boxW = ref(800);
const boxH = 340;

const valid = computed(() => props.points.filter((p) =>
  Number.isFinite(p.lat) && Number.isFinite(p.lng) && (p.lat !== 0 || p.lng !== 0)));

// ─────────── 內建 SVG 模式：等距圓柱投影（x=lng+180, y=90-lat），viewBox 0..360 / 0..180 ───────────
const ex = (lng: number) => lng + 180;
const ey = (lat: number) => 90 - lat;
const svgView = computed(() => {
  const pts = valid.value, W = boxW.value, H = boxH;
  if (!pts.length) return null;
  const xs = pts.map((p) => ex(p.lng)), ys = pts.map((p) => ey(p.lat));
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  // 視野 = 所有點的範圍 + 邊距，盡量放大到剛好框住所有點（不再硬撐成 24°×16° 害得近距離的點擠成一團）。
  // 最小視野只用來避免單點 / 極近點過度放大（內建低解析底圖會糊）。
  let spanX = Math.max(Math.max(...xs) - Math.min(...xs), 1.2) * 1.3;
  let spanY = Math.max(Math.max(...ys) - Math.min(...ys), 0.8) * 1.3;
  const aspect = W / H;
  if (spanX / spanY < aspect) spanX = spanY * aspect; else spanY = spanX / aspect;
  const vw = Math.min(spanX, 360), vh = Math.min(spanY, 180);
  const vx0 = Math.min(Math.max(cx - vw / 2, 0), 360 - vw);
  const vy0 = Math.min(Math.max(cy - vh / 2, 0), 180 - vh);
  return { vx0, vy0, vw, vh, W, H };
});
const svgViewBox = computed(() => {
  const v = svgView.value;
  return v ? `${v.vx0} ${v.vy0} ${v.vw} ${v.vh}` : "0 0 360 180";
});
const svgMarkers = computed(() => {
  const v = svgView.value;
  if (!v) return [];
  return valid.value.map((p) => ({
    id: p.id, name: p.name,
    left: (ex(p.lng) - v.vx0) / v.vw * v.W, top: (ey(p.lat) - v.vy0) / v.vh * v.H,
  }));
});

// ─────────── OSM slippy 模式：Web Mercator + 本機圖磚代理 ───────────
const TILE = 256;
const lngToWorldX = (lng: number, z: number) => (lng + 180) / 360 * TILE * 2 ** z;
function latToWorldY(lat: number, z: number): number {
  const s = Math.sin(lat * Math.PI / 180);
  return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * TILE * 2 ** z;
}
const slippyView = computed(() => {
  const pts = valid.value, W = boxW.value, H = boxH, pad = 48;
  if (!pts.length) return null;
  const lats = pts.map((p) => p.lat), lngs = pts.map((p) => p.lng);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  let z = 13;
  if (pts.length > 1) {
    for (z = 18; z >= 2; z--) {
      const dx = Math.abs(lngToWorldX(maxLng, z) - lngToWorldX(minLng, z));
      const dy = Math.abs(latToWorldY(minLat, z) - latToWorldY(maxLat, z));
      if (dx <= W - 2 * pad && dy <= H - 2 * pad) break;
    }
  }
  const cx = (lngToWorldX(minLng, z) + lngToWorldX(maxLng, z)) / 2;
  const cy = (latToWorldY(minLat, z) + latToWorldY(maxLat, z)) / 2;
  return { z, vx0: cx - W / 2, vy0: cy - H / 2, W, H };
});
const tiles = computed(() => {
  const v = slippyView.value;
  if (!v) return [];
  const n = 2 ** v.z;
  const out: { key: string; src: string; left: number; top: number }[] = [];
  const tx0 = Math.floor(v.vx0 / TILE), tx1 = Math.floor((v.vx0 + v.W) / TILE);
  const ty0 = Math.floor(v.vy0 / TILE), ty1 = Math.floor((v.vy0 + v.H) / TILE);
  for (let tx = tx0; tx <= tx1; tx++) {
    for (let ty = ty0; ty <= ty1; ty++) {
      if (ty < 0 || ty >= n) continue;
      const wx = ((tx % n) + n) % n;
      out.push({
        key: `${tx}_${ty}`,
        src: `/api/v1/system/map-tile/${v.z}/${wx}/${ty}`,   // ← 本機代理
        left: tx * TILE - v.vx0, top: ty * TILE - v.vy0,
      });
    }
  }
  return out;
});
const slippyMarkers = computed(() => {
  const v = slippyView.value;
  if (!v) return [];
  return valid.value.map((p) => ({
    id: p.id, name: p.name,
    left: lngToWorldX(p.lng, v.z) - v.vx0, top: latToWorldY(p.lat, v.z) - v.vy0,
  }));
});

let ro: ResizeObserver | null = null;
onMounted(() => {
  if (boxRef.value) {
    boxW.value = boxRef.value.clientWidth || 800;
    ro = new ResizeObserver(() => { if (boxRef.value) boxW.value = boxRef.value.clientWidth || 800; });
    ro.observe(boxRef.value);
  }
});
onBeforeUnmount(() => { ro?.disconnect(); });
</script>

<template>
  <div v-if="valid.length" ref="boxRef" class="lmap" :style="{ height: boxH + 'px' }">
    <!-- OSM slippy（本機代理圖磚）-->
    <template v-if="isSlippy">
      <img v-for="ti in tiles" :key="ti.key" :src="ti.src" class="lmap-tile"
           :style="{ left: ti.left + 'px', top: ti.top + 'px' }" alt="" draggable="false" />
      <div v-for="m in slippyMarkers" :key="m.id" class="lmap-pin"
           :style="{ left: m.left + 'px', top: m.top + 'px' }" :title="m.name" @click="emit('select', m.id)">
        <span class="lmap-dot"></span><span class="lmap-name">{{ m.name }}</span>
      </div>
      <div class="lmap-attr">© OpenStreetMap</div>
    </template>
    <!-- 內建 SVG（builtin / google）-->
    <template v-else>
      <svg class="lmap-svg" :viewBox="svgViewBox" preserveAspectRatio="none" :width="boxW" :height="boxH">
        <path :d="WORLD_LAND_PATH" class="lmap-land" />
      </svg>
      <div v-for="m in svgMarkers" :key="m.id" class="lmap-pin"
           :style="{ left: m.left + 'px', top: m.top + 'px' }" :title="m.name" @click="emit('select', m.id)">
        <span class="lmap-dot"></span><span class="lmap-name">{{ m.name }}</span>
      </div>
      <div class="lmap-attr">Natural Earth</div>
    </template>
    <div class="lmap-hint">{{ t("locations.map_all_hint") }}</div>
  </div>
</template>

<style scoped>
.lmap {
  position: relative; width: 100%; overflow: hidden;
  border: 1px solid var(--n-border-color, #ddd); border-radius: 8px; background: #adcee8;
}
.lmap-svg { position: absolute; left: 0; top: 0; display: block; }
.lmap-land { fill: #e6e3d7; stroke: #b9b29a; stroke-width: 0.15; vector-effect: non-scaling-stroke; }
.lmap-tile { position: absolute; width: 256px; height: 256px; user-select: none; pointer-events: none; }
html[data-theme="dark"] .lmap { background: #0b1a2b; }
html[data-theme="dark"] .lmap-land { fill: #243447; stroke: #3a4d63; }
html[data-theme="dark"] .lmap-tile { filter: invert(1) hue-rotate(180deg) brightness(.92) contrast(.9) saturate(.82); }
html[data-theme="dark"] .lmap-attr { background: rgba(15,24,37,.7); color: #aab8cc; }
html[data-theme="dark"] .lmap-hint { background: rgba(15,24,37,.75); color: #cdd8e6; }
.lmap-pin {
  position: absolute; transform: translate(-50%, -100%);
  display: flex; flex-direction: column; align-items: center; cursor: pointer; z-index: 5;
}
.lmap-dot {
  width: 14px; height: 14px; border-radius: 50% 50% 50% 0;
  background: #e74c3c; border: 2px solid #fff; transform: rotate(-45deg); box-shadow: 0 1px 3px rgba(0,0,0,0.5);
}
.lmap-name {
  margin-top: 2px; font-size: 11px; font-weight: 600; color: #1f2937;
  background: rgba(255,255,255,0.85); padding: 0 4px; border-radius: 3px;
  white-space: nowrap; max-width: 160px; overflow: hidden; text-overflow: ellipsis;
}
.lmap-pin:hover .lmap-dot { background: #18a058; }
.lmap-attr {
  position: absolute; right: 4px; bottom: 2px; z-index: 6;
  font-size: 10px; color: #555; background: rgba(255,255,255,0.7); padding: 0 4px; border-radius: 3px;
}
.lmap-hint {
  position: absolute; left: 6px; top: 6px; z-index: 6;
  font-size: 11px; color: #444; background: rgba(255,255,255,0.75); padding: 1px 6px; border-radius: 4px;
}
</style>
