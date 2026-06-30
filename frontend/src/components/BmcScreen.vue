<script setup lang="ts">
/**
 * BMC OOB主控台（IPMI SOL）— 瀏覽器內序列主控台。
 * 與後端 `/addresses/{id}/bmc/ws` 連線：先送 JSON config，之後資料雙向走 binary（鍵盤 ↔ SOL）。
 * 非破壞：只有鍵盤 + 文字畫面，無滑鼠/電源。Beta。
 */
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NForm, NFormItem, NInput, NSelect, NButton, NIcon, NSpace, NTag, NAlert, useMessage,
} from "naive-ui";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { requestBmcTicket, buildBmcWsUrl, listBmcCredentials, createBmcCredential } from "@/api/bmc";
import type { SshCredential } from "@/api/ssh";
import { TerminalIcon, CancelIcon, RefreshIcon, SaveIcon } from "@/icons";

const props = defineProps<{ addressId: string; ip: string }>();
const { t } = useI18n();
const msg = useMessage();

type Phase = "form" | "connecting" | "connected" | "closed" | "error";
const phase = ref<Phase>("form");
const errMsg = ref("");
const connInfo = ref("");

const form = ref({ username: "", password: "", cipher: "auto", credentialId: null as string | null });
const remember = ref(false);
const rememberLabel = ref("");
const creds = ref<SshCredential[]>([]);
const cipherOptions = [
  { label: t("bmc.cipher_auto"), value: "auto" },
  { label: "17", value: "17" }, { label: "3", value: "3" },
];

const termBox = ref<HTMLElement | null>(null);
let term: Terminal | null = null;
let fit: FitAddon | null = null;
let ws: WebSocket | null = null;
const enc = new TextEncoder();

const credOptions = computed(() => creds.value.map((c) => ({ label: `${c.label} (${c.username})`, value: c.id })));

async function loadCreds() {
  try {
    creds.value = await listBmcCredentials(props.addressId);
    if (creds.value.length && !form.value.credentialId) form.value.credentialId = creds.value[0].id;
  } catch { /* ignore */ }
}
void loadCreds();

function wsSendJson(o: unknown) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(o)); }

async function connect() {
  if (!form.value.credentialId && (!form.value.username || !form.value.password)) {
    msg.error(t("bmc.need_creds")); return;
  }
  phase.value = "connecting"; errMsg.value = "";
  let ticket;
  try { ticket = await requestBmcTicket(props.addressId); }
  catch (e: any) { phase.value = "error"; errMsg.value = e?.response?.data?.detail ?? t("bmc.ticket_failed"); return; }

  // 若要記住帳密，先存金庫
  if (remember.value && !form.value.credentialId && form.value.username && form.value.password) {
    try {
      const c = await createBmcCredential({
        label: rememberLabel.value || `${props.ip} BMC`, username: form.value.username,
        password: form.value.password, target_ip_id: props.addressId,
      });
      form.value.credentialId = c.id;
    } catch { /* 存失敗不擋連線 */ }
  }

  await nextTick();
  term = new Terminal({ cursorBlink: true, fontSize: 14, scrollback: 5000, convertEol: false,
    theme: { background: "#0b0b0d" } });
  fit = new FitAddon();
  term.loadAddon(fit);
  if (termBox.value) { term.open(termBox.value); fit.fit(); }
  term.onData((d) => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(enc.encode(d)); });

  ws = new WebSocket(buildBmcWsUrl(ticket.ws_path, ticket.ticket));
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    const cfg: any = { type: "config" };
    if (form.value.credentialId) cfg.credential_id = form.value.credentialId;
    else { cfg.username = form.value.username; cfg.password = form.value.password; }
    if (form.value.cipher !== "auto") cfg.cipher = Number(form.value.cipher);
    wsSendJson(cfg);
    form.value.password = "";   // 不留在記憶體
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      try {
        const m = JSON.parse(ev.data);
        if (m.type === "status" && m.state === "connected") {
          phase.value = "connected";
          connInfo.value = `cipher ${m.cipher}${m.vendor ? " · " + m.vendor : ""}`;
          nextTick(() => { fit?.fit(); term?.focus(); });
        } else if (m.type === "error") { phase.value = "error"; errMsg.value = m.message || m.code; cleanupWs(); }
      } catch { /* ignore */ }
    } else {
      term?.write(new Uint8Array(ev.data as ArrayBuffer));
    }
  };
  ws.onclose = () => { if (phase.value === "connected") phase.value = "closed"; };
  ws.onerror = () => { if (phase.value === "connecting") { phase.value = "error"; errMsg.value = t("bmc.ws_failed"); } };
}

