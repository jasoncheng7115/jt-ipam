<script setup lang="ts">
/**
 * PVE 主控台（noVNC / xterm）。版面與工具列比照 SSH/RDP/VNC。
 * 連線時要求輸入 PVE 帳密（可選擇存進金庫）；kind=vm → @novnc/novnc 圖形 RFB（可送出按鍵、縮放切換）；
 * kind=ct → xterm.js + PVE term 協定。WS 走同站後端代理到 PVE。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpin, NButton, NButtonGroup, NDropdown, NIcon, NSelect, NInput, NSwitch, NAlert, NTag,
  NForm, NFormItem, NSpace, NPopconfirm, useMessage,
} from "naive-ui";
import {
  NoVncIcon, TerminalIcon, DeleteIcon, CancelIcon, RefreshIcon, KeyIcon, ExpandIcon, ReduceIcon, ChevronDownIcon, InfoIcon,
} from "@/icons";
import { buildSendKeysMenu } from "@/composables/useSendKeys";
import {
  requestNovncTicket, buildNovncWsUrl, listPveCredentials, createPveCredential,
  deletePveCredential, type PveCredential,
} from "@/api/novnc";

const props = withDefaults(defineProps<{
  addressId: string;
  ip: string;
  hostname?: string | null;
  deviceName?: string | null;
  kind?: "vm" | "ct";
  fullHeight?: boolean;
}>(), { fullHeight: false, hostname: null, deviceName: null, kind: "vm" });

const { t } = useI18n();
const msg = useMessage();

type Phase = "form" | "connecting" | "connected" | "closed" | "error";
const phase = ref<Phase>("form");
const errorMsg = ref("");

const form = ref({ username: "", password: "", realm: "pam" });
const realmOpts = [
  { label: "pam (Linux PAM)", value: "pam" }, { label: "pve (Proxmox VE)", value: "pve" },
  { label: "ad (Active Directory)", value: "ad" }, { label: "ldap (LDAP)", value: "ldap" },
];
const remember = ref(false);
const ctHintDismissed = ref(false);
const savedCreds = ref<PveCredential[]>([]);
const selectedCredId = ref<string | null>(null);
const protoLabel = computed(() => (props.kind === "ct" ? "xterm" : "noVNC"));
const isVm = computed(() => props.kind === "vm");
const headIcon = computed(() => (isVm.value ? NoVncIcon : TerminalIcon));

const screenBox = ref<HTMLDivElement | null>(null);
const scaleMode = ref<"fit" | "native">("fit");
let rfb: any = null;
let ws: WebSocket | null = null;
let term: any = null;
let fitAddon: any = null;
let heartbeat: number | null = null;

const credOptions = computed(() => savedCreds.value.map((c) => ({
  label: `${c.label}（${c.username}）`, value: c.id,
})));

async function loadCreds() {
  try {
    savedCreds.value = await listPveCredentials(props.addressId);
    // 有已存帳密就預設選最近一筆 → 不必再輸入直接連線（比照 SSH）
    if (!selectedCredId.value && savedCreds.value.length) selectedCredId.value = savedCreds.value[0].id;
  } catch { savedCreds.value = []; }
}
onMounted(loadCreds);

// 只停連線、保留畫面 DOM（已關閉狀態凍結最後一幀，比照 RDP）
function stopConnection() {
  if (heartbeat) { clearInterval(heartbeat); heartbeat = null; }
  if (rfb) { try { rfb.disconnect(); } catch { /* noop */ } rfb = null; }
  if (ws) { try { ws.close(); } catch { /* noop */ } ws = null; }
}
// 完整拆除：連同 terminal 與畫面 DOM 一起清掉（回表單／離開頁面時用）
function cleanup() {
  stopConnection();
  if (term) { try { term.dispose(); } catch { /* noop */ } term = null; fitAddon = null; }
  if (screenBox.value) screenBox.value.innerHTML = "";
}
onBeforeUnmount(cleanup);

function utf8len(s: string): number { return new TextEncoder().encode(s).length; }

async function connect() {
  errorMsg.value = "";
  phase.value = "connecting";
  let credId: string | null = selectedCredId.value;
  if (!credId && remember.value) {
    try {
      const saved = await createPveCredential({
        label: `pve@${props.ip}`,
        target_ip_id: props.addressId,
        username: form.value.username.includes("@") ? form.value.username.trim()
          : `${form.value.username.trim()}@${form.value.realm}`,
        password: form.value.password,
      });
      credId = saved.id;
    } catch (e: any) {
      phase.value = "error";
      errorMsg.value = e?.response?.data?.detail || t("novnc.err_save_cred");
      return;
    }
  }
  let ticket;
  try {
    ticket = await requestNovncTicket(props.addressId, credId
      ? { credential_id: credId }
      : { username: form.value.username, password: form.value.password, realm: form.value.realm });
  } catch (e: any) {
    phase.value = "error";
    errorMsg.value = e?.response?.data?.detail || t("novnc.err_ticket");
    return;
  }
  form.value.password = "";
  const wsUrl = buildNovncWsUrl(ticket.ws_path, ticket.ticket);
  await nextTick();
  if (ticket.kind === "vm") await connectRfb(wsUrl, ticket.vnc_password);
  else await connectXterm(wsUrl, ticket.pve_user, ticket.vnc_password);
  if (selectedCredId.value || remember.value) void loadCreds();
}

