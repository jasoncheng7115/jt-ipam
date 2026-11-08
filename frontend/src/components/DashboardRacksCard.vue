<script setup lang="ts">
/**
 * 儀表板的「機櫃」卡片（放在最下面，2026-10-01 使用者要求）：直接看機櫃圖。
 *
 * 可以設定看哪個機房（那一間的全部機櫃）或挑幾個機櫃。設定跟著帳號存（user_preferences.pinned 的
 * `dash_rack_room`／`dash_racks` 兩個 namespace），換瀏覽器也一樣。沒設定時顯示一個「設定」按鈕。
 * 一次最多畫 MAX 個機櫃：機房很大時其餘的連到機櫃頁看。
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NButton, NCard, NEmpty, NForm, NFormItem, NIcon, NModal, NRadio, NRadioGroup, NSelect, NSlider, NSpace, NSpin, NTag,
} from "naive-ui";
import RackDiagram from "@/components/RackDiagram.vue";
import CardTitle from "@/components/CardTitle.vue";
import { getRackDiagram } from "@/api/racks";
import { usePinned } from "@/composables/usePinned";
import { RACK_DEVICE_TYPES, rackTypeFill } from "@/utils/rackColors";
import { RacksIcon, SettingsIcon, CancelIcon, SaveIcon } from "@/icons";

const props = defineProps<{
  locations: { id: string; name: string }[];
  racks: { id: string; name: string; location_id: string | null }[];
}>();
const { t } = useI18n();
const router = useRouter();
const MAX = 12;

const roomPin = usePinned("dash_rack_room");
const rackPin = usePinned("dash_racks");
// 顯示大小（使用者 2026-10-06）：整排照實際比例畫以後，矮的機櫃在高層架旁邊會很小，要能自己放大。
// 跟其他設定一樣存在帳號的偏好裡（釘選欄位的值是字串）
const scalePin = usePinned("dash_rack_scale");
const SCALE_MIN = 0.5, SCALE_MAX = 3;
const scale = computed(() => {
  const v = Number(scalePin.ids.value[0]);
  return Number.isFinite(v) && v > 0 ? Math.min(SCALE_MAX, Math.max(SCALE_MIN, v)) : 1;
});
const roomId = computed(() => roomPin.ids.value[0] ?? null);

/** 要畫的機櫃：設了機房就是那一間的全部（依名稱），否則是挑的那幾個（依挑的順序） */
const wanted = computed(() => {
  if (roomId.value) {
    return props.racks.filter((r) => r.location_id === roomId.value)
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  }
  const byId = new Map(props.racks.map((r) => [r.id, r]));
  return rackPin.ids.value.map((id) => byId.get(id)).filter(Boolean) as typeof props.racks;
});
const shown = computed(() => wanted.value.slice(0, MAX));
const configured = computed(() => !!roomId.value || rackPin.ids.value.length > 0);
const subtitle = computed(() => {
  if (roomId.value) return props.locations.find((l) => l.id === roomId.value)?.name ?? "";
  return rackPin.ids.value.length ? t("dashboard.racks_picked", { n: wanted.value.length }) : "";
});

const diagrams = ref<any[]>([]);
const loading = ref(false);
let seq = 0;
watch(() => shown.value.map((r) => r.id).join(","), async (key) => {
  const mine = ++seq;
  if (!key) { diagrams.value = []; return; }
  loading.value = true;
  const res = await Promise.allSettled(shown.value.map((r) => getRackDiagram(r.id)));
  if (mine !== seq) return;
  diagrams.value = res.flatMap((x) => (x.status === "fulfilled" ? [x.value] : []));
  measured.value = {};
  loading.value = false;
}, { immediate: true });

// 每台量到的自然高度（未縮放 px）；整排最高的那台決定共用的縮放比例
const measured = ref<Record<string, number>>({});
function onMeasured(rackId: string, px: number) {
  if (measured.value[rackId] !== px) measured.value = { ...measured.value, [rackId]: px };
}
const rowMaxPx = computed(() => {
  const ids = new Set(diagrams.value.map((d) => d.rack_id));
  return Math.max(0, ...Object.entries(measured.value).filter(([id]) => ids.has(id)).map(([, v]) => v));
});

