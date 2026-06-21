<script setup lang="ts">
/**
 * SSH 終端機（xterm.js）。先換 ticket → 開 WebSocket → 橋接後端 asyncssh。
 * 憑證（密碼/私鑰）只在連線時送出，前端不保存。Host key 採 TOFU：首次顯示指紋確認後由後端釘選。
 */
import { nextTick, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NForm, NFormItem, NInput, NInputNumber, NRadioGroup, NRadio, NButton,
  NSpace, NModal, NAlert, NIcon, NSpin, NTag, NButtonGroup, NSelect, NSwitch,
  NPopconfirm, NCard, useMessage,
} from "naive-ui";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import {
  requestSshTicket, buildSshWsUrl,
  listSshCredentials, createSshCredential, deleteSshCredential, type SshCredential,
} from "@/api/ssh";
import { TerminalIcon, CancelIcon, RefreshIcon, DeleteIcon } from "@/icons";

const props = withDefaults(defineProps<{
  addressId: string;
  ip: string;
  hostname?: string | null;
  deviceName?: string | null;
  defaultPort?: number;
  fullHeight?: boolean;
}>(), { defaultPort: 22, fullHeight: false, hostname: null, deviceName: null });

const FONT_MIN = 9;
const FONT_MAX = 24;
const fontSize = ref(13);

const { t } = useI18n();
const msg = useMessage();

// 已存帳密（by-user）：選一筆 → 以 reference 連線；或勾「記住」存新的
const savedCreds = ref<SshCredential[]>([]);
const selectedCredId = ref<string | null>(null);
const remember = ref(false);
const rememberLabel = ref("");
const credOptions = ref<{ label: string; value: string }[]>([]);

async function loadCreds() {
  try {
    savedCreds.value = await listSshCredentials(props.addressId);
    credOptions.value = savedCreds.value.map((c) => ({
      label: `${c.label}（${c.username}・${c.auth_type === "key" ? t("ssh.auth_key") : t("ssh.auth_password")}）`,
      value: c.id,
    }));
    // 有已存帳密就預設選最近一筆 → 不必再輸入，直接按連線（要改用其他帳密可清空下拉）
    if (!selectedCredId.value && savedCreds.value.length) {
      selectedCredId.value = savedCreds.value[0].id;
    }
  } catch { /* 靜默：沒有已存帳密不影響手動連線 */ }
}
async function delSelectedCred() {
  if (!selectedCredId.value) return;
  try {
    await deleteSshCredential(selectedCredId.value);
    selectedCredId.value = null;
    await loadCreds();
    msg.success(t("common.ok"));
  } catch { msg.error(t("errors.server")); }
}
onMounted(loadCreds);

type Phase = "form" | "connecting" | "connected" | "closed" | "error";
const phase = ref<Phase>("form");
const errorMsg = ref("");

const form = reactive({
  username: "",
  port: props.defaultPort,
  auth: "password" as "password" | "key",
  password: "",
  privateKey: "",
  passphrase: "",
});

const termEl = ref<HTMLElement | null>(null);
let term: Terminal | null = null;
let fit: FitAddon | null = null;
let ws: WebSocket | null = null;
let ro: ResizeObserver | null = null;
// heartbeat：每 20s 送 ping；45s 內沒收到任何訊息（含 pong）→ 判定斷線
let pingTimer: ReturnType<typeof setInterval> | null = null;
let watchdogTimer: ReturnType<typeof setInterval> | null = null;
let lastRecv = 0;
const PING_MS = 20_000;
const DEAD_MS = 45_000;

// host key TOFU 確認
const hostKeyAsk = ref(false);
const hostKeyFp = ref("");

function clearCreds() {
  form.password = "";
  form.privateKey = "";
  form.passphrase = "";
}

function wsSend(obj: Record<string, unknown>) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function disposeTerm() {
  ro?.disconnect(); ro = null;
  term?.dispose(); term = null; fit = null;
}

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
      // 45s 沒收到任何訊息（含 pong）→ 視為斷線（靜默斷線：拔線/睡眠/對端斷電）
      term?.write(`\r\n\x1b[33m${t("ssh.disconnected")}\x1b[0m\r\n`);
      teardown();
      phase.value = "closed";
    }
  }, 5_000);
}

function teardown() {
  stopHeartbeat();
  try { ws?.close(); } catch { /* noop */ }
  ws = null;
  disposeTerm();
}

function doFit() {
  try {
    fit?.fit();
    if (term) wsSend({ type: "resize", cols: term.cols, rows: term.rows });
  } catch { /* noop */ }
}

