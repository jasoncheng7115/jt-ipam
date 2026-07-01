<script setup lang="ts">
/**
 * VNC 畫面（原生 canvas）。先換 ticket → 開 WebSocket → 橋接後端 aardwolf VNCConnection。
 * 密碼只在連線時送出，前端不保存（或選已存密碼以 reference 連線）。
 * VNC 桌面尺寸由伺服器決定 → canvas 於收到 connected 狀態時依回傳尺寸設定。
 */
import { nextTick, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NForm, NFormItem, NInput, NInputNumber, NButton, NButtonGroup, NSpace, NAlert, NIcon, NSpin, NTag,
  NSelect, NSwitch, NPopconfirm, NCard, NDropdown, useMessage,
} from "naive-ui";
import {
  requestVncTicket, buildVncWsUrl,
  listVncCredentials, createVncCredential, deleteVncCredential, type VncCredential,
} from "@/api/vnc";
import { buildSendKeysMenu, makeSendCombo } from "@/composables/useSendKeys";
import { VncIcon, CancelIcon, RefreshIcon, DeleteIcon, ChevronDownIcon, KeyIcon, ExpandIcon, ReduceIcon } from "@/icons";
import ConsoleDisconnectedOverlay from "@/components/ConsoleDisconnectedOverlay.vue";

const props = withDefaults(defineProps<{
  addressId: string;
  ip: string;
  hostname?: string | null;
  deviceName?: string | null;
  fullHeight?: boolean;
}>(), { fullHeight: false, hostname: null, deviceName: null });

const { t } = useI18n();
const msg = useMessage();

const savedCreds = ref<VncCredential[]>([]);
const selectedCredId = ref<string | null>(null);
const remember = ref(false);
const rememberLabel = ref("");
const credOptions = ref<{ label: string; value: string }[]>([]);

async function loadCreds() {
  try {
    savedCreds.value = await listVncCredentials(props.addressId);
    credOptions.value = savedCreds.value.map((c) => ({ label: c.label, value: c.id }));
    if (!selectedCredId.value && savedCreds.value.length) {
      selectedCredId.value = savedCreds.value[0].id;
    }
  } catch { /* 靜默 */ }
}
async function delSelectedCred() {
  if (!selectedCredId.value) return;
  try {
    await deleteVncCredential(selectedCredId.value);
    selectedCredId.value = null;
    await loadCreds();
    msg.success(t("common.ok"));
  } catch { msg.error(t("errors.server")); }
}
onMounted(loadCreds);

type Phase = "form" | "connecting" | "connected" | "closed" | "error";
const phase = ref<Phase>("form");
const errorMsg = ref("");

const form = reactive({ password: "", port: 5900 });

const canvasEl = ref<HTMLCanvasElement | null>(null);
const canvasBoxEl = ref<HTMLElement | null>(null);
let ctx: CanvasRenderingContext2D | null = null;
let ws: WebSocket | null = null;
let pingTimer: ReturnType<typeof setInterval> | null = null;
let watchdogTimer: ReturnType<typeof setInterval> | null = null;
let lastRecv = 0;
let lastMove = 0;
const PING_MS = 20_000;
const DEAD_MS = 45_000;
const MOVE_THROTTLE = 30;

// 畫面縮放：fit=自動縮放符合視窗（CSS 縮放、不出捲軸）、native=原始解析度（1:1，超出可捲）
const scaleMode = ref<"fit" | "native">("fit");
let srvW = 0, srvH = 0;     // 伺服器 framebuffer 尺寸
let ro: ResizeObserver | null = null;

