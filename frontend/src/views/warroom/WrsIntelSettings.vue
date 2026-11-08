<script setup lang="ts">
/**
 * 管理 → 戰情室設定裡的「12 AI 情報」區塊（2026-10-26）。使用者指示：設定做在原本的戰情室設定區，不另做設計稿的設定頁。
 *
 * - 預覽用固定的示範資料（設計稿第 8 節：不呼叫模型），跟電視上的 WrAiIntel 是同一個元件，等比例縮小
 * - 自訂配色只收 #RRGGBB；一般文字對比至少 4.5:1、大字（警告 / 重大）至少 3:1，不足時提示並可一鍵修正
 * - 「立即產生」是獨立的明確操作，後端 60 秒冷卻
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCheckbox, NCheckboxGroup, NColorPicker, NFormItem, NIcon, NInputNumber, NRadioButton, NRadioGroup,
  NSelect, NSpace, NSwitch, useMessage,
} from "naive-ui";
import { PlayIcon, RefreshIcon, CheckIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { generateIntelNow, type IntelColors, type IntelEvents, type IntelSettings, type IntelState } from "@/api/warroom";
import { getAnomalySchedule, type AnomalySchedule } from "@/api/phase3";
import { getLLMConfig, type LLMConfig } from "@/api/system";
import { ANOMALY_CATEGORIES, anomalyCategoryLabel } from "./categories";
import WrAiIntel from "./WrAiIntel.vue";
import WrFx from "./WrFx.vue";

const model = defineModel<IntelSettings>({ required: true });
/** 戰情室設定的「資訊展示模式」：預覽照那個樣子播，WARNING 區塊寫明不會出現 */
const props = defineProps<{ infoMode?: boolean }>();
const { t, te } = useI18n();
const msg = useMessage();

const DOMAINS = ["capacity", "anomalies", "dns", "dhcp", "collection", "topology", "virt", "certs", "ai_audit"];
const DEFAULT_COLORS: IntelColors = { bg: "#0a1012", text: "#dce8e4", secondary: "#a8a3ce", accent: "#88ebc1",
                                      warn: "#e9b861", critical: "#f07866" };
const COLOR_KEYS = Object.keys(DEFAULT_COLORS) as (keyof IntelColors)[];

const opts = (keys: string[], prefix: string) => keys.map((v) => ({ label: t(`warroom_settings.intel_${prefix}_${v}`), value: v }));
const speedOpts = computed(() => opts(["slow", "normal", "fast"], "speed"));
const effectOpts = computed(() => opts(["clear", "standard", "cinema"], "fx"));
const scanOpts = computed(() => opts(["off", "low", "medium"], "sl"));
const noiseOpts = computed(() => opts(["off", "low"], "nz"));
const paletteOpts = computed(() => opts(["phosphor", "amber", "ice", "custom"], "pal"));
const catOpts = computed(() => ANOMALY_CATEGORIES.map((c) => ({ label: anomalyCategoryLabel(c, t, te), value: c })));