// 文字大小快速調整
function setFont(delta: number) {
  fontSize.value = Math.min(FONT_MAX, Math.max(FONT_MIN, fontSize.value + delta));
  if (term) { term.options.fontSize = fontSize.value; doFit(); }
}

async function connect() {
  errorMsg.value = "";
  if (!selectedCredId.value && !form.username.trim()) { errorMsg.value = t("ssh.err_username"); return; }
  phase.value = "connecting";

  // 決定憑證 reference：用已選的，或勾「記住」→ 先存後以 reference 連線
  let credId: string | null = selectedCredId.value;
  if (!credId && remember.value) {
    try {
      const saved = await createSshCredential({
        label: rememberLabel.value.trim() || `${form.username.trim()}@${props.ip}`,
        username: form.username.trim(),
        auth_type: form.auth,
        target_ip_id: props.addressId,
        password: form.auth === "password" ? form.password : undefined,
        private_key: form.auth === "key" ? form.privateKey : undefined,
        passphrase: form.auth === "key" ? form.passphrase : undefined,
      });
      credId = saved.id;
    } catch (e: any) {
      phase.value = "error";
      errorMsg.value = e?.response?.data?.detail || t("ssh.err_save_cred");
      return;
    }
  }

  let ticket;
  try {
    ticket = await requestSshTicket(props.addressId);
  } catch (e: any) {
    phase.value = "error";
    errorMsg.value = e?.response?.data?.detail || t("ssh.err_ticket");
    return;
  }

  await nextTick();
  if (!termEl.value) { phase.value = "error"; errorMsg.value = t("ssh.err_ticket"); return; }
  term = new Terminal({ cursorBlink: true, fontSize: fontSize.value, scrollback: 5000,
    theme: { background: "#1e1e1e" } });
  fit = new FitAddon();
  term.loadAddon(fit);
  term.open(termEl.value);
  doFit();
  ro = new ResizeObserver(() => doFit());
  ro.observe(termEl.value);
  term.onData((d) => wsSend({ type: "data", data: d }));

  ws = new WebSocket(buildSshWsUrl(ticket.ws_path, ticket.ticket));
  ws.onopen = () => {
    if (credId) {
      // 以已存憑證連線：前端只送 reference，明文不經過 WS
      wsSend({ type: "config", credential_id: credId, port: form.port,
               cols: term?.cols ?? 80, rows: term?.rows ?? 24 });
    } else {
      wsSend({
        type: "config",
        username: form.username.trim(),
        port: form.port,
        auth: form.auth,
        password: form.auth === "password" ? form.password : undefined,
        private_key: form.auth === "key" ? form.privateKey : undefined,
        passphrase: form.auth === "key" ? form.passphrase : undefined,
        cols: term?.cols ?? 80,
        rows: term?.rows ?? 24,
      });
    }
    clearCreds(); // 送出後即清空，前端不留
    startHeartbeat();
  };
  ws.onmessage = (ev) => {
    lastRecv = Date.now();   // 收到任何訊息（含 pong）即視為仍存活
    let payload: any;
    try { payload = JSON.parse(ev.data); } catch { return; }
    switch (payload.type) {
      case "pong": break;   // heartbeat 回應，僅更新 lastRecv（上方已做）
      case "data": term?.write(payload.data); break;
      case "status":
        if (payload.state === "connected") phase.value = "connected";
        else if (payload.state === "disconnected") phase.value = "closed";
        break;
      case "hostkey":
        hostKeyFp.value = payload.fingerprint;
        hostKeyAsk.value = true;
        break;
      case "error":
        phase.value = "error";
        errorMsg.value = payload.message || payload.code || t("ssh.err_generic");
        break;
    }
  };
  ws.onclose = () => {
    if (phase.value !== "error") {
      phase.value = "closed";
      term?.write(`\r\n\x1b[33m${t("ssh.disconnected")}\x1b[0m\r\n`);
    }
  };
  ws.onerror = () => {
    if (phase.value !== "error" && phase.value !== "connected") {
      phase.value = "error";
      errorMsg.value = t("ssh.err_ws");
    }
  };
}

