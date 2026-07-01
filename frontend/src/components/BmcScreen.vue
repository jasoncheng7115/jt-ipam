<script setup lang="ts">
/**
 * BMC 主控台（IPMI SOL）— 瀏覽器內序列主控台，版面比照 SshTerminal。
 * 與後端 `/addresses/{id}/bmc/ws` 連線：先送 JSON config，之後資料雙向走 binary（鍵盤 ↔ SOL）。
 * 非破壞：只有鍵盤 + 文字畫面，無滑鼠/電源。Beta。
 */
import { nextTick, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NForm, NFormItem, NInput, NSelect, NSwitch, NButton, NButtonGroup, NIcon, NSpace,
  NTag, NAlert, NSpin, NModal, NTooltip, useMessage,
} from "naive-ui";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { requestBmcTicket, buildBmcWsUrl, listBmcCredentials, createBmcCredential } from "@/api/bmc";
import type { SshCredential } from "@/api/ssh";
import { TerminalIcon, CancelIcon, RefreshIcon, InfoIcon, FitIcon } from "@/icons";

const props = withDefaults(defineProps<{
  addressId: string; ip: string; hostname?: string | null; deviceName?: string | null; fullHeight?: boolean;
}>(), { hostname: null, deviceName: null, fullHeight: false });
const { t } = useI18n();
const msg = useMessage();

type Phase = "form" | "connecting" | "connected" | "closed" | "error";
const phase = ref<Phase>("form");
const errorMsg = ref("");
const connInfo = ref("");
const blankDismissed = ref(false);
const guideOpen = ref(false);

const FONT_MIN = 9, FONT_MAX = 24;
const fontSize = ref(13);
const form = ref({ username: "", password: "", cipher: "auto" });
const selectedCredId = ref<string | null>(null);
const remember = ref(false);
const rememberLabel = ref("");
const creds = ref<SshCredential[]>([]);
const cipherOptions = [
  { label: t("bmc.cipher_auto"), value: "auto" }, { label: "17", value: "17" }, { label: "3", value: "3" },
];

const termEl = ref<HTMLElement | null>(null);
let term: Terminal | null = null;
let fit: FitAddon | null = null;
let ws: WebSocket | null = null;
const enc = new TextEncoder();

const credOptions = ref<{ label: string; value: string }[]>([]);
async function loadCreds() {
  try {
    creds.value = await listBmcCredentials(props.addressId);
    credOptions.value = creds.value.map((c) => ({ label: `${c.label} (${c.username})`, value: c.id }));
    if (creds.value.length && !selectedCredId.value) selectedCredId.value = creds.value[0].id;
  } catch { /* ignore */ }
}
void loadCreds();

function setFont(d: number) {
  fontSize.value = Math.min(FONT_MAX, Math.max(FONT_MIN, fontSize.value + d));
  if (term) { term.options.fontSize = fontSize.value; fit?.fit(); }
}

async function connect() {
  if (!selectedCredId.value && (!form.value.username || !form.value.password)) {
    msg.error(t("bmc.need_creds")); return;
  }
  phase.value = "connecting"; errorMsg.value = ""; blankDismissed.value = false;
  let ticket;
  try { ticket = await requestBmcTicket(props.addressId); }
  catch (e: any) { phase.value = "error"; errorMsg.value = e?.response?.data?.detail ?? t("bmc.ticket_failed"); return; }

  if (remember.value && !selectedCredId.value && form.value.username && form.value.password) {
    try {
      const c = await createBmcCredential({
        label: rememberLabel.value || `${props.ip} BMC`, username: form.value.username,
        password: form.value.password, target_ip_id: props.addressId,
      });
      selectedCredId.value = c.id;
    } catch { /* 存失敗不擋連線 */ }
  }

  await nextTick();
  term = new Terminal({ cursorBlink: true, fontSize: fontSize.value, scrollback: 5000, convertEol: false,
    theme: { background: "#1e1e1e" } });
  fit = new FitAddon();
  term.loadAddon(fit);
  if (termEl.value) { term.open(termEl.value); fit.fit(); }
  term.onData((d) => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(enc.encode(d)); });

  ws = new WebSocket(buildBmcWsUrl(ticket.ws_path, ticket.ticket));
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    const cfg: any = { type: "config" };
    if (selectedCredId.value) cfg.credential_id = selectedCredId.value;
    else { cfg.username = form.value.username; cfg.password = form.value.password; }
    if (form.value.cipher !== "auto") cfg.cipher = Number(form.value.cipher);
    ws!.send(JSON.stringify(cfg));
    form.value.password = "";
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      try {
        const m = JSON.parse(ev.data);
        if (m.type === "status" && m.state === "connected") {
          phase.value = "connected";
          connInfo.value = `cipher ${m.cipher}${m.vendor ? " · " + m.vendor : ""}`;
          nextTick(() => { fit?.fit(); term?.focus(); });
        } else if (m.type === "error") { phase.value = "error"; errorMsg.value = m.message || m.code; cleanupWs(); }
      } catch { /* ignore */ }
    } else { term?.write(new Uint8Array(ev.data as ArrayBuffer)); }
  };
  ws.onclose = () => { if (phase.value === "connected") phase.value = "closed"; };
  ws.onerror = () => { if (phase.value === "connecting") { phase.value = "error"; errorMsg.value = t("bmc.ws_failed"); } };
}