function openRack(id: string) {
  void router.push({ name: "racks", query: { rack: id } });
}
function openRacksPage() {
  void router.push({ name: "racks" });
}

// ── 設定 ──
const show = ref(false);
const mode = ref<"room" | "racks">("room");
const formRoom = ref<string | null>(null);
const formRacks = ref<string[]>([]);
const formScale = ref(1);
function openSettings() {
  formScale.value = scale.value;
  mode.value = roomId.value || !rackPin.ids.value.length ? "room" : "racks";
  formRoom.value = roomId.value;
  formRacks.value = [...rackPin.ids.value];
  show.value = true;
}
function save() {
  scalePin.setAll(formScale.value === 1 ? [] : [String(Math.round(formScale.value * 100) / 100)]);
  if (mode.value === "room") {
    roomPin.setAll(formRoom.value ? [formRoom.value] : []);
    rackPin.setAll([]);
  } else {
    roomPin.setAll([]);
    rackPin.setAll(formRacks.value);
  }
  show.value = false;
}
const locOptions = computed(() => props.locations.map((l) => ({ label: l.name, value: l.id })));
const rackOptions = computed(() => props.racks.map((r) => ({
  label: `${r.name}${r.location_id ? `（${props.locations.find((l) => l.id === r.location_id)?.name ?? ""}）` : ""}`,
  value: r.id,
})));
</script>

<template>
  <n-card data-testid="dash-racks">
    <!-- 標題列只放標題＋數量（使用者要求：儀表板卡片標題不放按鈕、不放標題以外的文字）；
         顯示哪個機房／幾個機櫃與「設定」按鈕放內文最上方的控制列，比照 AI 巡檢卡 -->
    <template #header>
      <CardTitle :icon="RacksIcon" :text="t('dashboard.racks_title')">
        <n-tag v-if="configured && wanted.length" size="small" round :bordered="false">
          {{ wanted.length }}
        </n-tag>
      </CardTitle>
    </template>
    <div v-if="configured" class="dr-ctrl">
      <span class="dr-sub" data-testid="dash-racks-sub">{{ subtitle }}</span>
      <n-button size="small" data-testid="dash-racks-settings" @click="openSettings">
        <template #icon><n-icon><SettingsIcon /></n-icon></template>
        {{ t("dashboard.racks_settings") }}
      </n-button>
    </div>

    <n-empty v-if="!configured" :description="t('dashboard.racks_empty')">
      <template #extra>
        <n-button size="small" type="primary" data-testid="dash-racks-settings" @click="openSettings"><template #icon><n-icon><SettingsIcon /></n-icon></template>{{ t("dashboard.racks_settings") }}</n-button>
      </template>
    </n-empty>
    <n-empty v-else-if="!wanted.length" :description="t('dashboard.racks_none')" />
    <n-spin v-else :show="loading">
      <div class="dr-row">
        <div v-for="d in diagrams" :key="d.rack_id" class="dr-rack">
          <a class="dr-name" @click="openRack(d.rack_id)">{{ d.name }}</a>
          <!-- 儀表板空間有限：精簡列高、縮小比例，一排機櫃一眼看得完 -->
          <!-- 整排用同一個縮放比例（fit-to＝最高那台的自然高度），大小才看得出誰高誰矮 -->
          <!-- 點機櫃圖本身也到機櫃頁（使用者 2026-10-08：除了點標題外，點機櫃本身也要可以去）；
               設備自己的點擊（到設備頁）會 stopPropagation，不受影響 -->
          <div class="dr-diagram" role="link" tabindex="0" :title="t('dashboard.racks_open', { name: d.name })"
               data-testid="dash-rack-diagram" @click="openRack(d.rack_id)" @keydown.enter="openRack(d.rack_id)">
            <RackDiagram :diagram="d" :show-legend="false" :controls="false" :shared-zoom="0.55 * scale" compact bare
                         :fit-to="rowMaxPx || null" @measured="onMeasured" />
          </div>
        </div>
      </div>
      <div class="dr-foot">
        <span class="dr-legend">
          <span v-for="ty in RACK_DEVICE_TYPES" :key="ty" class="dr-chip" :style="{ background: rackTypeFill(ty) }">{{ t(`devices.type_${ty}`) }}</span>
        </span>
        <a v-if="wanted.length > shown.length" class="dr-more" @click="openRacksPage">
          {{ t("dashboard.racks_more", { n: wanted.length - shown.length }) }}
        </a>
      </div>
    </n-spin>

    <n-modal v-model:show="show" preset="card" :title="t('dashboard.racks_settings_title')"
             style="width: 520px; max-width: calc(100vw - 32px)">
      <n-form label-placement="top">
        <n-form-item :label="t('dashboard.racks_mode')">
          <n-radio-group v-model:value="mode">
            <n-space>
              <n-radio value="room">{{ t("dashboard.racks_mode_room") }}</n-radio>
              <n-radio value="racks">{{ t("dashboard.racks_mode_racks") }}</n-radio>
            </n-space>
          </n-radio-group>
        </n-form-item>
        <n-form-item v-if="mode === 'room'" :label="t('dashboard.racks_room')">
          <n-select v-model:value="formRoom" :options="locOptions" filterable clearable data-testid="dash-racks-room" />
        </n-form-item>
        <n-form-item v-else :label="t('dashboard.racks_pick')">
          <n-select v-model:value="formRacks" :options="rackOptions" multiple filterable clearable
                    data-testid="dash-racks-pick" />
        </n-form-item>
        <n-form-item :label="t('dashboard.racks_scale')" :show-feedback="false">
          <div class="dr-scale">
            <n-slider v-model:value="formScale" :min="SCALE_MIN" :max="SCALE_MAX" :step="0.1"
                      :format-tooltip="(v: number) => `${Math.round(v * 100)}%`" data-testid="dash-racks-scale" />
            <span class="dr-scale-v">{{ Math.round(formScale * 100) }}%</span>
          </div>
        </n-form-item>
        <div class="dr-hint" style="margin: 4px 0 16px">{{ t("dashboard.racks_scale_hint") }}</div>
        <div class="dr-hint">{{ t("dashboard.racks_hint", { n: MAX }) }}</div>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="show = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button type="primary" data-testid="dash-racks-save" @click="save"><template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.dr-ctrl { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.dr-sub { font-size: 12.5px; opacity: .65; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 機櫃一整排靠下對齊（落地），名稱一律在同一條線上（使用者：「機櫃名稱位置高度要統一」）：
   每一欄撐滿整排的高度，名稱在最上面、機櫃圖用 margin-top:auto 推到底。超出寬度左右捲 */