function applyScale() {
  const c = canvasEl.value, box = canvasBoxEl.value;
  if (!c || !box || !srvW || !srvH) return;
  if (scaleMode.value === "native") {
    c.style.width = ""; c.style.height = "";   // 用 intrinsic 尺寸（1:1）
    return;
  }
  const s = Math.min(box.clientWidth / srvW, box.clientHeight / srvH);
  c.style.width = Math.max(1, Math.round(srvW * s)) + "px";
  c.style.height = Math.max(1, Math.round(srvH * s)) + "px";
}
function setScale(m: "fit" | "native") {
  scaleMode.value = m;
  nextTick(applyScale);
}
// 滑鼠座標換算：canvas 被 CSS 縮放時，offsetX/Y 是顯示像素，要換回 framebuffer 像素
function mapXY(e: MouseEvent): { x: number; y: number } {
  const c = canvasEl.value;
  if (!c || !c.clientWidth || !c.clientHeight) return { x: e.offsetX, y: e.offsetY };
  return {
    x: Math.round(e.offsetX * (c.width / c.clientWidth)),
    y: Math.round(e.offsetY * (c.height / c.clientHeight)),
  };
}

function wsSend(obj: Record<string, unknown>) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

// 送出特殊按鍵（VNC 目標可能是 Win/Mac/Linux → 含 macOS 組合）
const sendKeysMenu = buildSendKeysMenu(true);
const _sendCombo = makeSendCombo(wsSend);
function onSendKey(key: string) { _sendCombo(key); canvasEl.value?.focus(); }
function stopHeartbeat() {
  if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
  if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
}
function startHeartbeat() {
  stopHeartbeat();
  lastRecv = Date.now();
  pingTimer = setInterval(() => wsSend({ type: "ping" }), PING_MS);
  watchdogTimer = setInterval(() => {
    if (phase.value === "connected" && Date.now() - lastRecv > DEAD_MS) {
      teardown();
      phase.value = "closed";
    }
  }, 5_000);
}
function teardown() {
  stopHeartbeat();
  try { ws?.close(); } catch { /* noop */ }
  ws = null;
  ro?.disconnect(); ro = null;
}

const BMAP: Record<number, number> = { 0: 0, 2: 1, 1: 2 };
function onMouseMove(e: MouseEvent) {
  const now = Date.now();
  if (now - lastMove < MOVE_THROTTLE) return;
  lastMove = now;
  wsSend({ type: "m", move: true, ...mapXY(e) });
}
function onMouseDown(e: MouseEvent) {
  e.preventDefault();
  canvasEl.value?.focus();
  wsSend({ type: "m", b: BMAP[e.button] ?? 0, p: true, ...mapXY(e) });
}
function onMouseUp(e: MouseEvent) {
  e.preventDefault();
  wsSend({ type: "m", b: BMAP[e.button] ?? 0, p: false, ...mapXY(e) });
}
function onWheel(e: WheelEvent) {
  e.preventDefault();
  wsSend({ type: "m", wheel: true, dir: e.deltaY < 0 ? 1 : -1, ...mapXY(e) });
}
function onKey(e: KeyboardEvent, pressed: boolean) {
  e.preventDefault();
  wsSend({ type: "k", key: e.key, ch: e.key && e.key.length === 1 ? e.key : "", p: pressed });
}