// 符合視窗：序列主控台無法自動傳視窗大小，按一下把 stty rows/cols 對齊 xterm.js 實際大小
// 送進當前 shell（呼叫端需在提示字元）。不必在被控端裝任何腳本。
function fitRemote() {
  if (!term || !fit || !ws || ws.readyState !== WebSocket.OPEN) return;
  fit.fit();
  ws.send(enc.encode(`stty rows ${term.rows} cols ${term.cols}\r`));
  term.focus();
}
function onWinResize() { if (term && fit) fit.fit(); }
window.addEventListener("resize", onWinResize);

function cleanupWs() { try { ws?.close(); } catch { /* */ } ws = null; }
function disconnect() { cleanupWs(); phase.value = "closed"; }
function teardown() { cleanupWs(); try { term?.dispose(); } catch { /* */ } term = null; fit = null; }
function backToForm() { teardown(); phase.value = "form"; errorMsg.value = ""; connInfo.value = ""; void loadCreds(); }
onBeforeUnmount(() => { window.removeEventListener("resize", onWinResize); teardown(); });
</script>

<template>
  <div class="bmc-wrap" :class="{ 'bmc-full': fullHeight, 'bmc-center': fullHeight && phase === 'form' }">
    <!-- 連線設定表單 -->
    <div v-if="phase === 'form'" class="bmc-form">
      <n-card size="small" :bordered="true">
        <template #header>
          <span style="display:flex;align-items:center;gap:8px">
            <n-icon :component="TerminalIcon" :size="18" />
            <span>{{ t("bmc.connect_to", { ip }) }}</span>
            <n-tag size="tiny" type="warning" :bordered="false">Beta</n-tag>
            <n-button size="tiny" quaternary style="margin-left:auto" @click="guideOpen = true">
              <template #icon><n-icon :component="InfoIcon" /></template>{{ t("bmc.guide_btn") }}
            </n-button>
          </span>
        </template>
        <!-- 已存帳密 -->
        <div v-if="credOptions.length" class="bmc-saved-row">
          <span class="bmc-saved-label">{{ t("bmc.saved_cred") }}</span>
          <n-select v-model:value="selectedCredId" :options="credOptions" clearable size="small"
                    :placeholder="t('bmc.saved_cred_ph')" style="flex:1" />
        </div>

        <n-form label-placement="left" :label-width="92" size="small">
          <template v-if="!selectedCredId">
            <n-form-item :label="t('bmc.username')">
              <n-input v-model:value="form.username" placeholder="ADMIN / root" autofocus @keyup.enter="connect" />
            </n-form-item>
            <n-form-item :label="t('bmc.password')">
              <n-input v-model:value="form.password" type="password" show-password-on="click" @keyup.enter="connect" />
            </n-form-item>
          </template>
          <n-form-item :label="t('bmc.cipher')">
            <n-select v-model:value="form.cipher" :options="cipherOptions" style="width:160px" />
          </n-form-item>
          <n-form-item v-if="!selectedCredId" :label="t('bmc.remember')">
            <n-space vertical :size="4" style="width:100%">
              <n-switch v-model:value="remember" />
              <n-input v-if="remember" v-model:value="rememberLabel" size="small" :placeholder="t('bmc.remember_label')" />
            </n-space>
          </n-form-item>
          <n-alert :show-icon="false" type="info" style="margin-bottom:10px">
            {{ selectedCredId ? t("bmc.use_saved_hint") : (remember ? t("bmc.store_hint") : t("bmc.no_store_hint")) }}
          </n-alert>
          <n-space justify="end">
            <n-button type="primary" @click="connect">
              <template #icon><n-icon :component="TerminalIcon" /></template>{{ t("bmc.connect") }}
            </n-button>
          </n-space>
        </n-form>
      </n-card>
    </div>

    <!-- 終端機 -->
    <div v-show="phase !== 'form'" class="bmc-term-area" :class="{ 'bmc-full': fullHeight }">
      <div class="bmc-toolbar">
        <span class="bmc-status" :data-state="phase">
          <n-spin v-if="phase === 'connecting'" :size="12" />
          <span v-else class="bmc-dot" />
          <span>{{ t(`bmc.state_${phase}`) }}</span>
          <span class="bmc-ip">{{ ip }}</span>
          <n-tag v-if="hostname" size="small" :bordered="false" round>{{ hostname }}</n-tag>
          <span class="conn-proto conn-proto--bmc">BMC SOL</span>
          <span v-if="connInfo" class="bmc-meta">{{ connInfo }}</span>
        </span>
        <n-space :size="8" align="center">
          <n-tooltip v-if="phase === 'connected'" :delay="0" trigger="hover" placement="bottom">
            <template #trigger>
              <n-button size="tiny" @click="fitRemote">
                <template #icon><n-icon :component="FitIcon" /></template>{{ t("bmc.fit_window") }}
              </n-button>
            </template>
            <span style="display:inline-block;max-width:300px">{{ t("bmc.fit_hint") }}</span>
          </n-tooltip>
          <n-button size="tiny" quaternary :title="t('bmc.guide_btn')" @click="guideOpen = true">
            <template #icon><n-icon :component="InfoIcon" /></template>{{ t("bmc.guide_btn") }}
          </n-button>
          <n-button-group v-if="phase === 'connected'" size="tiny">
            <n-button :disabled="fontSize <= FONT_MIN" :title="t('bmc.font_smaller')" @click="setFont(-1)">A−</n-button>
            <n-button :disabled="fontSize >= FONT_MAX" :title="t('bmc.font_larger')" @click="setFont(1)">A+</n-button>
          </n-button-group>
          <n-button v-if="phase === 'connected'" size="tiny" type="error" ghost @click="disconnect">
            <template #icon><n-icon :component="CancelIcon" /></template>{{ t("bmc.disconnect") }}
          </n-button>
          <n-button v-if="phase === 'closed' || phase === 'error'" size="tiny" @click="backToForm">
            <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("bmc.reconnect") }}
          </n-button>
        </n-space>
      </div>
      <n-alert v-if="phase === 'error'" type="error" :show-icon="true" style="margin:8px 0">{{ errorMsg }}</n-alert>
      <n-alert v-if="phase === 'connected' && !blankDismissed" class="bmc-blank" type="info" closable :show-icon="true"
               style="margin:6px 0" @close="blankDismissed = true">
        {{ t("bmc.blank_hint") }}
        <n-button text type="primary" size="small" style="margin-left:4px" @click="guideOpen = true">
          {{ t("bmc.guide_open") }}
        </n-button>
      </n-alert>
      <div ref="termEl" class="bmc-term" :class="{ 'bmc-full': fullHeight, 'term-dim': phase === 'closed' }" />
    </div>

    <!-- 設定教學：讓 SOL 顯示主機畫面（序列主控台設定） -->
    <n-modal v-model:show="guideOpen" preset="card" :title="t('bmc.guide_title')"
             style="width:720px;max-width:94vw" :bordered="false">
      <div class="bmc-guide">
        <p class="bmc-guide-intro">{{ t("bmc.guide_intro") }}</p>

        <div class="bmc-guide-step">
          <span class="bmc-guide-num">1</span>
          <div class="bmc-guide-body">
            <h4>{{ t("bmc.guide_s1") }}</h4>
            <p>{{ t("bmc.guide_s1_d") }}</p>
            <pre>dmesg | grep -iE 'ttyS|SPCR'
