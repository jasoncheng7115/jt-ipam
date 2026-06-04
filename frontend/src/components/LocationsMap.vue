<script setup lang="ts">
/**
 * 機房 / 地點世界地圖 — 零相依的 OSM 圖磚 slippy 視圖。
 *
 * 直接以 Web Mercator 投影鋪 OpenStreetMap 圖磚（<img>），把所有有經緯度的地點
 * 標成標記，並自動選 zoom + 置中，讓所有點剛好落在視窗內。
 * 不引入 Leaflet 等相依；圖磚網域已在 CSP img-src 放行。
 * （地圖引擎目前固定用 OSM＝管理員預設；Google 需另接 SDK，先不混進總覽圖。）
 */
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";

interface Pt { id: string; name: string; lat: number; lng: number; }
const props = defineProps<{ points: Pt[] }>();
const emit = defineEmits<{ (e: "select", id: string): void }>();
const { t } = useI18n();

const TILE = 256;
const boxRef = ref<HTMLDivElement | null>(null);
const boxW = ref(800);
const boxH = 340;

function lngToWorldX(lng: number, z: number): number { return (lng + 180) / 360 * TILE * 2 ** z; }
function latToWorldY(lat: number, z: number): number {
  const s = Math.sin(lat * Math.PI / 180);
  return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * TILE * 2 ** z;
}

const valid = computed(() => props.points.filter((p) =>
  Number.isFinite(p.lat) && Number.isFinite(p.lng) && (p.lat !== 0 || p.lng !== 0)));

// 依所有點選出剛好塞得下的 zoom + 中心
const view = computed(() => {
  const pts = valid.value;
  const W = boxW.value, H = boxH, pad = 48;
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
  const v = view.value;
  if (!v) return [];
  const n = 2 ** v.z;
  const out: { key: string; src: string; left: number; top: number }[] = [];
  const tx0 = Math.floor(v.vx0 / TILE), tx1 = Math.floor((v.vx0 + v.W) / TILE);
  const ty0 = Math.floor(v.vy0 / TILE), ty1 = Math.floor((v.vy0 + v.H) / TILE);
  const subs = ["a", "b", "c"];
  let i = 0;
  for (let tx = tx0; tx <= tx1; tx++) {
    for (let ty = ty0; ty <= ty1; ty++) {
      if (ty < 0 || ty >= n) continue;
      const wx = ((tx % n) + n) % n;
      const s = subs[(i++) % 3];
      out.push({
        key: `${tx}_${ty}`,
        src: `https://${s}.tile.openstreetmap.org/${v.z}/${wx}/${ty}.png`,
        left: tx * TILE - v.vx0,
        top: ty * TILE - v.vy0,
      });
    }
  }
  return out;
});

const markers = computed(() => {
  const v = view.value;
  if (!v) return [];
  return valid.value.map((p) => ({
    id: p.id, name: p.name,
    left: lngToWorldX(p.lng, v.z) - v.vx0,
    top: latToWorldY(p.lat, v.z) - v.vy0,
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
    <img v-for="ti in tiles" :key="ti.key" :src="ti.src" class="lmap-tile"
         :style="{ left: ti.left + 'px', top: ti.top + 'px' }" alt="" draggable="false" />
    <div
      v-for="m in markers" :key="m.id" class="lmap-pin"
      :style="{ left: m.left + 'px', top: m.top + 'px' }"
      :title="m.name" @click="emit('select', m.id)"
    >
      <span class="lmap-dot"></span>
      <span class="lmap-name">{{ m.name }}</span>
    </div>
    <div class="lmap-attr">© OpenStreetMap</div>
    <div class="lmap-hint">{{ t("locations.map_all_hint") }}</div>
  </div>
</template>

<style scoped>
.lmap {
  position: relative;
  width: 100%;
  overflow: hidden;
  border: 1px solid var(--n-border-color, #ddd);
  border-radius: 8px;
  background: #e8eef2;
}
.lmap-tile { position: absolute; width: 256px; height: 256px; user-select: none; pointer-events: none; }
/* 深色主題：把 OSM 圖磚反相 + 轉色做成深色地圖（標記 / 文字是另外的 DOM，不受影響）*/
html[data-theme="dark"] .lmap { background: #0b1220; }
html[data-theme="dark"] .lmap-tile { filter: invert(1) hue-rotate(180deg) brightness(.92) contrast(.9) saturate(.82); }
html[data-theme="dark"] .lmap-attr { background: rgba(15,24,37,.7); color: #aab8cc; }
html[data-theme="dark"] .lmap-hint { background: rgba(15,24,37,.75); color: #cdd8e6; }
.lmap-pin {
  position: absolute;
  transform: translate(-50%, -100%);
  display: flex; flex-direction: column; align-items: center;
  cursor: pointer; z-index: 5;
}
.lmap-dot {
  width: 14px; height: 14px; border-radius: 50% 50% 50% 0;
  background: #e74c3c; border: 2px solid #fff; transform: rotate(-45deg);
  box-shadow: 0 1px 3px rgba(0,0,0,0.5);
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