async function connect() {
  errorMsg.value = "";
  phase.value = "connecting";

  let credId: string | null = selectedCredId.value;
  if (!credId && remember.value) {
    try {
      const saved = await createVncCredential({
        label: rememberLabel.value.trim() || `vnc@${props.ip}`,
        target_ip_id: props.addressId,
        password: form.password,
      });
      credId = saved.id;
    } catch (e: any) {
      phase.value = "error";
      errorMsg.value = e?.response?.data?.detail || t("vnc.err_save_cred");
      return;
    }
  }

  let ticket;
  try {
    ticket = await requestVncTicket(props.addressId);
  } catch (e: any) {
    phase.value = "error";
    errorMsg.value = e?.response?.data?.detail || t("vnc.err_ticket");
    return;
  }

  await nextTick();
  if (!canvasEl.value) { phase.value = "error"; errorMsg.value = t("vnc.err_ticket"); return; }
  ctx = canvasEl.value.getContext("2d");

  ws = new WebSocket(buildVncWsUrl(ticket.ws_path, ticket.ticket));
  ws.onopen = () => {
    if (credId) {
      wsSend({ type: "config", credential_id: credId, port: form.port });
    } else {
      wsSend({ type: "config", password: form.password, port: form.port });
    }
    form.password = "";
    startHeartbeat();
  };
  ws.onmessage = (ev) => {
    lastRecv = Date.now();
    let payload: any;
    try { payload = JSON.parse(ev.data); } catch { return; }
    switch (payload.type) {
      case "pong": break;
      case "img": {
        const im = new Image();
        im.onload = () => ctx?.drawImage(im, payload.x, payload.y);
        im.src = "data:image/png;base64," + payload.d;
        break;
      }
      case "status":
        if (payload.state === "connected") {
          phase.value = "connected";
          // VNC 桌面尺寸由伺服器決定 → 依回傳尺寸設定 canvas，並套用縮放
          if (canvasEl.value && payload.width && payload.height) {
            srvW = payload.width; srvH = payload.height;
            canvasEl.value.width = srvW;
            canvasEl.value.height = srvH;
            ctx = canvasEl.value.getContext("2d");
            nextTick(() => {
              applyScale();
              if (!ro && canvasBoxEl.value) { ro = new ResizeObserver(() => applyScale()); ro.observe(canvasBoxEl.value); }
            });
          }
          nextTick(() => canvasEl.value?.focus());
        } else if (payload.state === "disconnected") phase.value = "closed";
        break;
      case "error":
        phase.value = "error";
        errorMsg.value = payload.message || payload.code || t("vnc.err_generic");
        break;
    }
  };
  ws.onclose = () => { if (phase.value !== "error") phase.value = "closed"; };
  ws.onerror = () => {
    if (phase.value !== "error" && phase.value !== "connected") {
      phase.value = "error";
      errorMsg.value = t("vnc.err_ws");
    }
  };
}

function disconnect() {
  wsSend({ type: "close" });
  teardown();
  phase.value = "closed";
}
function backToForm() {
  teardown();
  phase.value = "form";
}

onBeforeUnmount(teardown);
</script>