.dr-row { display: flex; flex-wrap: nowrap; gap: 18px; align-items: stretch; overflow-x: auto; padding: 0 2px 8px; }
.dr-rack { flex: 0 0 auto; display: flex; flex-direction: column; align-items: center; }
.dr-rack > :last-child { margin-top: auto; }
.dr-diagram { cursor: pointer; border-radius: 6px; transition: box-shadow .12s ease; }
.dr-diagram:hover { box-shadow: 0 0 0 2px rgba(24, 160, 88, .35); }
.dr-diagram:focus-visible { outline: 2px solid #18a058; outline-offset: 2px; }
.dr-name { font-weight: 600; font-size: 13px; margin-bottom: 6px; cursor: pointer;
           color: var(--primary-color, #18a058); white-space: nowrap; }
.dr-name:hover { text-decoration: underline; }
.dr-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-top: 8px; }
.dr-legend { display: flex; flex-wrap: wrap; gap: 6px; }
.dr-chip { font-size: 11px; color: #fff; padding: 1px 6px; border-radius: 4px; }
.dr-more { font-size: 12.5px; cursor: pointer; color: var(--primary-color, #18a058); }
.dr-hint { font-size: 12px; opacity: .65; }
.dr-scale { display: flex; align-items: center; gap: 12px; width: 100%; }
.dr-scale > :first-child { flex: 1 1 auto; }
.dr-scale-v { font-variant-numeric: tabular-nums; min-width: 44px; text-align: right; font-size: 13px; }
</style>