function cleanupWs() { try { ws?.close(); } catch { /* */ } ws = null; }
function disconnect() { cleanupWs(); phase.value = "closed"; }
function backToForm() {
  cleanupWs();
  try { term?.dispose(); } catch { /* */ }
  term = null; fit = null;
  phase.value = "form"; errMsg.value = ""; connInfo.value = "";
  void loadCreds();
}
onBeforeUnmount(() => { cleanupWs(); try { term?.dispose(); } catch { /* */ } });
</script>

<template>
  <div class="bmc-wrap">
    <div class="bmc-bar">
      <n-space align="center" :size="8">
        <n-icon :component="TerminalIcon" :size="18" />
        <strong>BMC · {{ ip }}</strong>
        <n-tag size="tiny" type="warning" :bordered="false">Beta</n-tag>
        <n-tag v-if="phase === 'connected'" size="tiny" type="success" :bordered="false">{{ t("bmc.connected") }} · {{ connInfo }}</n-tag>
        <n-tag v-else-if="phase === 'closed'" size="tiny" :bordered="false">{{ t("bmc.disconnected") }}</n-tag>
      </n-space>
      <n-space :size="6">
        <n-button v-if="phase === 'connected'" size="small" type="error" ghost @click="disconnect">
          <template #icon><n-icon :component="CancelIcon" /></template>{{ t("bmc.disconnect") }}
        </n-button>
        <n-button v-if="phase === 'closed' || phase === 'error'" size="small" @click="backToForm">
          <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("bmc.reconnect") }}
        </n-button>
      </n-space>
    </div>

    <div v-if="phase === 'form'" class="bmc-form">
      <n-card :bordered="false" style="max-width:460px;margin:24px auto">
        <template #header><n-space align="center" :size="8"><n-icon :component="TerminalIcon" :size="18" />{{ t("bmc.connect_title") }}</n-space></template>
        <n-alert type="info" :show-icon="true" style="margin-bottom:14px">{{ t("bmc.hint") }}</n-alert>
        <n-form>
          <n-form-item v-if="credOptions.length" :label="t('bmc.saved_cred')">
            <n-select v-model:value="form.credentialId" :options="credOptions" clearable :placeholder="t('bmc.saved_cred_ph')" />
          </n-form-item>
          <template v-if="!form.credentialId">
            <n-form-item :label="t('bmc.username')">
              <n-input v-model:value="form.username" placeholder="ADMIN / root" />
            </n-form-item>
            <n-form-item :label="t('bmc.password')">
              <n-input v-model:value="form.password" type="password" show-password-on="click" @keydown.enter="connect" />
            </n-form-item>
            <n-form-item :label="t('bmc.remember')">
              <n-space vertical :size="4" style="width:100%">
                <n-input v-if="remember" v-model:value="rememberLabel" size="small" :placeholder="t('bmc.remember_label')" />
                <n-button size="tiny" :type="remember ? 'primary' : 'default'" @click="remember = !remember">
                  <template #icon><n-icon :component="SaveIcon" /></template>{{ remember ? t("bmc.remember_on") : t("bmc.remember_off") }}
                </n-button>
              </n-space>
            </n-form-item>
          </template>
          <n-form-item :label="t('bmc.cipher')">
            <n-select v-model:value="form.cipher" :options="cipherOptions" style="width:160px" />
          </n-form-item>
        </n-form>
        <n-button type="primary" block @click="connect">
          <template #icon><n-icon :component="TerminalIcon" /></template>{{ t("bmc.connect") }}
        </n-button>
      </n-card>
    </div>

    <div v-else-if="phase === 'error'" class="bmc-center">
      <n-alert type="error" :title="t('bmc.connect_failed')" style="max-width:520px">{{ errMsg }}</n-alert>
    </div>

    <div v-show="phase === 'connecting' || phase === 'connected' || phase === 'closed'" class="bmc-term-area">
      <div ref="termBox" class="bmc-term" :class="{ dim: phase === 'closed' }"></div>
    </div>
  </div>
</template>

<style scoped>
.bmc-wrap { display:flex; flex-direction:column; height:100%; min-height:0; }
.bmc-bar { display:flex; justify-content:space-between; align-items:center; padding:8px 12px; border-bottom:1px solid var(--n-border-color,#2b2b30); gap:8px; flex-wrap:wrap; }
.bmc-form, .bmc-center { flex:1; overflow:auto; display:flex; }
.bmc-center { align-items:center; justify-content:center; }
.bmc-term-area { flex:1; min-height:0; background:#0b0b0d; padding:6px; }
.bmc-term { width:100%; height:100%; }
.bmc-term.dim { filter:grayscale(1) brightness(.55); pointer-events:none; }
</style>