async function connectRfb(wsUrl: string, password: string) {
  try {
    const RFB = (await import("@novnc/novnc")).default;
    if (screenBox.value) screenBox.value.innerHTML = "";
    rfb = new RFB(screenBox.value, wsUrl, { credentials: { password } });
    rfb.scaleViewport = scaleMode.value === "fit";
    rfb.clipViewport = false;
    rfb.background = "#000";
    rfb.addEventListener("connect", () => { phase.value = "connected"; });
    rfb.addEventListener("disconnect", (e: any) => {
      // 連線中斷 → 停在「已關閉」狀態（畫面保留、可重新連線），不退回表單
      if (phase.value === "form" || phase.value === "closed") return;
      if (e?.detail?.clean) phase.value = "closed";
      else { phase.value = "error"; errorMsg.value = errorMsg.value || t("novnc.err_disconnected"); }
      stopConnection();
    });
    rfb.addEventListener("securityfailure", (e: any) => {
      phase.value = "error"; errorMsg.value = e?.detail?.reason || t("novnc.err_auth");
    });
  } catch (e: any) {
    phase.value = "error"; errorMsg.value = e?.message || t("novnc.err_connect");
  }
}

async function connectXterm(wsUrl: string, pveUser: string, vncticket: string) {
  try {
    const [{ Terminal }, { FitAddon }] = await Promise.all([
      import("@xterm/xterm"), import("@xterm/addon-fit"),
    ]);
    await import("@xterm/xterm/css/xterm.css");
    term = new Terminal({ cursorBlink: true, fontSize: 13, theme: { background: "#000000" } });
    fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    if (screenBox.value) { screenBox.value.innerHTML = ""; term.open(screenBox.value); fitAddon.fit(); }
    ws = new WebSocket(wsUrl, "binary");
    ws.binaryType = "arraybuffer";
    const enc = new TextEncoder();
    let authed = false;
    ws.onopen = () => { ws!.send(enc.encode(`${pveUser}:${vncticket}\n`)); };
    ws.onmessage = (ev) => {
      const buf = ev.data instanceof ArrayBuffer ? new Uint8Array(ev.data) : enc.encode(String(ev.data));
      if (!authed) {
        authed = true; phase.value = "connected";
        ws!.send(enc.encode(`1:${term.cols}:${term.rows}:`));
        term.focus();
        term.onData((d: string) => ws?.send(enc.encode(`0:${utf8len(d)}:${d}`)));
        heartbeat = window.setInterval(() => { try { ws?.send(enc.encode("2")); } catch { /* noop */ } }, 30000);
        return;
      }
      term.write(buf);
    };
    ws.onerror = () => { if (phase.value !== "connected") { phase.value = "error"; errorMsg.value = t("novnc.err_connect"); } };
    ws.onclose = () => { if (phase.value === "connected") phase.value = "closed"; stopConnection(); };
    window.addEventListener("resize", onResize);
  } catch (e: any) {
    phase.value = "error"; errorMsg.value = e?.message || t("novnc.err_connect");
  }
}

function onResize() {
  if (fitAddon && term && ws && ws.readyState === 1) {
    fitAddon.fit();
    ws.send(new TextEncoder().encode(`1:${term.cols}:${term.rows}:`));
  }
}
onBeforeUnmount(() => window.removeEventListener("resize", onResize));

// ── 縮放：自動縮放（fit）/ 原始解析度（native，1:1 可捲動）──
function setScale(m: "fit" | "native") {
  scaleMode.value = m;
  if (rfb) rfb.scaleViewport = m === "fit";
}