<template>
  <div class="vnc-wrap" :class="{ 'vnc-full': fullHeight, 'vnc-center': fullHeight && phase === 'form' }">
    <!-- 連線設定表單 -->
    <div v-if="phase === 'form'" class="vnc-form">
      <n-card size="small" :bordered="true">
        <template #header>
          <span style="display:flex;align-items:center;gap:8px">
            <n-icon :component="VncIcon" :size="18" />
            <span>{{ t("vnc.connect_to", { ip }) }}</span>
            <n-tag size="small" type="warning" :bordered="false" round>{{ t("vnc.beta") }}</n-tag>
          </span>
        </template>
        <n-alert :show-icon="true" type="warning" :bordered="false" style="margin-bottom:12px">
          {{ t("vnc.beta_hint") }}
        </n-alert>
        <!-- 已存密碼 -->
        <div v-if="credOptions.length" class="vnc-saved-row">
          <span class="vnc-saved-label">{{ t("vnc.saved_cred") }}</span>
          <n-select v-model:value="selectedCredId" :options="credOptions" clearable size="small"
                    :placeholder="t('vnc.saved_cred_ph')" style="flex:1" />
          <n-popconfirm v-if="selectedCredId" @positive-click="delSelectedCred">
            <template #trigger>
              <n-button quaternary type="error" size="small">
                <template #icon><n-icon :component="DeleteIcon" /></template>
              </n-button>
            </template>
            {{ t("vnc.saved_cred_del_confirm") }}
          </n-popconfirm>
        </div>

        <n-form label-placement="left" :label-width="92" size="small">
          <n-form-item v-if="!selectedCredId" :label="t('vnc.password')">
            <n-space vertical :size="2" style="width:100%">
              <n-input v-model:value="form.password" type="password" show-password-on="click"
                       :placeholder="t('vnc.password_ph')" @keyup.enter="connect" />
              <span style="font-size:11px;opacity:.7">{{ t("vnc.auth_note") }}</span>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('vnc.port')">
            <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width:140px" />
          </n-form-item>

          <n-form-item v-if="!selectedCredId" :label="t('vnc.remember')">
            <n-space vertical :size="4" style="width:100%">
              <n-switch v-model:value="remember" />
              <n-input v-if="remember" v-model:value="rememberLabel" size="small"
                       :placeholder="t('vnc.remember_label_ph')" />
            </n-space>
          </n-form-item>

          <n-alert :show-icon="false" type="info" style="margin-bottom:10px">
            {{ selectedCredId ? t("vnc.use_saved_hint") : (remember ? t("vnc.store_hint") : t("vnc.no_store_hint")) }}
          </n-alert>
          <n-space justify="end">
            <n-button type="primary" @click="connect">
              <template #icon><n-icon :component="VncIcon" /></template>
              {{ t("vnc.connect") }}
            </n-button>
          </n-space>
        </n-form>
      </n-card>
    </div>

    <!-- 畫面 -->
    <div v-show="phase !== 'form'" class="vnc-screen-area" :class="{ 'vnc-full': fullHeight }">
      <div class="vnc-toolbar">
        <span class="vnc-status" :data-state="phase">
          <n-spin v-if="phase === 'connecting'" :size="12" />
          <span v-else class="vnc-dot" />
          <span>{{ t(`vnc.state_${phase}`) }}</span>
          <span class="vnc-ip">{{ ip }}</span>
          <n-tag v-if="hostname" size="small" :bordered="false" round>{{ hostname }}</n-tag>
          <span class="conn-proto conn-proto--vnc">VNC</span>
          <n-tag v-if="deviceName" size="small" type="info" :bordered="false" round>{{ deviceName }}</n-tag>
          <n-tag size="small" type="warning" :bordered="false" round>{{ t("vnc.beta") }}</n-tag>
        </span>
        <n-space :size="8" align="center">
          <!-- 送出特殊按鍵 -->
          <n-dropdown v-if="phase === 'connected'" trigger="click" :options="sendKeysMenu"
                      size="small" @select="onSendKey">
            <n-button size="tiny">
              <template #icon><n-icon :component="KeyIcon" /></template>
              {{ t("vnc.send_keys") }}<n-icon :component="ChevronDownIcon" style="margin-left:2px" />
            </n-button>
          </n-dropdown>
          <!-- 畫面縮放：自動縮放（符合視窗、不出捲軸） / 原始解析度（1:1） -->
          <n-button-group v-if="phase === 'connected'" size="tiny">
            <n-button :type="scaleMode === 'fit' ? 'primary' : 'default'" @click="setScale('fit')">
              <template #icon><n-icon :component="ExpandIcon" /></template>{{ t("vnc.scale_fit") }}
            </n-button>
            <n-button :type="scaleMode === 'native' ? 'primary' : 'default'" @click="setScale('native')">
              <template #icon><n-icon :component="ReduceIcon" /></template>{{ t("vnc.scale_native") }}
            </n-button>
          </n-button-group>
          <n-button v-if="phase === 'connected'" size="tiny" type="error" ghost @click="disconnect">
            <template #icon><n-icon :component="CancelIcon" /></template>{{ t("vnc.disconnect") }}
          </n-button>
          <n-button v-if="phase === 'closed' || phase === 'error'" size="tiny" @click="backToForm">
            <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("vnc.reconnect") }}
          </n-button>
        </n-space>
      </div>
      <n-alert v-if="phase === 'error'" type="error" :show-icon="true" style="margin:8px 0">
        {{ errorMsg }}
      </n-alert>
      <div class="vnc-disp" :class="{ 'vnc-full': fullHeight }">
      <div ref="canvasBoxEl" class="vnc-canvas-box"
           :class="{ 'vnc-full': fullHeight, 'vnc-fit': scaleMode === 'fit', 'vnc-native': scaleMode !== 'fit', 'term-dim': phase === 'closed' }">
        <canvas ref="canvasEl" class="vnc-canvas" tabindex="0"
                @mousemove="onMouseMove" @mousedown="onMouseDown" @mouseup="onMouseUp"
                @wheel.prevent="onWheel" @contextmenu.prevent
                @keydown="onKey($event, true)" @keyup="onKey($event, false)" />
      </div>
      <ConsoleDisconnectedOverlay :show="phase === 'closed' || phase === 'error'" :error="phase === 'error'" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.vnc-wrap { width: 100%; }