// ─────────────────────────── 對比 ───────────────────────────
function lum(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrast(a: string, b: string): number {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
/** 每個前景色要達到的對比：正文、次要、強調（目前這則的字色）4.5；警告與重大是粗體大字 3 */
const NEED: Record<string, number> = { text: 4.5, secondary: 4.5, accent: 4.5, warn: 3, critical: 3 };
const lowContrast = computed(() => {
  const c = model.value.colors;
  if (model.value.palette !== "custom") return [];
  return Object.keys(NEED).filter((k) => contrast(c[k as keyof IntelColors], c.bg) < NEED[k]);
});
/** 一鍵修正：往背景的反方向調亮（或調暗）到剛好過門檻 */
function mix(hex: string, to: string, f: number): string {
  const p = (h: string, i: number) => parseInt(h.slice(i, i + 2), 16);
  return "#" + [1, 3, 5].map((i) => Math.round(p(hex, i) + (p(to, i) - p(hex, i)) * f).toString(16).padStart(2, "0")).join("");
}
function fixContrast() {
  const c = model.value.colors;
  const toward = lum(c.bg) < 0.18 ? "#ffffff" : "#000000";
  for (const k of lowContrast.value) {
    const key = k as keyof IntelColors;
    let f = 0;
    while (f < 1 && contrast(mix(c[key], toward, f), c.bg) < NEED[k] + 0.05) f += 0.02;
    c[key] = mix(c[key], toward, Math.min(f, 1));
  }
}
function resetColors() { model.value.colors = { ...DEFAULT_COLORS }; }
function setColor(k: keyof IntelColors, v: string) {
  if (/^#[0-9a-fA-F]{6}$/.test(v)) model.value.colors[k] = v.toLowerCase();
}

// ─────────────────────────── 預覽：固定示範資料（文件用 IP，不接資料庫、不呼叫模型） ───────────────────────────
function demo(): { state: IntelState; events: IntelEvents } {
  const now = Date.now();
  const iso = (minAgo: number) => new Date(now - minAgo * 60000).toISOString();
  const infoItems = [
    { id: "i1", fact_ids: [], category: "overview", state: "info", code: "overview_info",
      params: { at: iso(1), registered_ips: 1367, subnets: 42 } },
    { id: "i2", fact_ids: [], category: "capacity", state: "info", code: "capacity_info",
      params: { top_label: "203.0.113.0/24", top_pct: 64 } },
    { id: "i3", fact_ids: [], category: "dns", state: "info", code: "dns_info", params: { groups: 3 } },
    { id: "i4", fact_ids: [], category: "certs", state: "info", code: "certs_info", params: { total: 18 } },
  ];
  const items = props.infoMode ? infoItems : [
    { id: "d1", fact_ids: [], category: "overview", state: "info", code: "overview",
      params: { at: iso(1), registered_ips: 1367, open_anomalies: 4 } },
    { id: "d2", fact_ids: [], category: "events", state: "new", code: "events_changed",
      params: { since: iso(6), new: 1, resolved: 0, examples: [{ category: "ip_conflicts", label: "", ip: "198.51.100.80" }] } },
    { id: "d3", fact_ids: [], category: "ongoing", state: "ongoing", code: "ongoing_anomaly",
      params: { category: "ip_conflicts", ip: "198.51.100.80", label: "", first_seen_at: iso(48), baseline: false } },
    { id: "d4", fact_ids: [], category: "capacity", state: "watch", code: "capacity",
      params: { high_util: 2, warn: 80, top_label: "203.0.113.0/24", top_pct: 91, pools_alert: 0 } },
    { id: "d5", fact_ids: [], category: "integrity", state: "watch", code: "integrity",
      params: { total: 6, stale: 1, failed: 0, never: 0,
                examples: [{ name: "DHCP", state: "stale", last_success_at: iso(95) }] } },
  ];
  const fresh = (id: string, name: string, min: number, state = "fresh") =>
    ({ id, name, kind: id, label: name, last_success_at: iso(min), state });
  const state = {
    generating: false, ai_enabled: true, last_error_code: null, next_due_at: new Date(now + 4 * 60000).toISOString(),
    last_attempt_at: iso(1), stale: false, outdated: false,
    batch: {
      id: "demo", status: "ok", origin: "model", model: null, generated_at: iso(1), checked_at: iso(1),
      snapshot_at: iso(1), baseline: false, items,
      pinned: [{ id: "p1", kind: "anomaly", category: "ip_conflicts", label: "", ip: "198.51.100.80", state: "open" },
               { id: "p2", kind: "source", label: "DHCP", ip: null, state: "stale" }],
      sources: [fresh("scan", "Scanner", 2), fresh("dhcp", "DHCP", 95, "stale"), fresh("dns", "DNS", 7),
                fresh("snmp", "SNMP", 4), fresh("virt", "Proxmox VE", 9), fresh("mon", "Zabbix", 3)],
      stats: {},
    },
  } as unknown as IntelState;
  const events = {
    state: "ok", checked_at: iso(0),
    items: model.value.warning_enabled
      ? [{ id: "e1", category: "ip_conflicts", label: "", ip: "198.51.100.80", first_seen_at: iso(3), state: "open" }]
      : [],
  } as unknown as IntelEvents;
  return { state, events };
}
const demoData = ref(demo());
const preview = computed(() => ({ ...demoData.value, display: model.value, info: !!props.infoMode }));
const previewKey = ref(0);
function replayPreview() { demoData.value = demo(); previewKey.value++; }
watch(() => props.infoMode, replayPreview);

/* 預覽照電視上這個主題內容區的大小（1920×1080 畫布扣掉頁首、指標列、頁尾＝1824×728）排版，再等比例縮到卡片寬度
 * （字級寫死給電視看，直接放進卡片會擠爆） */
const STAGE_W = 1824;
const STAGE_H = 728;
const box = ref<HTMLElement | null>(null);
const scale = ref(0.4);
let ro: ResizeObserver | null = null;
onMounted(() => {
  if (!box.value) return;
  ro = new ResizeObserver(([e]) => { scale.value = e.contentRect.width / STAGE_W; });
  ro.observe(box.value);
});
onBeforeUnmount(() => ro?.disconnect());

// ─────────────────────────── 異常偵測的排程 ───────────────────────────
// WARNING 不等 AI，但要等異常偵測記錄到事件才會出現：把目前的偵測頻率寫在警示設定旁邊，
// 不是「每 N 分鐘」時建議改（使用者 10-26：「這個寫在設定頁裡提示」）。
const detect = ref<AnomalySchedule | null>(null);
onMounted(async () => { try { detect.value = await getAnomalySchedule(); } catch { /* 讀不到就不顯示 */ } });

/** 用的是哪個模型：沿用 AI 對話的設定（使用者 10-26：要特別標示並可以直接過去看設定） */
const llm = ref<LLMConfig | null>(null);
onMounted(async () => { try { llm.value = await getLLMConfig(); } catch { /* 讀不到就只寫說明 */ } });
const llmOn = computed(() => !!llm.value?.enabled && !!llm.value?.chat_model);
const detectText = computed(() => {
  const d = detect.value;
  if (!d) return "";
  const times = d.times.join(" / ");
  if (!d.schedule_enabled) return t("warroom_settings.intel_det_off");
  if (d.frequency === "interval") return t("warroom_settings.intel_det_interval", { n: d.interval_minutes });
  if (d.frequency === "weekly") {
    return t("warroom_settings.intel_det_weekly", {
      days: d.weekdays.map((w) => t(`llm_settings.weekday_${w}`)).join(t("warroom.intel.t.sep")), times });
  }
  if (d.frequency === "monthly") return t("warroom_settings.intel_det_monthly", { day: d.month_day, times });
  return t("warroom_settings.intel_det_daily", { times });
});
const detectSuggest = computed(() => !!detect.value && (!detect.value.schedule_enabled || detect.value.frequency !== "interval"));

// ─────────────────────────── 立即產生 ───────────────────────────
const generating = ref(false);
async function generateNow() {
  generating.value = true;
  try {
    await generateIntelNow();
    msg.success(t("warroom_settings.intel_generate_started"));
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { generating.value = false; }
}
</script>

<template>
  <div class="wri" data-testid="wrs-intel">
    <p class="wri-intro">{{ t("warroom_settings.intel_intro") }}</p>
    <!-- 使用者 10-26：「加個提示標籤明顯一點」，怎麼讓電視一直停在這個主題 -->
    <n-alert type="info" :bordered="false" class="wri-alert wri-pin" data-testid="wrs-intel-pin-tip"
             :title="t('warroom_settings.intel_pin_title')">
      <ul class="wri-pin__list">
        <li>{{ t("warroom_settings.intel_pin_key") }}</li>
        <li>{{ t("warroom_settings.intel_pin_playlist") }}</li>
        <li>{{ t("warroom_settings.intel_pin_setting") }}</li>
      </ul>
    </n-alert>

    <div class="wri-preview">
      <div class="wri-preview__head">
        <span class="wri-sub">{{ t("warroom_settings.intel_preview") }}</span>
        <n-button size="tiny" quaternary @click="replayPreview">
          <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("warroom_settings.intel_preview_replay") }}
        </n-button>
      </div>
      <div ref="box" class="wri-preview__box" :style="{ height: `${STAGE_H * scale}px` }" data-testid="wrs-intel-preview">
        <div class="wri-preview__stage" :style="{ transform: `scale(${scale})` }">
          <WrAiIntel :key="previewKey" :preview="preview" />
          <WrFx :effects="model.effects" :scanlines="model.scanlines" :noise="model.noise" />
        </div>
      </div>
      <div class="wri-hint">{{ t("warroom_settings.intel_preview_hint") }}</div>
    </div>

    <div class="wri-sec">{{ t("warroom_settings.intel_sec_content") }}</div>
    <div class="wri-grid">
      <n-form-item :label="t('warroom_settings.intel_interval')">
        <n-input-number v-model:value="model.interval_minutes" :min="1" :max="60" data-testid="wrs-intel-interval">
          <template #suffix>{{ t("warroom_settings.intel_minutes") }}</template>
        </n-input-number>
      </n-form-item>
      <n-form-item :label="t('warroom_settings.intel_dwell')">
        <n-input-number v-model:value="model.dwell_seconds" :min="10" :max="300">
          <template #suffix>{{ t("warroom_settings.seconds") }}</template>
        </n-input-number>
      </n-form-item>
      <n-form-item :label="t('warroom_settings.intel_item_limit')">
        <n-input-number v-model:value="model.item_limit" :min="4" :max="30" />
      </n-form-item>
    </div>
    <n-alert :type="llm && !llmOn ? 'warning' : 'info'" :bordered="false" class="wri-alert" data-testid="wrs-intel-model">
      <template v-if="llm && llmOn">{{ t("warroom_settings.intel_model_tag", { model: llm.chat_model }) }}</template>
      <template v-else-if="llm">{{ t("warroom_settings.intel_model_none") }}</template>
      <template v-else>{{ t("warroom_settings.intel_model_tag_plain") }}</template>
      <router-link to="/llm" class="wri-link" data-testid="wrs-intel-model-link">{{ t("warroom_settings.intel_model_link") }}</router-link>
    </n-alert>
    <p class="wri-hint wri-hint--block wri-warn" data-testid="wrs-intel-llm-load">{{ t("warroom_settings.intel_interval_load") }}</p>
    <p class="wri-hint wri-hint--block">{{ t("warroom_settings.intel_model_hint") }}</p>
    <n-form-item :label="t('warroom_settings.intel_domains')" :show-feedback="false">
      <n-checkbox-group v-model:value="model.domains" data-testid="wrs-intel-domains">
        <n-space :size="[14, 6]">
          <n-checkbox v-for="d in DOMAINS" :key="d" :value="d" :label="t(`warroom.intel.cat.${d}`)" />
        </n-space>
      </n-checkbox-group>
    </n-form-item>
    <p class="wri-hint wri-hint--block" style="margin: 4px 0 16px">{{ t("warroom_settings.intel_domains_hint") }}</p>
    <n-form-item :label="t('warroom_settings.intel_mask_ips')" label-placement="left">
      <n-switch v-model:value="model.mask_ips" />
      <span class="wri-hint">{{ t("warroom_settings.intel_mask_ips_hint") }}</span>
    </n-form-item>
    <n-form-item :label="t('warroom_settings.intel_background')" label-placement="left">
      <n-switch v-model:value="model.background" data-testid="wrs-intel-background" />
      <span class="wri-hint">{{ t("warroom_settings.intel_background_hint") }}</span>
    </n-form-item>

    <div class="wri-sec">{{ t("warroom_settings.intel_sec_display") }}</div>
    <div class="wri-row">
      <n-form-item :label="t('warroom_settings.intel_layout')">
        <n-radio-group v-model:value="model.layout" size="small" data-testid="wrs-intel-layout">
          <n-radio-button value="three_panel">{{ t("warroom_settings.intel_layout_three_panel") }}</n-radio-button>
          <n-radio-button value="focus">{{ t("warroom_settings.intel_layout_focus") }}</n-radio-button>
        </n-radio-group>
      </n-form-item>
      <n-form-item :label="t('warroom_settings.intel_playback')">
        <n-radio-group v-model:value="model.playback" size="small">
          <n-radio-button value="rotate">{{ t("warroom_settings.intel_playback_rotate") }}</n-radio-button>
          <n-radio-button value="pinned">{{ t("warroom_settings.intel_playback_pinned") }}</n-radio-button>
        </n-radio-group>
      </n-form-item>
    </div>
    <p class="wri-hint wri-hint--block">{{ t("warroom_settings.intel_playback_hint") }}</p>
    <div class="wri-grid">
      <n-form-item :label="t('warroom_settings.intel_speed')">
        <n-select v-model:value="model.speed" :options="speedOpts" />
      </n-form-item>
      <n-form-item :label="t('warroom_settings.intel_effects')">
        <n-select v-model:value="model.effects" :options="effectOpts" />
      </n-form-item>
      <n-form-item :label="t('warroom_settings.intel_scanlines')">
        <n-select v-model:value="model.scanlines" :options="scanOpts" />
      </n-form-item>
      <n-form-item :label="t('warroom_settings.intel_noise')">
        <n-select v-model:value="model.noise" :options="noiseOpts" />
      </n-form-item>
    </div>
    <n-form-item :label="t('warroom_settings.intel_rhythm')" label-placement="left">
      <n-switch v-model:value="model.rhythm" />
      <span class="wri-hint" data-testid="wrs-intel-rhythm-hint">{{ t("warroom_settings.intel_rhythm_hint") }}</span>
    </n-form-item>
    <n-form-item :label="t('warroom_settings.intel_cursor')" label-placement="left">
      <n-switch v-model:value="model.cursor" />
      <span class="wri-hint">{{ t("warroom_settings.intel_cursor_hint") }}</span>
    </n-form-item>

    <div class="wri-sec">{{ t("warroom_settings.intel_sec_colors") }}</div>
    <n-form-item :label="t('warroom_settings.intel_palette')">
      <n-radio-group v-model:value="model.palette" size="small" data-testid="wrs-intel-palette">
        <n-radio-button v-for="o in paletteOpts" :key="o.value" :value="o.value">{{ o.label }}</n-radio-button>
      </n-radio-group>
    </n-form-item>
    <template v-if="model.palette === 'custom'">
      <div class="wri-colors" data-testid="wrs-intel-colors">
        <div v-for="k in COLOR_KEYS" :key="k" class="wri-color">
          <span class="wri-color__k">{{ t(`warroom_settings.intel_color_${k}`) }}</span>
          <n-color-picker :value="model.colors[k]" :modes="['hex']" :show-alpha="false" size="small"
                          @update:value="(v: string) => setColor(k, v)" />
          <span v-if="k !== 'bg'" class="wri-color__r" :class="{ bad: lowContrast.includes(k) }">
            {{ contrast(model.colors[k], model.colors.bg).toFixed(1) }}:1</span>
        </div>
      </div>
      <n-alert v-if="lowContrast.length" type="warning" :bordered="false" class="wri-alert" data-testid="wrs-intel-contrast">
        {{ t("warroom_settings.intel_contrast_low", { list: lowContrast.map((k) => t(`warroom_settings.intel_color_${k}`)).join(" / ") }) }}
        <n-button size="tiny" type="warning" class="wri-fix" data-testid="wrs-intel-fix" @click="fixContrast"><template #icon><n-icon><CheckIcon /></n-icon></template>
          {{ t("warroom_settings.intel_contrast_fix") }}</n-button>
      </n-alert>
      <n-button size="small" class="wri-reset" @click="resetColors">
        <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("warroom_settings.intel_colors_reset") }}
      </n-button>
    </template>

    <div class="wri-sec">{{ t("warroom_settings.intel_sec_warning") }}</div>
    <n-alert v-if="props.infoMode" type="info" :show-icon="false" :bordered="false" class="wri-alert" data-testid="wrs-intel-info-note">
      {{ t("warroom_settings.info_mode_intel_note") }}</n-alert>
    <n-form-item :label="t('warroom_settings.intel_warning_enabled')" label-placement="left">
      <n-switch v-model:value="model.warning_enabled" data-testid="wrs-intel-warning" />
    </n-form-item>
    <template v-if="model.warning_enabled">
      <n-form-item :label="t('warroom_settings.intel_warning_categories')" :show-feedback="false">
        <n-select v-model:value="model.warning_categories" :options="catOpts" multiple style="max-width: 640px"
                  data-testid="wrs-intel-warning-cats" />
      </n-form-item>
      <p class="wri-hint wri-hint--block" style="margin: 4px 0 16px">{{ t("warroom_settings.intel_warning_categories_hint") }}</p>
      <n-alert v-if="detectText" type="info" :show-icon="false" :bordered="false" class="wri-alert" data-testid="wrs-intel-detect">
        {{ detectText }}<template v-if="detectSuggest">{{ t("warroom_settings.intel_det_suggest") }}</template>
        <router-link to="/anomaly" class="wri-link" data-testid="wrs-intel-detect-link">{{ t("warroom_settings.intel_det_link") }}</router-link>
      </n-alert>
      <n-form-item :label="t('warroom_settings.intel_breathe')" label-placement="left">
        <n-switch v-model:value="model.warning_breathe" data-testid="wrs-intel-breathe" />
        <span class="wri-hint">{{ t("warroom_settings.intel_breathe_hint") }}</span>
      </n-form-item>
    </template>

    <div class="wri-gen">
      <n-button type="info" size="small" :loading="generating" data-testid="wrs-intel-generate" @click="generateNow">
        <template #icon><n-icon :component="PlayIcon" /></template>{{ t("warroom_settings.intel_generate") }}
      </n-button>
      <span class="wri-hint">{{ t("warroom_settings.intel_generate_hint") }}</span>
    </div>
  </div>