# 例：ACPI: SPCR: console: uart,io,0x3f8,115200  → 0x3f8=ttyS0, 0x2f8=ttyS1</pre>
          </div>
        </div>

        <div class="bmc-guide-step">
          <span class="bmc-guide-num">2</span>
          <div class="bmc-guide-body">
            <h4>{{ t("bmc.guide_s2") }}</h4>
            <p>{{ t("bmc.guide_s2_d") }}</p>
            <div class="bmc-guide-label">{{ t("bmc.guide_s2_grub") }}</div>
            <pre># /etc/default/grub 的 GRUB_CMDLINE_LINUX 內加：
console=tty0 console=ttyS0,115200n8
update-grub          # RHEL 系：grub2-mkconfig -o /boot/grub2/grub.cfg</pre>
            <div class="bmc-guide-label">{{ t("bmc.guide_s2_pve") }}</div>
            <pre># 編輯 /etc/kernel/cmdline，同一行末尾加：
console=tty0 console=ttyS0,115200n8
proxmox-boot-tool refresh</pre>
          </div>
        </div>

        <div class="bmc-guide-step">
          <span class="bmc-guide-num">3</span>
          <div class="bmc-guide-body">
            <h4>{{ t("bmc.guide_s3") }}</h4>
            <p>{{ t("bmc.guide_s3_d") }}</p>
            <pre>systemctl enable --now serial-getty@ttyS0</pre>
          </div>
        </div>

        <div class="bmc-guide-step">
          <span class="bmc-guide-num">4</span>
          <div class="bmc-guide-body">
            <h4>{{ t("bmc.guide_s4") }}</h4>
            <p>{{ t("bmc.guide_s4_d") }}</p>
          </div>
        </div>

        <div class="bmc-guide-step">
          <span class="bmc-guide-num">5</span>
          <div class="bmc-guide-body">
            <h4>{{ t("bmc.guide_s5") }}</h4>
            <p>{{ t("bmc.guide_s5_d") }}</p>
          </div>
        </div>

        <n-alert type="success" :show-icon="true" :bordered="false" style="margin-top:6px">
          {{ t("bmc.guide_tip") }}
        </n-alert>

        <h4 class="bmc-ts-head">{{ t("bmc.guide_ts") }}</h4>
        <ul class="bmc-ts">
          <li><b>{{ t("bmc.guide_ts_blank") }}</b><br>{{ t("bmc.guide_ts_blank_d") }}
            <code>echo test &gt; /dev/ttyS0</code> / <code>/dev/ttyS1</code></li>
          <li><b>{{ t("bmc.guide_ts_baud") }}</b><br>{{ t("bmc.guide_ts_baud_d") }}
            <code>ipmitool -I open sol info 1 | grep 'Bit Rate'</code></li>
          <li><b>{{ t("bmc.guide_ts_term") }}</b><br>{{ t("bmc.guide_ts_term_d") }}</li>
          <li><b>{{ t("bmc.guide_ts_size") }}</b><br>{{ t("bmc.guide_ts_size_d") }}</li>
        </ul>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.bmc-wrap { width: 100%; }