.vnc-wrap.vnc-full { height: 100%; display: flex; flex-direction: column; }
.vnc-wrap.vnc-center { justify-content: center; align-items: center; }
.vnc-wrap.vnc-center .vnc-form { width: 520px; max-width: 92vw; }
.vnc-form { max-width: 520px; }
.vnc-screen-area { display: flex; flex-direction: column; }
.vnc-disp { position: relative; }
.vnc-disp.vnc-full { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.vnc-screen-area.vnc-full { flex: 1; min-height: 0; }
.vnc-toolbar { display: flex; justify-content: space-between; align-items: center; padding: 4px 2px; gap: 8px; }
.vnc-status { font-size: 13px; display: inline-flex; align-items: center; gap: 7px;
  padding: 3px 11px; border-radius: 999px; font-weight: 500;
  background: rgba(128, 128, 128, .12); color: #888; }
.vnc-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.vnc-ip { opacity: .7; font-variant-numeric: tabular-nums; }
.vnc-status[data-state="connected"] { color: #18a058; background: rgba(24, 160, 88, .14); }
.vnc-status[data-state="connected"] .vnc-dot { animation: vnc-pulse 1.8s infinite; }
.vnc-status[data-state="connecting"] { color: #d99812; background: rgba(217, 152, 18, .14); }
.vnc-status[data-state="error"] { color: #d03050; background: rgba(208, 48, 80, .14); }
.vnc-status[data-state="closed"] { color: #888; background: rgba(128, 128, 128, .14); }
@keyframes vnc-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(24, 160, 88, .5); }
  70%  { box-shadow: 0 0 0 6px rgba(24, 160, 88, 0); }
  100% { box-shadow: 0 0 0 0 rgba(24, 160, 88, 0); }
}
.vnc-canvas-box { background: #000; padding: 0; border-radius: 10px; border: 1px solid #2b2b30;
  box-shadow: 0 10px 30px rgba(0,0,0,.30), 0 3px 10px rgba(0,0,0,.20); overflow: auto; display: inline-block; max-width: 100%; }
/* 協定標籤（主機名稱右邊）：VNC */
.conn-proto { font-weight: 700; font-size: 11px; letter-spacing: .4px; line-height: 1;
  padding: 2px 7px; border-radius: 999px; }
.conn-proto--vnc { color: #8a63d2; background: rgba(138,99,210,.16); }
.vnc-canvas-box.vnc-full { flex: 1; min-height: 0; display: block; }
.vnc-canvas { display: block; outline: none; background: #000; }
/* 自動縮放：置中、不出捲軸（canvas 由 JS 設 CSS 尺寸符合容器）。原始解析度：1:1、超出可捲。 */
.vnc-canvas-box.vnc-fit { overflow: hidden; display: flex; align-items: center; justify-content: center; }
.vnc-canvas-box.vnc-native { overflow: auto; }
:deep(.n-card > .n-card-header) { display: flex; align-items: center; padding-top: 12px; padding-bottom: 12px; }
.vnc-saved-row { display: flex; align-items: center; margin-bottom: 18px; }
.vnc-saved-label { width: 92px; flex: none; box-sizing: border-box; text-align: right;
  padding-right: 12px; font-size: 14px; }
.vnc-saved-row :deep(.n-button) { margin-left: 6px; }
/* 已斷線：整個畫面反灰並停用互動，讓使用者一眼看出已中斷 */
.term-dim { filter: grayscale(1) brightness(.45); pointer-events: none; transition: filter .25s; }
</style>