</template>

<style scoped>
.wri { display: flex; flex-direction: column; }
.wri-intro { margin: 0 0 12px; opacity: .8; line-height: 1.7; }
.wri-hint { margin-left: 12px; opacity: .7; font-size: 13px; }
.wri-hint--block { margin: -6px 0 14px; display: block; }
.wri-sec { font-weight: 600; font-size: 14px; margin: 14px 0 10px; padding-top: 12px; border-top: 1px dashed rgba(128, 128, 128, .3); }
.wri-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0 18px; max-width: 960px; }
.wri-row { display: flex; flex-wrap: wrap; gap: 0 28px; }
.wri-preview { max-width: 960px; margin-bottom: 6px; }
.wri-preview__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.wri-sub { font-weight: 600; font-size: 13px; }
.wri-preview__box { position: relative; width: 100%; overflow: hidden; border-radius: 6px; border: 1px solid rgba(128, 128, 128, .3); }
.wri-preview__stage { position: absolute; top: 0; left: 0; width: 1824px; height: 728px; transform-origin: 0 0; }
.wri-preview .wri-hint { margin: 6px 0 0; display: block; }
.wri-colors { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px 18px; max-width: 960px; margin-bottom: 12px; }
.wri-color { display: grid; grid-template-columns: 64px minmax(0, 1fr) 56px; gap: 10px; align-items: center; font-size: 13px; }
.wri-color__k { opacity: .8; }
.wri-color__r { font-family: ui-monospace, monospace; font-size: 12px; opacity: .8; }
.wri-color__r.bad { color: var(--n-warning-color, #f0a020); opacity: 1; font-weight: 600; }
.wri-alert { margin-bottom: 10px; max-width: 960px; }
.wri-fix { margin-left: 10px; }
.wri-link { margin-left: 10px; white-space: nowrap; }
.wri-pin { max-width: 960px; margin-bottom: 14px; }
.wri-pin__list { margin: 4px 0 0; padding-left: 20px; line-height: 1.8; }
.wri-warn { color: var(--n-warning-color, #d08700); opacity: 1; }
.wri-reset { align-self: flex-start; margin-bottom: 8px; }
.wri-gen { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin: 14px 0 6px; }
.wri-gen .wri-hint { margin-left: 6px; }
</style>