// ── 送出特殊按鍵（僅圖形 RFB 用，透過 @novnc/novnc 的 sendKey/sendCtrlAltDel）──
const sendKeysMenu = buildSendKeysMenu(false);
const KS: Record<string, number> = {
  ctrl: 0xffe3, alt: 0xffe9, win: 0xffeb, esc: 0xff1b, tab: 0xff09,
};
function rfbCombo(mods: number[], keysym: number) {
  if (!rfb) return;
  for (const m of mods) rfb.sendKey(m, null, true);
  rfb.sendKey(keysym, null, true);
  rfb.sendKey(keysym, null, false);
  for (const m of [...mods].reverse()) rfb.sendKey(m, null, false);
}
function onSendKey(key: string) {
  if (!rfb) return;
  if (key === "cad") rfb.sendCtrlAltDel();
  else if (key === "esc") rfbCombo([], KS.esc);
  else if (key === "tab") rfbCombo([], KS.tab);
  else if (key === "win") rfbCombo([], KS.win);
  else if (key === "alttab") rfbCombo([KS.alt], KS.tab);
  else if (key === "ctrlesc") rfbCombo([KS.ctrl], KS.esc);
  else if (/^f\d+$/.test(key)) rfbCombo([], 0xffbe + (parseInt(key.slice(1), 10) - 1));
}

// 中斷連線：停在「已關閉」狀態（畫面保留），不退回表單（比照 RDP）
function disconnect() { phase.value = "closed"; stopConnection(); }
// 重新連線：回到表單（已存帳密會自動帶入，一鍵再連）
function backToForm() { cleanup(); phase.value = "form"; errorMsg.value = ""; }

async function removeCred() {
  if (!selectedCredId.value) return;
  try { await deletePveCredential(selectedCredId.value); await loadCreds(); selectedCredId.value = null; }
  catch { msg.error(t("errors.network")); }
}
</script>

<template>
  <div class="vnc-wrap" :class="{ 'vnc-full': fullHeight, 'vnc-center': fullHeight && (phase === 'form' || phase === 'error') }">
    <!-- 連線設定表單（版面比照 SSH/RDP/VNC）-->
    <div v-if="phase === 'form' || phase === 'error'" class="vnc-form">
      <n-card size="small" :bordered="true">
        <template #header>
          <span style="display:flex;align-items:center;gap:8px">
            <n-icon :component="headIcon" :size="18" />
            <span>{{ t("novnc.connect_to", { ip }) }}</span>
            <n-tag size="small" type="warning" :bordered="false" round>PVE</n-tag>
            <n-tag size="small" :bordered="false" round>{{ protoLabel }}</n-tag>
          </span>
        </template>
        <n-alert v-if="phase === 'error'" type="error" :show-icon="true" style="margin-bottom:12px">
          {{ errorMsg }}
        </n-alert>

        <!-- 已存 PVE 帳密 -->
        <div v-if="credOptions.length" class="vnc-saved-row">
          <span class="vnc-saved-label">{{ t("novnc.saved_cred") }}</span>
          <n-select v-model:value="selectedCredId" :options="credOptions" clearable size="small"
                    :placeholder="t('novnc.use_typed')" style="flex:1" />
          <n-popconfirm v-if="selectedCredId" @positive-click="removeCred">
            <template #trigger>
              <n-button quaternary type="error" size="small">
                <template #icon><n-icon :component="DeleteIcon" /></template>
              </n-button>
            </template>
            {{ t("common.confirm_delete") }}
          </n-popconfirm>
        </div>

        <n-form label-placement="left" :label-width="92" size="small">
          <template v-if="!selectedCredId">
            <n-form-item :label="t('novnc.username')">
              <n-input v-model:value="form.username" placeholder="root" />
            </n-form-item>
            <n-form-item :label="t('novnc.password')">
              <n-input v-model:value="form.password" type="password" show-password-on="click"
                       :placeholder="t('common.please_enter')" @keyup.enter="connect" />
            </n-form-item>
            <n-form-item :label="t('novnc.realm')">
              <n-select v-model:value="form.realm" :options="realmOpts" />
            </n-form-item>
            <n-form-item :label="t('novnc.remember')">
              <n-switch v-model:value="remember" />
            </n-form-item>
          </template>

          <n-alert :show-icon="false" type="info" style="margin-bottom:10px">
            {{ selectedCredId ? t("novnc.use_saved_hint") : t("novnc.no_store_hint") }}
          </n-alert>
          <n-space justify="end">
            <n-button type="primary" :disabled="!selectedCredId && (!form.username || !form.password)"
                      @click="connect">
              <template #icon><n-icon :component="headIcon" /></template>
              {{ protoLabel }} {{ t("novnc.connect") }}
            </n-button>
          </n-space>
        </n-form>
      </n-card>
    </div>

    <!-- 連線中 / 已連線（工具列 + 畫面，比照 VNC）-->
    <div v-show="phase === 'connecting' || phase === 'connected' || phase === 'closed'" class="vnc-screen-area" :class="{ 'vnc-full': fullHeight }">
      <div class="vnc-toolbar">
        <span class="vnc-status" :data-state="phase">
          <n-spin v-if="phase === 'connecting'" :size="12" />
          <span v-else class="vnc-dot" />
          <span>{{ t(`novnc.state_${phase}`) }}</span>
          <span class="vnc-ip">{{ ip }}</span>
          <n-tag v-if="hostname" size="small" :bordered="false" round>{{ hostname }}</n-tag>
          <n-tag size="small" type="warning" :bordered="false" round>PVE</n-tag>
          <n-tag size="small" :bordered="false" round>{{ protoLabel }}</n-tag>
          <n-tag v-if="deviceName" size="small" type="info" :bordered="false" round>{{ deviceName }}</n-tag>
        </span>
        <!-- LXC（xterm）提示：放在狀態列右側、單行，太長以 … 截斷 -->
        <span v-if="phase === 'connected' && !isVm && !ctHintDismissed" class="vnc-hint"
              :title="t('novnc.lxc_enter_hint')">
          <n-icon :component="InfoIcon" :size="13" class="vnc-hint-i" />
          <span class="vnc-hint-text">{{ t("novnc.lxc_enter_hint") }}</span>
          <n-icon :component="CancelIcon" :size="12" class="vnc-hint-x" @click="ctHintDismissed = true" />
        </span>
        <n-space :size="8" align="center">
          <!-- 送出按鍵（僅圖形 VM）-->
          <n-dropdown v-if="phase === 'connected' && isVm" trigger="click" :options="sendKeysMenu"
                      size="small" @select="onSendKey">
            <n-button size="tiny">
              <template #icon><n-icon :component="KeyIcon" /></template>
              {{ t("vnc.send_keys") }}<n-icon :component="ChevronDownIcon" style="margin-left:2px" />
            </n-button>
          </n-dropdown>
          <!-- 縮放：自動縮放 / 原始解析度（僅圖形 VM）-->
          <n-button-group v-if="phase === 'connected' && isVm" size="tiny">
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
          <!-- 已關閉 → 重新連線（回表單，已存帳密會自動帶入）-->
          <n-button v-if="phase === 'closed'" size="tiny" @click="backToForm">
            <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("vnc.reconnect") }}
          </n-button>
        </n-space>
      </div>
      <div ref="screenBox" class="vnc-canvas-box"
           :class="{ 'vnc-full': fullHeight, 'vnc-native': scaleMode === 'native', 'vnc-term': !isVm, 'term-dim': phase === 'closed' }"></div>
    </div>
  </div>
