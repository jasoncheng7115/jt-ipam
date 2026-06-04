<script setup lang="ts">
/**
 * 機房平面圖：上傳底圖 + 把機櫃拖到圖上定位 + 檢視點機櫃。
 *
 * - 座標以 0..1 比例存（與底圖解析度無關）。
 * - 檢視模式：點機櫃標記 → emit("select", rackId)，父層載入該機櫃 U 位。
 * - 編輯模式（admin）：拖拉標記移動；未擺放的機櫃在下方托盤，點一下放到圖中央再拖。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { NButton, NIcon, NSpace, NEmpty, NPopconfirm, NSlider, NInputNumber, useMessage } from "naive-ui";
import { RacksIcon, EditIcon, SaveIcon, DeleteIcon, PlusIcon, CancelIcon } from "@/icons";
import {
  listRacksByLocation, getFloorplanObjectURL, uploadFloorplan,
  deleteFloorplan, setRackPositions, type Rack,
} from "@/api/racks";

const props = defineProps<{ locationId: string; canEdit: boolean }>();
const emit = defineEmits<{ (e: "select", rackId: string): void }>();

const { t } = useI18n();
const msg = useMessage();

const racks = ref<Rack[]>([]);
const imgUrl = ref<string | null>(null);
const hasPlan = ref(false);
const loading = ref(false);
const editMode = ref(false);
const dirty = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const planEl = ref<HTMLDivElement | null>(null);   // 外框（overflow hidden）
const innerEl = ref<HTMLDivElement | null>(null);   // 受 transform 的內層（img + 標記）

// 縮放 / 平移：預設 fit（整張看完整）；用下方 bar 控制，不靠滾輪
const zoom = ref(1);
const pan = ref({ x: 0, y: 0 });
const imgEl = ref<HTMLImageElement | null>(null);
const zoomPct = computed({
  get: () => Math.round(zoom.value * 100),
  set: (p: number) => { zoom.value = Math.min(4, Math.max(0.1, (p || 100) / 100)); },
});
// 控制點反向縮放 → 不論平面圖縮放/方框大小，操作點維持固定螢幕大小（好點選）
// 標籤 / 控制鈕：反向縮放 + 反向旋轉 → 不論機櫃方框旋轉幾度，文字與按鈕都維持正向可讀
function markStyle(r: Rack) {
  return { transform: `scale(${1 / zoom.value}) rotate(${-(r.pos_rot || 0)}deg)` };
}
// 算出「整張剛好塞進可視框」的縮放並置中
function fitToView() {
  const box = planEl.value;
  const img = imgEl.value;
  if (!box || !img || !img.naturalWidth) return;
  const cw = box.clientWidth, ch = box.clientHeight;
  const renderedH = cw * (img.naturalHeight / img.naturalWidth); // zoom=1 時 inner 寬=cw
  const fit = Math.min(1, ch / renderedH);
  zoom.value = fit;
  pan.value = { x: (cw - cw * fit) / 2, y: (ch - renderedH * fit) / 2 };
}
function resetView() { fitToView(); }
let panning = false;
let panStart = { x: 0, y: 0, px: 0, py: 0 };
function onPanStart(ev: PointerEvent) {
  // 標記自己會 stopPropagation；落到這裡代表點在空白處 → 平移
  panning = true;
  panStart = { x: ev.clientX, y: ev.clientY, px: pan.value.x, py: pan.value.y };
  window.addEventListener("pointermove", onPanMove);
  window.addEventListener("pointerup", onPanEnd);
}
function onPanMove(ev: PointerEvent) {
  if (!panning) return;
  pan.value = { x: panStart.px + (ev.clientX - panStart.x), y: panStart.py + (ev.clientY - panStart.y) };
}
function onPanEnd() {
  panning = false;
  window.removeEventListener("pointermove", onPanMove);
  window.removeEventListener("pointerup", onPanEnd);
}

function revoke() {
  if (imgUrl.value) { URL.revokeObjectURL(imgUrl.value); imgUrl.value = null; }
}

async function load() {
  loading.value = true;
  editMode.value = false;
  dirty.value = false;
  resetView();
  try {
    racks.value = await listRacksByLocation(props.locationId);
    revoke();
    try {
      imgUrl.value = await getFloorplanObjectURL(props.locationId);
      hasPlan.value = true;
    } catch {
      hasPlan.value = false;
    }
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

watch(() => props.locationId, () => { void load(); });
onMounted(() => { void load(); });
onBeforeUnmount(() => {
  revoke();
  window.removeEventListener("pointermove", onMove);
  window.removeEventListener("pointerup", onUp);
  window.removeEventListener("pointermove", onPanMove);
  window.removeEventListener("pointerup", onPanEnd);
  window.removeEventListener("pointermove", onRotateMove);
  window.removeEventListener("pointerup", onRotateUp);
  window.removeEventListener("pointermove", onResizeMove);
  window.removeEventListener("pointerup", onResizeUp);
});

const placed = () => racks.value.filter((r) => r.pos_x != null && r.pos_y != null);
const unplaced = () => racks.value.filter((r) => r.pos_x == null || r.pos_y == null);

function pickFile() { fileInput.value?.click(); }
async function onFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (!f) return;
  try {
    await uploadFloorplan(props.locationId, f);
    msg.success(t("common.ok"));
    await load();
    editMode.value = true;
  } catch (err: any) {
    msg.error(err?.response?.data?.detail ?? t("errors.server"));
  } finally {
    if (fileInput.value) fileInput.value.value = "";
  }
}

async function removePlan() {
  try {
    await deleteFloorplan(props.locationId);
    await load();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 拖拉定位 ──
let dragId: string | null = null;
function fracFromEvent(ev: PointerEvent): { x: number; y: number } | null {
  const el = innerEl.value;   // 用內層（已套 transform）的螢幕矩形 → 縮放/平移後仍正確
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  const x = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
  const y = Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height));
  return { x, y };
}
function onMarkerDown(r: Rack, ev: PointerEvent) {
  if (!editMode.value) { emit("select", r.id); return; }
  ev.preventDefault();
  dragId = r.id;
  (ev.target as HTMLElement).setPointerCapture?.(ev.pointerId);
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}
const SNAP = 0.018;   // 拖動時與其它機櫃同軸對齊的容差（比例）
function snapAxis(val: number, others: (number | null | undefined)[]): number {
  let best = val, bestDist = SNAP;
  for (const o of others) {
    if (o == null) continue;
    const dd = Math.abs(o - val);
    if (dd < bestDist) { best = o; bestDist = dd; }
  }
  return best;
}
function onMove(ev: PointerEvent) {
  if (!dragId) return;
  const f = fracFromEvent(ev);
  if (!f) return;
  const r = racks.value.find((x) => x.id === dragId);
  if (!r) return;
  // 自動貼齊其它機櫃（同 x 或同 y 軸對齊），不用對到完全一樣的像素
  const others = racks.value.filter((x) => x.id !== dragId && x.pos_x != null && x.pos_y != null);
  const sx = snapAxis(f.x, others.map((o) => o.pos_x as number));
  const sy = snapAxis(f.y, others.map((o) => o.pos_y as number));
  r.pos_x = +sx.toFixed(5); r.pos_y = +sy.toFixed(5); dirty.value = true;
}
function onUp() {
  dragId = null;
  window.removeEventListener("pointermove", onMove);
  window.removeEventListener("pointerup", onUp);
}
function placeFromTray(r: Rack) {
  r.pos_x = 0.5; r.pos_y = 0.5; r.pos_rot = r.pos_rot ?? 0; dirty.value = true;
}
function unplace(r: Rack) {
  r.pos_x = null; r.pos_y = null; dirty.value = true;
}
// 拖曳旋轉把手 → 任意角度；輕點(幾乎沒移動)則 +90 快速轉
let rotId: string | null = null;
let rotCenter = { x: 0, y: 0 };
let rotMoved = false;
function onRotateDown(r: Rack, ev: PointerEvent) {
  if (!editMode.value) return;
  ev.preventDefault();
  const box = (ev.currentTarget as HTMLElement).parentElement?.getBoundingClientRect();
  if (!box) return;
  rotCenter = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
  rotId = r.id; rotMoved = false;
  window.addEventListener("pointermove", onRotateMove);
  window.addEventListener("pointerup", onRotateUp);
}
function onRotateMove(ev: PointerEvent) {
  if (!rotId) return;
  const r = racks.value.find((x) => x.id === rotId);
  if (!r) return;
  rotMoved = true;
  let deg = (Math.atan2(ev.clientY - rotCenter.y, ev.clientX - rotCenter.x) * 180) / Math.PI + 90;
  deg = ((deg % 360) + 360) % 360;
  // 任意角度；但接近正交(±6°)時軟吸附到 0/90/180/270，方便對齊牆面。
  const nearest = ((Math.round(deg / 90) * 90) % 360 + 360) % 360;
  r.pos_rot = Math.abs(deg - nearest) <= 6 ? nearest : Math.round(deg);
  dirty.value = true;
}
function onRotateUp() {
  if (rotId && !rotMoved) {   // 沒拖動 = 點一下 → +90
    const r = racks.value.find((x) => x.id === rotId);
    if (r) { r.pos_rot = ((r.pos_rot ?? 0) + 90) % 360; dirty.value = true; }
  }
  rotId = null;
  window.removeEventListener("pointermove", onRotateMove);
  window.removeEventListener("pointerup", onRotateUp);
}
// 機櫃方框大小：有存 pos_w/pos_h(比例) 就用比例，否則用預設（寬固定、高隨 U）。
function boxHeight(r: Rack): number {
  const u = r.u_height || 42;
  return Math.round(34 + Math.min(60, u) * 0.9);
}
const BOX_BASE_W = 46;   // 平面圖機櫃方框預設寬（px）
function boxStyle(r: Rack) {
  const s: Record<string, string> = {
    left: (r.pos_x as number) * 100 + "%",
    top: (r.pos_y as number) * 100 + "%",
    transform: `translate(-50%, -50%) rotate(${r.pos_rot || 0}deg)`,
  };
  // 寬度（大小）：手動拉過用 pos_w 比例，否則固定預設 → 大小可拖、可保留。
  s.width = r.pos_w != null ? r.pos_w * 100 + "%" : BOX_BASE_W + "px";
  // 形狀：有設定寬×深 → 用 CSS aspect-ratio 鎖定俯視腳印比例(寬:深)，高度自動由寬推得
  //       （拖大小只改寬、形狀不變）。沒設寬深才退回手動 pos_h 或 U 數估高。
  if (r.width_mm && r.depth_mm) {
    s.aspectRatio = `${r.width_mm} / ${r.depth_mm}`;
  } else {
    s.height = r.pos_h != null ? r.pos_h * 100 + "%" : boxHeight(r) + "px";
  }
  return s;
}

// 拖曳右下角把手改變大小（以中心為錨點，半寬/半高 × 2 = 比例）
let resizeId: string | null = null;
function onResizeDown(r: Rack, ev: PointerEvent) {
  if (!editMode.value) return;
  ev.preventDefault();
  resizeId = r.id;
  window.addEventListener("pointermove", onResizeMove);
  window.addEventListener("pointerup", onResizeUp);
}
function onResizeMove(ev: PointerEvent) {
  if (!resizeId) return;
  const f = fracFromEvent(ev);
  if (!f) return;
  const r = racks.value.find((x) => x.id === resizeId);
  if (!r) return;
  r.pos_w = Math.min(1, Math.max(0.02, +(2 * (f.x - (r.pos_x as number))).toFixed(4)));
  r.pos_h = Math.min(1, Math.max(0.02, +(2 * (f.y - (r.pos_y as number))).toFixed(4)));
  dirty.value = true;
}
function onResizeUp() {
  resizeId = null;
  window.removeEventListener("pointermove", onResizeMove);
  window.removeEventListener("pointerup", onResizeUp);
}

async function save() {
  // positions 視為完整擺放狀態：未列入的機櫃後端會清空座標（= 移除擺放）
  const positions = placed().map((r) => ({
    id: r.id, pos_x: r.pos_x as number, pos_y: r.pos_y as number, pos_rot: r.pos_rot ?? 0,
    pos_w: r.pos_w ?? null, pos_h: r.pos_h ?? null,
  }));
  try {
    await setRackPositions(props.locationId, positions);
    dirty.value = false;
    editMode.value = false;
    msg.success(t("common.ok"));
    await load();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
</script>

<template>
  <div class="floorplan-wrap">
    <input ref="fileInput" type="file" accept="image/png,image/jpeg,image/gif,image/webp"
           style="display:none" @change="onFile" />

    <n-space align="center" style="margin-bottom: 10px" :wrap-item="false">
      <n-icon :size="18"><RacksIcon /></n-icon>
      <strong>{{ t("racks.floor_plan") }}</strong>
      <template v-if="canEdit">
        <n-button size="small" @click="pickFile">
          <template #icon><n-icon><PlusIcon /></n-icon></template>
          {{ hasPlan ? t("racks.fp_replace") : t("racks.fp_upload") }}
        </n-button>
        <n-button v-if="hasPlan && !editMode" size="small" @click="editMode = true">
          <template #icon><n-icon><EditIcon /></n-icon></template>
          {{ t("racks.fp_edit") }}
        </n-button>
        <n-button v-if="editMode" size="small" type="primary" :disabled="!dirty" @click="save">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
        <n-button v-if="editMode" size="small" @click="load">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-popconfirm v-if="hasPlan" @positive-click="removePlan">
          <template #trigger>
            <n-button size="small" type="error" quaternary>
              <template #icon><n-icon><DeleteIcon /></n-icon></template>
              {{ t("racks.fp_remove") }}
            </n-button>
          </template>
          {{ t("racks.fp_remove_confirm") }}
        </n-popconfirm>
      </template>
      <span v-if="editMode" class="hint">{{ t("racks.fp_edit_hint") }}</span>
      <span v-else-if="hasPlan" class="hint">{{ t("racks.fp_view_hint") }}</span>
    </n-space>

    <n-empty v-if="!hasPlan && !loading" :description="t('racks.fp_empty')" style="padding: 32px 0" />

    <div
      v-else-if="imgUrl" ref="planEl" class="plan" :class="{ editing: editMode, panning }"
      @pointerdown="onPanStart"
    >
      <div
        ref="innerEl" class="plan-inner"
        :style="{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: '0 0' }"
      >
        <img ref="imgEl" :src="imgUrl" class="plan-img" draggable="false" alt="floor plan" @load="fitToView" />
        <div
          v-for="r in placed()" :key="r.id"
          class="rackbox" :class="{ drag: editMode }"
          :style="boxStyle(r)"
          :title="`${r.name} · ${r.u_height}U`"
          @pointerdown.stop="onMarkerDown(r, $event)"
        >
          <!-- 標籤反向縮放 → 不論平面圖縮放比例都維持可讀的螢幕字級 -->
          <div class="rb-label" :style="markStyle(r)">
            <n-icon :size="13"><RacksIcon /></n-icon>
            <span class="m-name">{{ r.name }}</span>
            <span class="m-u">{{ r.u_height }}U</span>
          </div>
          <span v-if="editMode" class="m-rot" :style="markStyle(r)" :title="t('racks.fp_rotate')"
                @pointerdown.stop="onRotateDown(r, $event)">⟳</span>
          <span v-if="editMode" class="m-x" :style="markStyle(r)" @pointerdown.stop @click.stop="unplace(r)">×</span>
          <span v-if="editMode" class="m-resize" :style="markStyle(r)" :title="t('racks.fp_resize')"
                @pointerdown.stop="onResizeDown(r, $event)"></span>
        </div>
      </div>
    </div>
    <!-- 縮放工具列：移到平面圖下方，避免蓋住圖面 -->
    <div v-if="imgUrl" class="plan-ctrls" @pointerdown.stop @wheel.stop>
      <n-button size="tiny" circle @click="zoomPct = zoomPct - 10">－</n-button>
      <n-slider v-model:value="zoomPct" :min="10" :max="400" :step="5" :tooltip="false" style="width: 130px" />
      <n-button size="tiny" circle @click="zoomPct = zoomPct + 10">＋</n-button>
      <n-input-number v-model:value="zoomPct" size="tiny" :min="10" :max="400" :step="5"
                      style="width: 92px" :show-button="false">
        <template #suffix>%</template>
      </n-input-number>
      <n-button size="tiny" @click="resetView">{{ t("racks.fp_reset_view") }}</n-button>
    </div>

    <!-- 未擺放機櫃托盤（編輯模式）-->
    <div v-if="editMode && unplaced().length" class="tray">
      <span class="tray-label">{{ t("racks.fp_unplaced") }}：</span>
      <n-button v-for="r in unplaced()" :key="r.id" size="tiny" dashed @click="placeFromTray(r)">
        {{ r.name }}
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.plan {
  position: relative;
  width: 100%;
  height: 62vh;
  overflow: hidden;
  border: 1px solid rgba(127,127,127,0.25);
  border-radius: 8px;
  background: rgba(127,127,127,0.04);
  cursor: grab;
  touch-action: none;
}
.plan.panning { cursor: grabbing; }
.plan.editing { cursor: crosshair; }
.plan-inner { position: absolute; top: 0; left: 0; width: 100%; }
.plan-img { display: block; width: 100%; height: auto; user-select: none; pointer-events: none; }
.plan-ctrls {
  margin-top: 10px;
  display: flex; gap: 8px; align-items: center; justify-content: center;
  flex-wrap: wrap;
  padding: 6px 10px; border-radius: 8px;
  background: rgba(127,127,127,0.06);
  cursor: default;
}
/* 機櫃方框：寬度固定、高度隨 U 數，外觀像俯視的機櫃 */
.rackbox {
  position: absolute;
  width: 46px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  padding: 2px;
  border-radius: 4px;
  background: linear-gradient(180deg, #2f6fd8, #2059b0);
  border: 1px solid rgba(255,255,255,0.35);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  box-shadow: 0 1px 5px rgba(0,0,0,0.4);
  cursor: pointer;
}
.rackbox .m-name { max-width: 42px; overflow: hidden; text-overflow: ellipsis; }
.rackbox .m-u { font-size: 9px; opacity: 0.8; font-weight: 500; }
.rackbox.drag { cursor: grab; }
.rackbox.drag:active { cursor: grabbing; }
.rackbox .m-rot, .rackbox .m-x {
  position: absolute; top: -8px;
  width: 16px; height: 16px; line-height: 15px; text-align: center;
  border-radius: 50%; background: rgba(0,0,0,0.55); font-size: 11px; font-weight: 700;
  cursor: pointer;
}
.rackbox .m-rot { right: 28px; }
.rackbox .m-x { right: -6px; }
.rackbox .m-rot:hover, .rackbox .m-x:hover { background: rgba(0,0,0,0.85); }
.rackbox .m-resize {
  position: absolute; right: -4px; bottom: -4px;
  width: 12px; height: 12px; border-radius: 2px;
  background: #fff; border: 2px solid #2059b0; cursor: nwse-resize;
}
.tray {
  margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  padding: 8px 10px; background: rgba(127,127,127,0.06); border-radius: 6px;
}
.tray-label { font-size: 12px; opacity: 0.7; }
.hint { font-size: 12px; opacity: 0.6; }
</style>