.bmc-wrap.bmc-full { height: 100%; display: flex; flex-direction: column; }
.bmc-wrap.bmc-center { justify-content: center; align-items: center; }
.bmc-wrap.bmc-center .bmc-form { width: 560px; max-width: 92vw; }
.bmc-form { max-width: 560px; }
.bmc-term-area { display: flex; flex-direction: column; }
.bmc-term-area.bmc-full { flex: 1; min-height: 0; }
.bmc-toolbar { display: flex; justify-content: space-between; align-items: center; padding: 4px 2px; gap: 8px; }
.bmc-status { font-size: 13px; display: inline-flex; align-items: center; gap: 7px;
  padding: 3px 11px; border-radius: 999px; font-weight: 500; background: rgba(128,128,128,.12); color: #888; }
.bmc-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.bmc-ip { opacity: .7; font-variant-numeric: tabular-nums; }
.bmc-meta { opacity: .7; font-size: 12px; }
.bmc-status[data-state="connected"] { color: #18a058; background: rgba(24,160,88,.14); }
.bmc-status[data-state="connected"] .bmc-dot { animation: bmc-pulse 1.8s infinite; }
.bmc-status[data-state="connecting"] { color: #d99812; background: rgba(217,152,18,.14); }
.bmc-status[data-state="error"] { color: #d03050; background: rgba(208,48,80,.14); }
.bmc-status[data-state="closed"] { color: #888; background: rgba(128,128,128,.14); }
@keyframes bmc-pulse { 0% { box-shadow: 0 0 0 0 rgba(24,160,88,.5); } 70% { box-shadow: 0 0 0 6px rgba(24,160,88,0); } 100% { box-shadow: 0 0 0 0 rgba(24,160,88,0); } }
.bmc-term { height: 420px; background: #1e1e1e; padding: 8px; border-radius: 10px;
  border: 1px solid #2b2b30; box-shadow: 0 10px 30px rgba(0,0,0,.30), 0 3px 10px rgba(0,0,0,.20); overflow: hidden; }
.bmc-term.bmc-full { flex: 1; height: auto; min-height: 0; }
/* 卡片標題 icon+文字垂直置中（覆蓋主題預設，避免內容偏上） */
:deep(.n-card > .n-card-header) { display: flex; align-items: center; padding-top: 12px; padding-bottom: 12px; }
.bmc-saved-row { display: flex; align-items: center; margin-bottom: 18px; }
.bmc-saved-label { width: 92px; flex: none; box-sizing: border-box; text-align: right; padding-right: 12px; font-size: 14px; }
.conn-proto { font-weight: 700; font-size: 11px; letter-spacing: .4px; line-height: 1; padding: 2px 7px; border-radius: 999px; }
.conn-proto--bmc { color: #d99812; background: rgba(217,152,18,.16); }
.term-dim { filter: grayscale(1) brightness(.45); pointer-events: none; transition: filter .25s; }
/* 空白提示列收緊：行距、上下內距不要那麼高 */
.bmc-blank :deep(.n-alert-body) { padding: 7px 12px; }
.bmc-blank :deep(.n-alert-body__content) { line-height: 1.5; font-size: 13px; }
/* 設定教學彈窗 */
.bmc-guide-intro { margin: 0 0 16px; color: #555; }
html[data-theme="dark"] .bmc-guide-intro { color: #b6c2d4; }
.bmc-guide-step { display: flex; gap: 12px; margin-bottom: 16px; }
.bmc-guide-num { flex: none; width: 24px; height: 24px; border-radius: 50%; background: #18a058; color: #fff;
  font-size: 13px; font-weight: 700; display: flex; align-items: center; justify-content: center; margin-top: 1px; }
.bmc-guide-body { flex: 1; min-width: 0; }
.bmc-guide-body h4 { margin: 2px 0 4px; font-size: 14px; }
.bmc-guide-body p { margin: 0 0 8px; color: #666; font-size: 13px; }
html[data-theme="dark"] .bmc-guide-body p { color: #a6b2c4; }
.bmc-guide-label { font-size: 12px; font-weight: 600; color: #888; margin: 6px 0 3px; }
.bmc-guide-body pre { margin: 0 0 8px; padding: 10px 12px; background: #1e1e1e; color: #e6e6e6;
  border-radius: 8px; font-size: 12.5px; line-height: 1.55; overflow-x: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre-wrap; word-break: break-word; }
.bmc-ts-head { margin: 18px 0 8px; font-size: 14px; }
.bmc-ts { margin: 0; padding-left: 18px; }
.bmc-ts li { margin-bottom: 9px; font-size: 13px; color: #555; line-height: 1.5; }
html[data-theme="dark"] .bmc-ts li { color: #a6b2c4; }
.bmc-ts code { background: rgba(128,128,128,.16); padding: 1px 6px; border-radius: 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
</style>