function acceptHostKey() {
  hostKeyAsk.value = false;
  wsSend({ type: "hostkey_accept" });
}
function rejectHostKey() {
  hostKeyAsk.value = false;
  wsSend({ type: "hostkey_reject" });
  teardown();
  phase.value = "form";
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
  <div class="ssh-wrap" :class="{ 'ssh-full': fullHeight, 'ssh-center': fullHeight && phase === 'form' }">
    <!-- 連線設定表單 -->
    <div v-if="phase === 'form'" class="ssh-form">
      <n-card size="small" :bordered="true">
        <template #header>
          <span style="display:flex;align-items:center;gap:8px">
            <n-icon :component="TerminalIcon" :size="18" />
            <span>{{ t("ssh.connect_to", { ip }) }}</span>
          </span>
        </template>
      <!-- 已存帳密（個人保管）：選一筆即以 reference 連線。獨立 flex 列確保 label 與下拉垂直置中 -->
      <div v-if="credOptions.length" class="ssh-saved-row">
        <span class="ssh-saved-label">{{ t("ssh.saved_cred") }}</span>
        <n-select v-model:value="selectedCredId" :options="credOptions" clearable size="small"
                  :placeholder="t('ssh.saved_cred_ph')" style="flex:1" />
        <n-popconfirm v-if="selectedCredId" @positive-click="delSelectedCred">
          <template #trigger>
            <n-button quaternary type="error" size="small">
              <template #icon><n-icon :component="DeleteIcon" /></template>
            </n-button>
          </template>
          {{ t("ssh.saved_cred_del_confirm") }}
        </n-popconfirm>
      </div>

      <n-form label-placement="left" :label-width="92" size="small">
        <!-- 手動輸入（未選已存帳密時才顯示）-->
        <template v-if="!selectedCredId">
          <n-form-item :label="t('ssh.auth_method')">
            <n-radio-group v-model:value="form.auth">
              <n-radio value="password">{{ t("ssh.auth_password") }}</n-radio>
              <n-radio value="key">{{ t("ssh.auth_key") }}</n-radio>
            </n-radio-group>
          </n-form-item>
          <n-form-item :label="t('ssh.username')">
            <n-input v-model:value="form.username" placeholder="root" autofocus
                     @keyup.enter="connect" />
          </n-form-item>
          <n-form-item v-if="form.auth === 'password'" :label="t('ssh.password')">
            <n-input v-model:value="form.password" type="password" show-password-on="click"
                     @keyup.enter="connect" />
          </n-form-item>
          <template v-else>
            <n-form-item :label="t('ssh.private_key')">
              <n-input v-model:value="form.privateKey" type="textarea"
                       :autosize="{ minRows: 4, maxRows: 8 }"
                       placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
            </n-form-item>
            <n-form-item :label="t('ssh.passphrase')">
              <n-input v-model:value="form.passphrase" type="password" show-password-on="click" />
            </n-form-item>
          </template>
        </template>

        <n-form-item :label="t('ssh.port')">
          <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width:140px" />
        </n-form-item>

        <!-- 記住此帳密（僅手動模式）-->
        <n-form-item v-if="!selectedCredId" :label="t('ssh.remember')">
          <n-space vertical :size="4" style="width:100%">
            <n-switch v-model:value="remember" />
            <n-input v-if="remember" v-model:value="rememberLabel" size="small"
                     :placeholder="t('ssh.remember_label_ph')" />
          </n-space>
        </n-form-item>

        <n-alert :show-icon="false" type="info" style="margin-bottom:10px">
          {{ selectedCredId ? t("ssh.use_saved_hint") : (remember ? t("ssh.store_hint") : t("ssh.no_store_hint")) }}
        </n-alert>
        <n-space justify="end">
          <n-button type="primary" @click="connect">
            <template #icon><n-icon :component="TerminalIcon" /></template>
            {{ t("ssh.connect") }}
          </n-button>
        </n-space>
      </n-form>
      </n-card>
    </div>

    <!-- 終端機 -->
    <div v-show="phase !== 'form'" class="ssh-term-area" :class="{ 'ssh-full': fullHeight }">
      <div class="ssh-toolbar">
        <span class="ssh-status" :data-state="phase">
          <n-spin v-if="phase === 'connecting'" :size="12" />
          <span v-else class="ssh-dot" />
          <span class="ssh-state-label">{{ t(`ssh.state_${phase}`) }}</span>
          <span class="ssh-ip">{{ ip }}</span>
          <n-tag v-if="hostname" size="small" :bordered="false" round>{{ hostname }}</n-tag>
          <n-tag v-if="deviceName" size="small" type="info" :bordered="false" round>{{ deviceName }}</n-tag>
        </span>
        <n-space :size="8" align="center">
          <!-- 文字大小快速調整 -->
          <n-button-group v-if="phase === 'connected'" size="tiny">
            <n-button :disabled="fontSize <= FONT_MIN" :title="t('ssh.font_smaller')" @click="setFont(-1)">A−</n-button>
            <n-button :disabled="fontSize >= FONT_MAX" :title="t('ssh.font_larger')" @click="setFont(1)">A+</n-button>
          </n-button-group>
          <n-button v-if="phase === 'connected'" size="tiny" type="error" ghost @click="disconnect">
            <template #icon><n-icon :component="CancelIcon" /></template>{{ t("ssh.disconnect") }}
          </n-button>
          <n-button v-if="phase === 'closed' || phase === 'error'" size="tiny" @click="backToForm">
            <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("ssh.reconnect") }}
          </n-button>
        </n-space>
      </div>
      <n-alert v-if="phase === 'error'" type="error" :show-icon="true" style="margin:8px 0">
        {{ errorMsg }}
      </n-alert>
      <div ref="termEl" class="ssh-term" :class="{ 'ssh-full': fullHeight }" />
    </div>

    <!-- host key TOFU 確認 -->
    <n-modal v-model:show="hostKeyAsk" preset="dialog" :title="t('ssh.hostkey_title')"
             :positive-text="t('ssh.hostkey_trust')" :negative-text="t('common.cancel')"
             @positive-click="acceptHostKey" @negative-click="rejectHostKey"
             :mask-closable="false">
      <div style="line-height:1.6">
        <div>{{ t("ssh.hostkey_body", { ip }) }}</div>
        <code class="ssh-fp">{{ hostKeyFp }}</code>
        <div style="opacity:.7;font-size:12px">{{ t("ssh.hostkey_hint") }}</div>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.ssh-wrap { width: 100%; }