</template>

<style scoped>
.vnc-wrap { width: 100%; }
.vnc-wrap.vnc-full { height: 100%; display: flex; flex-direction: column; }
.vnc-wrap.vnc-center { justify-content: center; align-items: center; }
.vnc-wrap.vnc-center .vnc-form { width: 520px; max-width: 92vw; }
.vnc-form { max-width: 520px; }
.vnc-saved-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.vnc-saved-label { font-size: 12px; opacity: .75; white-space: nowrap; }
.vnc-screen-area { display: flex; flex-direction: column; }
.vnc-screen-area.vnc-full { flex: 1; min-height: 0; }
.vnc-toolbar { display: flex; justify-content: space-between; align-items: center; padding: 4px 2px; gap: 8px; }
.vnc-status { font-size: 13px; display: inline-flex; align-items: center; gap: 7px;
  padding: 3px 11px; border-radius: 999px; font-weight: 500;
  background: rgba(128, 128, 128, .12); color: #888; }
.vnc-status[data-state="connected"] { color: #18a058; background: rgba(24,160,88,.12); }
.vnc-status[data-state="connecting"] { color: #f0a020; background: rgba(240,160,32,.12); }
.vnc-status[data-state="closed"] { color: #888; background: rgba(128,128,128,.14); }
.vnc-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.vnc-ip { opacity: .7; font-variant-numeric: tabular-nums; }
.vnc-canvas-box { flex: 1; min-height: 360px; background: #000; border-radius: 10px;
  border: 1px solid #2b2b30; box-shadow: 0 10px 30px rgba(0,0,0,.30), 0 3px 10px rgba(0,0,0,.20);
  overflow: hidden; }
.vnc-canvas-box.vnc-full { min-height: 0; }
.vnc-canvas-box.vnc-native { overflow: auto; }
/* xterm（CT）終端機留一點內距，不要貼齊邊緣（比照 SSH 主控台）*/
.vnc-canvas-box.vnc-term { padding: 8px; }
/* 已斷線：整個畫面反灰並停用互動，讓使用者一眼看出已中斷 */
.term-dim { filter: grayscale(1) brightness(.45); pointer-events: none; transition: filter .25s; }
.vnc-hint { flex: 1; min-width: 0; display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; color: #6b86c2; }
.vnc-hint-i { flex: none; opacity: .8; }
.vnc-hint-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vnc-hint-x { flex: none; cursor: pointer; opacity: .55; }
.vnc-hint-x:hover { opacity: 1; }
</style>
