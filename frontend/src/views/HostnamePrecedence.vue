<script setup lang="ts">
/**
 * 全域 Hostname 來源優先序 (feature A，admin)。
 * 原生 HTML5 拖拉排序，無額外相依。
 */
import { onMounted, ref } from "vue";
import { NButton, NCard, NSpin, NText, NSwitch, NSpace, NIcon, useMessage } from "naive-ui";
import { SaveIcon } from "@/icons";
import { useI18n } from "vue-i18n";
import {
  getHostnamePrecedence, setHostnamePrecedence,
  getArpPrecedence, setArpPrecedence,
  getDevNamePrecedence, setDevNamePrecedence,
  getModelPrecedence, setModelPrecedence,
  getOsPrecedence, setOsPrecedence,
} from "@/api/hostname";

const { t } = useI18n();
const msg = useMessage();

const order = ref<string[]>([]);
const disabled = ref<string[]>([]);
const loading = ref(false);
const saving = ref(false);
const dragIndex = ref<number | null>(null);

// ── ARP / MAC 來源順序 ──
const arpOrder = ref<string[]>([]);
const arpDisabled = ref<string[]>([]);
const arpSaving = ref(false);
const arpDragIndex = ref<number | null>(null);
function arpIsEnabled(s: string): boolean { return !arpDisabled.value.includes(s); }
function arpSetEnabled(s: string, on: boolean) {
  if (s === "manual") return;
  if (on) arpDisabled.value = arpDisabled.value.filter((x) => x !== s);
  else if (!arpDisabled.value.includes(s)) arpDisabled.value = [...arpDisabled.value, s];
}
function arpSrcLabel(s: string): string {
  const key = `hostnameSrc.src.${s}`;
  const out = t(key);
  return out === key ? s : out;
}
function onArpDrop(i: number) {
  if (arpDragIndex.value === null || arpDragIndex.value === i) return;
  const arr = [...arpOrder.value];
  const [moved] = arr.splice(arpDragIndex.value, 1);
  arr.splice(i, 0, moved);
  arpOrder.value = arr;
  arpDragIndex.value = null;
}
async function saveArp() {
  arpSaving.value = true;
  try {
    const r = await setArpPrecedence(arpOrder.value, arpDisabled.value);
    arpOrder.value = r.order;
    arpDisabled.value = r.disabled ?? [];
    msg.success(t("hostnameSrc.saved"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "save failed");
  } finally { arpSaving.value = false; }
}

// ── 裝置名稱來源順序 ──
const devOrder = ref<string[]>([]);
const devDisabled = ref<string[]>([]);
const devSaving = ref(false);
const devDragIndex = ref<number | null>(null);
function devIsEnabled(s: string): boolean { return !devDisabled.value.includes(s); }
function devSetEnabled(s: string, on: boolean) {
  if (s === "manual") return;
  if (on) devDisabled.value = devDisabled.value.filter((x) => x !== s);
  else if (!devDisabled.value.includes(s)) devDisabled.value = [...devDisabled.value, s];
}
function devSrcLabel(s: string): string {
  const key = `hostnameSrc.src.${s}`;
  const out = t(key);
  return out === key ? s : out;
}
function onDevDrop(i: number) {
  if (devDragIndex.value === null || devDragIndex.value === i) return;
  const arr = [...devOrder.value];
  const [moved] = arr.splice(devDragIndex.value, 1);
  arr.splice(i, 0, moved);
  devOrder.value = arr;
  devDragIndex.value = null;
}
async function saveDev() {
  devSaving.value = true;
  try {
    const r = await setDevNamePrecedence(devOrder.value, devDisabled.value);
    devOrder.value = r.order;
    devDisabled.value = r.disabled ?? [];
    msg.success(t("hostnameSrc.saved"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "save failed");
  } finally { devSaving.value = false; }
}

// ── 裝置型號來源順序 ──
const modelOrder = ref<string[]>([]);
const modelDisabled = ref<string[]>([]);
const modelSaving = ref(false);
const modelDragIndex = ref<number | null>(null);
function modelIsEnabled(s: string): boolean { return !modelDisabled.value.includes(s); }
function modelSetEnabled(s: string, on: boolean) {
  if (s === "manual") return;
  if (on) modelDisabled.value = modelDisabled.value.filter((x) => x !== s);
  else if (!modelDisabled.value.includes(s)) modelDisabled.value = [...modelDisabled.value, s];
}
function modelSrcLabel(s: string): string {
  const key = `hostnameSrc.src.${s}`;
  const out = t(key);
  return out === key ? s : out;
}
function onModelDrop(i: number) {
  if (modelDragIndex.value === null || modelDragIndex.value === i) return;
  const arr = [...modelOrder.value];
  const [moved] = arr.splice(modelDragIndex.value, 1);
  arr.splice(i, 0, moved);
  modelOrder.value = arr;
  modelDragIndex.value = null;
}
async function saveModel() {
  modelSaving.value = true;
  try {
    const r = await setModelPrecedence(modelOrder.value, modelDisabled.value);
    modelOrder.value = r.order;
    modelDisabled.value = r.disabled ?? [];
    msg.success(t("hostnameSrc.saved"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "save failed");
  } finally { modelSaving.value = false; }
}

// ── OS 來源順序（無停用清單）──
const osOrder = ref<string[]>([]);
const osSaving = ref(false);
const osDragIndex = ref<number | null>(null);
function osSrcLabel(s: string): string {
  const key = `os_precedence.src_${s}`;
  const out = t(key);
  return out === key ? s : out;
}
function onOsDrop(i: number) {
  if (osDragIndex.value === null || osDragIndex.value === i) return;
  const arr = [...osOrder.value];
  const [moved] = arr.splice(osDragIndex.value, 1);
  arr.splice(i, 0, moved);
  osOrder.value = arr;
  osDragIndex.value = null;
}
async function saveOs() {
  osSaving.value = true;
  try {
    const r = await setOsPrecedence(osOrder.value);
    osOrder.value = r.order;
    msg.success(t("hostnameSrc.saved"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "save failed");
  } finally { osSaving.value = false; }
}

function srcLabel(s: string): string {
  const key = `hostnameSrc.src.${s}`;
  const out = t(key);
  return out === key ? s : out;
}
function isEnabled(s: string): boolean { return !disabled.value.includes(s); }
function setEnabled(s: string, on: boolean) {
  if (s === "manual") return;  // 手動不可停用
  if (on) disabled.value = disabled.value.filter((x) => x !== s);
  else if (!disabled.value.includes(s)) disabled.value = [...disabled.value, s];
}

async function load() {
  loading.value = true;
  try {
    const [p, a, d, m, o] = await Promise.all([
      getHostnamePrecedence(), getArpPrecedence(), getDevNamePrecedence(), getModelPrecedence(),
      getOsPrecedence(),
    ]);
    order.value = p.order;
    disabled.value = p.disabled ?? [];
    arpOrder.value = a.order;
    arpDisabled.value = a.disabled ?? [];
    devOrder.value = d.order;
    devDisabled.value = d.disabled ?? [];
    modelOrder.value = m.order;
    modelDisabled.value = m.disabled ?? [];
    osOrder.value = o.order;
  } finally {
    loading.value = false;
  }
}

function onDragStart(i: number) { dragIndex.value = i; }
function onDragOver(e: DragEvent) { e.preventDefault(); }
function onDrop(i: number) {
  if (dragIndex.value === null || dragIndex.value === i) return;
  const arr = [...order.value];
  const [moved] = arr.splice(dragIndex.value, 1);
  arr.splice(i, 0, moved);
  order.value = arr;
  dragIndex.value = null;
}

async function save() {
  saving.value = true;
  try {
    const p = await setHostnamePrecedence(order.value, disabled.value);
    order.value = p.order;
    disabled.value = p.disabled ?? [];
    msg.success(t("hostnameSrc.saved"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "save failed");
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <n-spin :show="loading">
   <n-space vertical :size="16">
    <n-card :title="t('hostnameSrc.page_title')">
      <n-text depth="3" style="display: block; margin-bottom: 12px; font-size: 13px">
        {{ t("hostnameSrc.page_subtitle") }}
      </n-text>
      <n-text depth="3" style="font-size: 12px">{{ t("hostnameSrc.drag_hint") }}</n-text>
      <ul class="rank-list">
        <li
          v-for="(s, i) in order" :key="s"
          class="rank-item"
          :class="{ dragging: dragIndex === i, disabled: !isEnabled(s) }"
          draggable="true"
          @dragstart="onDragStart(i)"
          @dragover="onDragOver"
          @drop="onDrop(i)"
          @dragend="dragIndex = null"
        >
          <span class="rank-num">{{ i + 1 }}</span>
          <span class="rank-handle">⠿</span>
          <span class="rank-label">{{ srcLabel(s) }}</span>
          <span class="rank-key">{{ s }}</span>
          <n-switch
            :value="isEnabled(s)"
            :disabled="s === 'manual'"
            size="small"
            style="margin-left: auto"
            @update:value="(v: boolean) => setEnabled(s, v)"
          >
            <template #checked>{{ t("hostnameSrc.enabled") }}</template>
            <template #unchecked>{{ t("hostnameSrc.disabled") }}</template>
          </n-switch>
        </li>
      </ul>

      <n-button type="primary" :loading="saving" style="margin-top: 16px" @click="save">
        <template #icon><n-icon><SaveIcon /></n-icon></template>
        {{ t("hostnameSrc.save") }}
      </n-button>
    </n-card>

    <n-card :title="t('hostnameSrc.arp_title')">
        <n-text depth="3" style="display: block; font-size: 13px; margin-bottom: 8px">
          {{ t("hostnameSrc.arp_subtitle") }}
        </n-text>
        <ul class="rank-list">
          <li
            v-for="(s, i) in arpOrder" :key="s"
            class="rank-item"
            :class="{ dragging: arpDragIndex === i, disabled: !arpIsEnabled(s) }"
            draggable="true"
            @dragstart="arpDragIndex = i"
            @dragover="onDragOver"
            @drop="onArpDrop(i)"
            @dragend="arpDragIndex = null"
          >
            <span class="rank-num">{{ i + 1 }}</span>
            <span class="rank-handle">⠿</span>
            <span class="rank-label">{{ arpSrcLabel(s) }}</span>
            <span class="rank-key">{{ s }}</span>
            <n-switch
              :value="arpIsEnabled(s)"
              :disabled="s === 'manual'"
              size="small"
              style="margin-left: auto"
              @update:value="(v: boolean) => arpSetEnabled(s, v)"
            >
              <template #checked>{{ t("hostnameSrc.enabled") }}</template>
              <template #unchecked>{{ t("hostnameSrc.disabled") }}</template>
            </n-switch>
          </li>
        </ul>
        <n-button type="primary" :loading="arpSaving" style="margin-top: 16px" @click="saveArp">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("hostnameSrc.save") }}
        </n-button>
    </n-card>

    <n-card :title="t('hostnameSrc.dev_title')">
        <n-text depth="3" style="display: block; font-size: 13px; margin-bottom: 8px">
          {{ t("hostnameSrc.dev_subtitle") }}
        </n-text>
        <ul class="rank-list">
          <li
            v-for="(s, i) in devOrder" :key="s"
            class="rank-item"
            :class="{ dragging: devDragIndex === i, disabled: !devIsEnabled(s) }"
            draggable="true"
            @dragstart="devDragIndex = i"
            @dragover="onDragOver"
            @drop="onDevDrop(i)"
            @dragend="devDragIndex = null"
          >
            <span class="rank-num">{{ i + 1 }}</span>
            <span class="rank-handle">⠿</span>
            <span class="rank-label">{{ devSrcLabel(s) }}</span>
            <span class="rank-key">{{ s }}</span>
            <n-switch
              :value="devIsEnabled(s)"
              :disabled="s === 'manual'"
              size="small"
              style="margin-left: auto"
              @update:value="(v: boolean) => devSetEnabled(s, v)"
            >
              <template #checked>{{ t("hostnameSrc.enabled") }}</template>
              <template #unchecked>{{ t("hostnameSrc.disabled") }}</template>
            </n-switch>
          </li>
        </ul>
        <n-button type="primary" :loading="devSaving" style="margin-top: 16px" @click="saveDev">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("hostnameSrc.save") }}
        </n-button>
    </n-card>

    <n-card :title="t('hostnameSrc.model_title')">
        <n-text depth="3" style="display: block; font-size: 13px; margin-bottom: 8px">
          {{ t("hostnameSrc.model_subtitle") }}
        </n-text>
        <ul class="rank-list">
          <li
            v-for="(s, i) in modelOrder" :key="s"
            class="rank-item"
            :class="{ dragging: modelDragIndex === i, disabled: !modelIsEnabled(s) }"
            draggable="true"
            @dragstart="modelDragIndex = i"
            @dragover="onDragOver"
            @drop="onModelDrop(i)"
            @dragend="modelDragIndex = null"
          >
            <span class="rank-num">{{ i + 1 }}</span>
            <span class="rank-handle">⠿</span>
            <span class="rank-label">{{ modelSrcLabel(s) }}</span>
            <span class="rank-key">{{ s }}</span>
            <n-switch
              :value="modelIsEnabled(s)"
              :disabled="s === 'manual'"
              size="small"
              style="margin-left: auto"
              @update:value="(v: boolean) => modelSetEnabled(s, v)"
            >
              <template #checked>{{ t("hostnameSrc.enabled") }}</template>
              <template #unchecked>{{ t("hostnameSrc.disabled") }}</template>
            </n-switch>
          </li>
        </ul>
        <n-button type="primary" :loading="modelSaving" style="margin-top: 16px" @click="saveModel">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("hostnameSrc.save") }}
        </n-button>
    </n-card>

    <n-card :title="t('os_precedence.title')">
        <n-text depth="3" style="display: block; font-size: 13px; margin-bottom: 8px">
          {{ t("os_precedence.hint") }}
        </n-text>
        <ul class="rank-list">
          <li
            v-for="(s, i) in osOrder" :key="s"
            class="rank-item"
            :class="{ dragging: osDragIndex === i }"
            draggable="true"
            @dragstart="osDragIndex = i"
            @dragover="onDragOver"
            @drop="onOsDrop(i)"
            @dragend="osDragIndex = null"
          >
            <span class="rank-num">{{ i + 1 }}</span>
            <span class="rank-handle">⠿</span>
            <span class="rank-label">{{ osSrcLabel(s) }}</span>
            <span class="rank-key">{{ s }}</span>
          </li>
        </ul>
        <n-button type="primary" :loading="osSaving" style="margin-top: 16px" @click="saveOs">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("hostnameSrc.save") }}
        </n-button>
    </n-card>
   </n-space>
  </n-spin>
</template>

<style scoped>
.arp-section {
  margin-top: 28px;
  padding-top: 20px;
  border-top: 1px solid var(--n-border-color, rgba(127,127,127,.2));
}
.rank-list {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
  max-width: 460px;
}
.rank-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  margin: 6px 0;
  border: 1px solid var(--n-border-color, rgba(127,127,127,.2));
  border-radius: 8px;
  background: var(--n-card-color, rgba(127,127,127,.04));
  cursor: grab;
  user-select: none;
}
.rank-item.dragging { opacity: .5; }
.rank-item.disabled { opacity: .45; }
.rank-item.disabled .rank-num { background: var(--n-text-color-3, #888); }
.rank-item:active { cursor: grabbing; }
.rank-num {
  width: 22px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 50%;
  background: var(--primary-color, #18a058);
  color: #fff; font-size: 12px; font-weight: 600;
  flex: 0 0 auto;
}
.rank-handle { opacity: .5; font-size: 16px; }
.rank-label { font-weight: 500; }
.rank-key { opacity: .5; font-size: 12px; font-family: monospace; }
.rank-item .rank-label { margin-right: 4px; }
</style>