.ssh-wrap.ssh-full { height: 100%; display: flex; flex-direction: column; }
/* 全頁模式且尚未連線：把連線表單置中（頁面很大時不要黏左上角） */
.ssh-wrap.ssh-center { justify-content: center; align-items: center; }
.ssh-wrap.ssh-center .ssh-form { width: 560px; max-width: 92vw; }
.ssh-form { max-width: 560px; }
.ssh-title { font-weight: 600; display: flex; align-items: center; gap: 6px; margin-bottom: 12px; }
.ssh-term-area { display: flex; flex-direction: column; }
.ssh-term-area.ssh-full { flex: 1; min-height: 0; }
.ssh-toolbar { display: flex; justify-content: space-between; align-items: center; padding: 4px 2px; gap: 8px; }
.ssh-status { font-size: 13px; display: inline-flex; align-items: center; gap: 7px;
  padding: 3px 11px; border-radius: 999px; font-weight: 500;
  background: rgba(128, 128, 128, .12); color: #888; }
.ssh-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.ssh-ip { opacity: .7; font-variant-numeric: tabular-nums; }
.ssh-status[data-state="connected"] { color: #18a058; background: rgba(24, 160, 88, .14); }
.ssh-status[data-state="connected"] .ssh-dot { animation: ssh-pulse 1.8s infinite; }
.ssh-status[data-state="connecting"] { color: #d99812; background: rgba(217, 152, 18, .14); }
.ssh-status[data-state="error"] { color: #d03050; background: rgba(208, 48, 80, .14); }
.ssh-status[data-state="closed"] { color: #888; background: rgba(128, 128, 128, .14); }
@keyframes ssh-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(24, 160, 88, .5); }
  70%  { box-shadow: 0 0 0 6px rgba(24, 160, 88, 0); }
  100% { box-shadow: 0 0 0 0 rgba(24, 160, 88, 0); }
}
.ssh-term { height: 420px; background: #1e1e1e; padding: 8px; border-radius: 8px;
  border: 1px solid #2b2b30; box-shadow: 0 1px 3px rgba(0,0,0,.18); overflow: hidden; }
.ssh-term.ssh-full { flex: 1; height: auto; min-height: 0; }
/* 卡片標題 icon+文字垂直置中（覆蓋主題預設，避免內容偏上） */
:deep(.n-card > .n-card-header) { display: flex; align-items: center; padding-top: 12px; padding-bottom: 12px; }
/* 「已存帳密」列：flex 列，label 與下拉/刪除鈕保證垂直置中；label 寬度對齊表單 92px 欄 */
.ssh-saved-row { display: flex; align-items: center; margin-bottom: 18px; }
.ssh-saved-label { width: 92px; flex: none; box-sizing: border-box; text-align: right;
  padding-right: 12px; font-size: 14px; }
.ssh-saved-row :deep(.n-button) { margin-left: 6px; }
.ssh-fp { display: block; margin: 8px 0; padding: 6px 8px; background: rgba(128,128,128,.12);
  border-radius: 4px; word-break: break-all; font-size: 13px; }
</style>
