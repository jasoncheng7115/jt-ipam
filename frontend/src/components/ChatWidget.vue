<script setup lang="ts">
/**
 * 浮動 AI 聊天視窗 (規格 §11.1 — UI 右下角浮動按鈕)。
 *
 * 規格 §11.1：本地推論不外送 — 後端走 Ollama，本元件只與後端 /api/v1/ai/chat 通訊。
 */
import { computed, nextTick, ref, watch } from "vue";
import {
  NButton,
  NCard,
  NEmpty,
  NIcon,
  NInput,
  NSpace,
  NSpin,
  NTag,
  NTooltip,
  useMessage,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  chatStream, confirmAction, listMyConversations, getConversation, deleteConversation, getModelInfo,
  type ChatMessage, type ChatPageContext, type ConversationSummary, type ModelInfo, type PendingAction,
} from "@/api/chat";
import { fmtRelative, fmtDateTime } from "@/utils/datetime";
import { BubbleStar } from "@iconoir/vue";
import { CancelIcon, SendIcon, ChatHistoryIcon, ToolsIcon, RefreshIcon, WarnIcon } from "@/icons";
import { useAuthStore } from "@/stores/auth";
import { renderMarkdown } from "@/utils/markdown";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const userLabel = computed(() => auth.me?.username || "You");

// 把目前所在頁面 (子網路 / 裝置 / 區段) 帶給 AI，讓它在沒指定網段時自動沿用
const pageContext = computed<ChatPageContext>(() => {
  const id = route.params.id as string | undefined;
  if (!id) return {};
  if (route.name === "subnet-detail") return { subnet_id: id };
  if (route.name === "device-detail") return { device_id: id };
  if (route.name === "section-detail") return { section_id: id };
  return {};
});

// UI 端訊息：除了 role/content 還記每則回應的 model 與耗時（不回傳給後端）
type ChatRef = { type: string; id: string; label: string };
type UiMessage = ChatMessage & {
  model?: string | null; elapsedMs?: number | null; ts?: string | null; refs?: ChatRef[];
  pendingActions?: PendingAction[];   // AI 想執行的異動，待使用者確認
  actionDone?: string;                // 確認後的結果摘要（已執行）
};

// 從工具呼叫結果（trace）擷取被提到的物件 → 答案下方給可點連結
function extractRefs(trace: ChatMessage[] | undefined): ChatRef[] {
  const refs: ChatRef[] = [];
  const seen = new Set<string>();
  const add = (type: string, id: any, label: any) => {
    if (!id || !label) return;
    const k = `${type}:${id}`;
    if (seen.has(k)) return;
    seen.add(k);
    refs.push({ type, id: String(id), label: String(label) });
  };
  for (const m of trace ?? []) {
    if (m.role !== "tool" || typeof m.content !== "string") continue;
    let d: any;
    try { d = JSON.parse(m.content); } catch { continue; }
    if (!d || typeof d !== "object") continue;
    for (const rk of d.racks ?? []) add("rack", rk.id, rk.name);
    for (const dv of d.devices ?? []) add("device", dv.id, dv.name);
    for (const sn of d.subnets ?? []) add("subnet", sn.id, sn.cidr);
    for (const lo of d.locations ?? []) add("location", lo.id, lo.name);
    // 單筆詳情
    if (d.id && d.name && d.type && ("u_position" in d || "vendor" in d)) add("device", d.id, d.name);
    if (d.subnet_id && d.cidr) add("subnet", d.subnet_id, d.cidr);
  }
  return refs.slice(0, 12);
}

function goRef(r: ChatRef) {
  switch (r.type) {
    case "rack": router.push({ name: "racks" }); break;
    case "device": router.push({ name: "device-detail", params: { id: r.id } }); break;
    case "subnet": router.push({ name: "subnet-detail", params: { id: r.id } }); break;
    case "location": router.push({ name: "locations" }); break;
    case "ip": router.push({ name: "addresses", query: { q: r.label } }); break;
  }
  open.value = false;
}

const open = ref(false);
const input = ref("");
const messages = ref<UiMessage[]>([]);
const loading = ref(false);
// 最近一次回應用的 model（給 badge tooltip 顯示）
const lastModel = computed<string | null>(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].role === "assistant" && messages.value[i].model) return messages.value[i].model ?? null;
  }
  return null;
});
function fmtElapsed(ms?: number | null): string {
  if (ms == null) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}
// 模型參數（hover badge 顯示「名稱與參數」）
const modelInfo = ref<ModelInfo | null>(null);
watch(lastModel, async (m) => {
  if (!m) { modelInfo.value = null; return; }
  if (modelInfo.value?.model === m) return;
  try { modelInfo.value = await getModelInfo(m); } catch { modelInfo.value = null; }
}, { immediate: true });
const modelTip = computed(() => {
  if (!lastModel.value) return t("chat.model_tip_none");
  const mi = modelInfo.value;
  const parts: string[] = [];
  if (mi?.parameter_size) parts.push(mi.parameter_size);
  if (mi?.quantization) parts.push(mi.quantization);
  if (mi?.context_length) parts.push(`ctx ${mi.context_length.toLocaleString()}`);
  const detail = parts.length ? "\n" + parts.join(" · ") : "";
  return t("chat.model_tip", { model: lastModel.value }) + detail;
});
const partial = ref("");        // 串流中累積的最終答案
const toolStatus = ref("");     // 正在執行的工具提示
const trace = ref<ChatMessage[]>([]);
const showTrace = ref(false);
const conversationId = ref<string | null>(null);   // 多輪同一段對話 → 後端 append
const showHistory = ref(false);
// 檢視歷程對話時，把「進行中的對話」暫存起來，回到對話 / 送新問題時還原
const savedLive = ref<{ messages: UiMessage[]; conversationId: string | null; trace: ChatMessage[] } | null>(null);
function backToLive() {
  if (savedLive.value) {
    messages.value = savedLive.value.messages;
    conversationId.value = savedLive.value.conversationId;
    trace.value = savedLive.value.trace;
    savedLive.value = null;
  }
  showHistory.value = false;
}
const history = ref<ConversationSummary[]>([]);
const historyLoading = ref(false);
const msg = useMessage();
const scrollEl = ref<HTMLDivElement | null>(null);

const visibleMessages = computed(() =>
  messages.value.filter((m) => m.role === "user" || m.role === "assistant"),
);

async function send() {
  if (!input.value.trim() || loading.value) return;
  backToLive();   // 送新問題 → 自動切回「進行中的對話」（不續寫正在檢視的歷程）
  const userMsg: UiMessage = { role: "user", content: input.value.trim(), ts: new Date().toISOString() };
  messages.value.push(userMsg);
  input.value = "";
  loading.value = true;
  partial.value = "";
  toolStatus.value = "";
  const startTs = Date.now();
  await scroll();
  try {
    await chatStream(
      // 只送 role/content（後端 StrictModel 不收多餘欄位）
      messages.value
        .filter((m) => ["user", "assistant", "system"].includes(m.role))
        .map((m) => ({ role: m.role, content: m.content })),
      4,
      (ev) => {
        if (ev.type === "token") {
          partial.value += ev.text;
          void scroll();
        } else if (ev.type === "tool") {
          toolStatus.value = t("chat.tool_running", { name: ev.name });
        } else if (ev.type === "tool_round") {
          partial.value = "";   // 該輪非最終答案，清掉暫存
        } else if (ev.type === "pending_action") {
          // AI 想做異動 → 不自動執行，掛在訊息上等使用者確認
          messages.value.push({
            role: "assistant",
            content: ev.actions.length ? t("chat.confirm_intro") : "",
            pendingActions: ev.actions,
            ts: new Date().toISOString(),
          });
          partial.value = "";
          toolStatus.value = "";
          void scroll();
        } else if (ev.type === "done") {
          messages.value.push({
            role: "assistant",
            content: ev.answer || t("chat.no_answer"),
            model: ev.model ?? null,
            // 後端有給就用後端的 LLM 耗時；否則用前端量到的整體耗時兜底
            elapsedMs: ev.elapsed_ms ?? (Date.now() - startTs),
            ts: new Date().toISOString(),
            refs: extractRefs(ev.trace_messages),
          });
          trace.value = ev.trace_messages;
          if (ev.conversation_id) conversationId.value = ev.conversation_id;
          partial.value = "";
          toolStatus.value = "";
        } else if (ev.type === "error") {
          msg.error(ev.detail || "Chat failed");
        }
      },
      undefined,
      pageContext.value,
      conversationId.value,
    );
  } catch (e: any) {
    msg.error(e?.message ?? "Chat failed");
  } finally {
    loading.value = false;
    partial.value = "";
    toolStatus.value = "";
    await scroll();
  }
}

const confirming = ref(false);
async function confirmPending(m: UiMessage, a: PendingAction) {
  if (confirming.value) return;
  confirming.value = true;
  try {
    const r = await confirmAction(a.tool, a.args);
    m.pendingActions = [];                       // 收掉確認卡
    m.actionDone = r.title || a.title;
    messages.value.push({
      role: "assistant",
      content: t("chat.confirm_done", { what: r.title || a.title }),
      ts: new Date().toISOString(),
    });
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("chat.confirm_fail"));
  } finally {
    confirming.value = false;
    await scroll();
  }
}
function cancelPending(m: UiMessage) {
  m.pendingActions = [];
  m.actionDone = t("chat.confirm_cancelled");
}

async function scroll() {
  await nextTick();
  if (scrollEl.value) {
    scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
  }
}

function reset() {
  messages.value = [];
  trace.value = [];
  showTrace.value = false;
  conversationId.value = null;   // 新對話
  savedLive.value = null;
}

async function toggleHistory() {
  if (showHistory.value) { backToLive(); return; }   // 關閉歷程清單 = 回到進行中的對話
  showHistory.value = true;
  await loadHistory();
}
async function loadHistory() {
  historyLoading.value = true;
  try {
    history.value = await listMyConversations();
  } catch { /* silent */ } finally {
    historyLoading.value = false;
  }
}
async function openConversation(id: string) {
  try {
    const conv = await getConversation(id);
    // 第一次從進行中對話點進歷程 → 先暫存進行中對話，之後可「回到對話」還原
    if (!savedLive.value) {
      savedLive.value = { messages: messages.value, conversationId: conversationId.value, trace: trace.value };
    }
    messages.value = conv.messages.map((m) => ({
      role: m.role as ChatMessage["role"],
      content: m.content,
      model: m.model,
      elapsedMs: m.elapsed_ms,
      ts: m.created_at,
    }));
    conversationId.value = conv.id;
    showHistory.value = false;
    await scroll();
  } catch {
    msg.error(t("errors.network"));
  }
}
async function removeConversation(id: string) {
  try {
    await deleteConversation(id);
    if (conversationId.value === id) reset();
    await loadHistory();
  } catch {
    msg.error(t("errors.network"));
  }
}
</script>

<template>
  <button
    v-if="!open"
    class="chat-fab"
    :title="t('chat.assistant')"
    @click="open = true"
  >
    <n-icon :size="26"><BubbleStar /></n-icon>
  </button>

  <div v-if="open" class="chat-shell">
    <n-card size="small" :bordered="false">
      <template #header>
        <n-space class="chat-title-row" align="center" :size="6" :wrap="false">
          <span class="chat-title">jt-ipam AI</span>
          <n-tooltip :z-index="10001">
            <template #trigger>
              <n-tag size="tiny" type="info" :bordered="false" class="chat-badge">{{ t("chat.local_badge") }}</n-tag>
            </template>
            <div style="white-space:pre-line">{{ modelTip }}</div>
          </n-tooltip>
        </n-space>
      </template>
      <template #header-extra>
        <div class="chat-actions">
          <!-- 三顆動作鈕收進一個有外框 + 分隔線的分段控制，明顯看得出是按鈕 -->
          <div class="chat-seg">
            <n-button text size="small" class="seg-btn" :title="showHistory ? t('chat.hide_history') : t('chat.history')" @click="toggleHistory">
              <template #icon><n-icon><ChatHistoryIcon /></n-icon></template>
              <span class="chat-act-label">{{ showHistory ? t("chat.hide_history") : t("chat.history") }}</span>
            </n-button>
            <n-button text size="small" class="seg-btn" :title="showTrace ? t('chat.hide_trace') : t('chat.trace')" @click="showTrace = !showTrace">
              <template #icon><n-icon><ToolsIcon /></n-icon></template>
              <span class="chat-act-label">{{ showTrace ? t("chat.hide_trace") : t("chat.trace") }}</span>
            </n-button>
            <n-button text size="small" class="seg-btn" :title="t('chat.reset')" @click="reset">
              <template #icon><n-icon><RefreshIcon /></n-icon></template>
              <span class="chat-act-label">{{ t("chat.reset") }}</span>
            </n-button>
          </div>
          <n-button quaternary circle size="small" :title="t('common.cancel')" @click="open = false">
            <template #icon><n-icon :size="18"><CancelIcon /></n-icon></template>
          </n-button>
        </div>
      </template>

      <div v-if="showHistory" class="chat-history">
        <n-spin v-if="historyLoading" size="small" style="margin: 8px" />
        <n-empty v-else-if="!history.length" :description="t('chat.no_history')" size="small" style="margin: 12px" />
        <div v-for="c in history" v-else :key="c.id" class="hist-row">
          <div class="hist-main" @click="openConversation(c.id)">
            <div class="hist-title">{{ c.title || t("chat.untitled") }}</div>
            <div class="hist-meta">{{ fmtRelative(c.updated_at) }} · {{ c.message_count ?? 0 }}</div>
          </div>
          <n-button text size="tiny" type="error" @click.stop="removeConversation(c.id)">×</n-button>
        </div>
      </div>

      <div v-show="!showHistory" ref="scrollEl" class="chat-scroll">
        <div
          v-for="(m, i) in visibleMessages"
          :key="i"
          class="bubble"
          :class="m.role"
        >
          <strong v-if="m.role === 'user'">{{ userLabel }}</strong>
          <strong v-else>AI</strong>
          <pre v-if="m.role === 'user'">{{ m.content }}</pre>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div v-else class="md" v-html="renderMarkdown(m.content)"></div>
          <!-- 異動確認卡：AI 想新增/修改/刪除，需使用者按確認 -->
          <div v-if="m.pendingActions && m.pendingActions.length" class="confirm-card">
            <div v-for="(a, ai) in m.pendingActions" :key="ai" class="confirm-row">
              <n-icon :size="15" class="confirm-ico"><WarnIcon /></n-icon>
              <span class="confirm-title">{{ a.title }}</span>
            </div>
            <div class="confirm-btns">
              <n-button size="small" type="primary" :loading="confirming"
                        @click="confirmPending(m, m.pendingActions[0])">
                {{ t("chat.confirm_do") }}
              </n-button>
              <n-button size="small" quaternary :disabled="confirming" @click="cancelPending(m)">
                {{ t("chat.confirm_cancel") }}
              </n-button>
            </div>
          </div>
          <div v-if="m.actionDone" class="confirm-done">✓ {{ m.actionDone }}</div>
          <div v-if="m.role === 'assistant' && m.refs && m.refs.length" class="msg-refs">
            <span class="msg-refs__label">{{ t("chat.related") }}</span>
            <n-tag v-for="r in m.refs" :key="r.type + r.id" size="small" type="info"
                   style="cursor: pointer" @click="goRef(r)">{{ r.label }}</n-tag>
          </div>
          <div v-if="m.ts || m.model || m.elapsedMs != null" class="msg-meta">
            <span v-if="m.ts">{{ fmtDateTime(m.ts) }}</span>
            <span v-if="m.ts && (m.model || m.elapsedMs != null)"> · </span>
            <span v-if="m.model">{{ m.model }}</span>
            <span v-if="m.model && m.elapsedMs != null"> · </span>
            <span v-if="m.elapsedMs != null">{{ fmtElapsed(m.elapsedMs) }}</span>
          </div>
        </div>
        <div v-if="loading && partial" class="bubble assistant">
          <strong>AI</strong>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div class="md" v-html="renderMarkdown(partial)"></div>
        </div>
        <div v-if="loading && toolStatus" class="tool-status">{{ toolStatus }}</div>
        <n-spin v-if="loading && !partial" size="small" style="margin: 8px 0" />
      </div>

      <details v-if="showTrace && trace.length" class="trace">
        <summary>{{ t("chat.tool_trace", { count: trace.length }) }}</summary>
        <pre>{{ JSON.stringify(trace, null, 2) }}</pre>
      </details>

      <div class="chat-input-row">
        <n-input
          v-model:value="input"
          :placeholder="t('chat.placeholder')"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          :disabled="loading"
          @keydown.enter.exact.prevent="send"
          style="flex: 1 1 auto; min-width: 0"
        />
        <n-button type="primary" :loading="loading" :disabled="!input.trim()"
                  @click="send" class="chat-send-btn">
          <template #icon><n-icon><SendIcon /></n-icon></template>
          {{ t("chat.send") }}
        </n-button>
      </div>
      <div class="chat-foot">
        <kbd>Enter</kbd> {{ t("chat.foot_send") }}
        · <kbd>Shift</kbd>+<kbd>Enter</kbd> {{ t("chat.foot_newline") }}
        · {{ t("chat.foot_local") }}
      </div>
    </n-card>
  </div>
</template>

<style scoped>
.chat-foot {
  font-size: 11px;
  margin-top: 6px;
  opacity: 0.7;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 3px;
}
.chat-foot kbd {
  display: inline-block;
  padding: 1px 5px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", monospace;
  font-size: 10px;
  line-height: 1.4;
  border: 1px solid rgba(127, 127, 127, 0.4);
  border-bottom-width: 2px;
  border-radius: 4px;
  background: rgba(127, 127, 127, 0.12);
}
.chat-fab {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  border: none;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: white;
  font-size: 24px;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
  z-index: 9000;
}
.chat-fab:hover {
  transform: scale(1.05);
}
.chat-shell {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: min(480px, calc(100vw - 48px));
  max-height: 70vh;
  z-index: 9000;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.35));
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.4);
  background: var(--n-card-color, white);
  display: flex;
  flex-direction: column;
  /* 讓 header 動作鈕能依「視窗實際寬度」決定要不要收成 icon */
  container-type: inline-size;
}
/* 標題：字體調小 + 不換行，避免把「本地 Ollama」標籤擠到第二行 */
/* 標題列與動作區放不下時整列換行（動作區掉到第二列），避免標題/標籤與按鈕重疊 */
.chat-shell :deep(.n-card-header) { flex-wrap: wrap; row-gap: 6px; column-gap: 8px; }
.chat-shell :deep(.n-card-header__main) { min-width: 0; }
.chat-title-row { flex-wrap: nowrap; min-width: 0; }
.chat-title { font-size: 15px; font-weight: 600; white-space: nowrap; }
.chat-badge { white-space: nowrap; flex: 0 0 auto; }

/* 標題列右側動作區：與 × 垂直置中 */
.chat-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
/* 三顆鈕的分段控制：外框 + 圓角，每顆之間一條分隔線，明顯是可按的群組 */
.chat-seg {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.32));
  border-radius: 8px;
  overflow: hidden;
}
.chat-seg :deep(.seg-btn) {
  border-radius: 0;
  height: 28px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  --n-color-hover: rgba(24, 160, 88, 0.12);
}
.chat-seg :deep(.seg-btn .n-button__content) { align-items: center; line-height: 1; }
.chat-seg :deep(.seg-btn + .seg-btn) {
  border-left: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.28));
}
/* 此 widget 寬度固定 ≤480px，動作鈕一律只留 icon（保留 tooltip 說明），
   避免「回到對話」等較長標籤（英文更長）把「本地 Ollama」標籤擠到重疊。
   分段外框 + 分隔線仍讓它明顯是一組可按的按鈕。 */
@container (max-width: 520px) {
  .chat-act-label { display: none; }
  .chat-seg :deep(.seg-btn) { padding: 0 9px; }
}
.chat-input-row {
  display: flex;
  align-items: stretch;
  gap: 8px;
  margin-top: 8px;
}
.chat-send-btn {
  flex: 0 0 auto;
  align-self: stretch;
  min-width: 64px;
}
.chat-scroll {
  max-height: 340px;
  overflow-y: auto;
  padding: 8px 0;
}
.msg-meta {
  margin-top: 4px;
  font-size: 11px;
  opacity: 0.55;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
/* 異動確認卡 */
.confirm-card {
  margin-top: 8px;
  border: 1px solid #f0a020;
  background: rgba(240, 160, 32, 0.08);
  border-radius: 8px;
  padding: 10px 12px;
}
.confirm-row { display: flex; align-items: center; gap: 6px; font-size: 13px; margin: 2px 0; }
.confirm-ico { color: #f0a020; flex: 0 0 auto; }
.confirm-title { font-weight: 600; }
.confirm-btns { display: flex; gap: 8px; margin-top: 8px; }
.confirm-done { margin-top: 6px; font-size: 12px; color: #18a058; font-weight: 600; }
/* AI 回應下方的相關物件可點連結 */
.msg-refs {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.msg-refs__label { font-size: 12px; opacity: 0.6; }
.chat-history {
  max-height: 340px;
  overflow-y: auto;
  padding: 4px 0;
}
.hist-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
}
.hist-row:hover { background: rgba(128, 128, 128, 0.1); }
.hist-main { flex: 1 1 auto; cursor: pointer; min-width: 0; }
.hist-title {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.hist-meta { font-size: 11px; opacity: 0.55; }
.bubble {
  margin: 6px 0;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 13px;
}
.bubble.user {
  background: rgba(99, 102, 241, 0.1);
}
.bubble.assistant {
  background: rgba(34, 197, 94, 0.08);
}
.tool-status {
  font-size: 12px;
  color: var(--n-text-color-3, #888);
  font-style: italic;
  margin: 4px 0;
  padding: 0 4px;
}
.bubble pre {
  margin: 4px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
}
.bubble .md {
  margin-top: 4px;
  line-height: 1.5;
}
.bubble .md :first-child { margin-top: 0; }
.bubble .md :last-child { margin-bottom: 0; }
.bubble .md p { margin: 4px 0; }
.bubble .md ul, .bubble .md ol { margin: 4px 0; padding-left: 20px; }
.bubble .md li { margin: 2px 0; }
.bubble .md h1, .bubble .md h2, .bubble .md h3,
.bubble .md h4, .bubble .md h5, .bubble .md h6 {
  margin: 6px 0 2px; font-size: 14px; font-weight: 600;
}
.bubble .md code {
  background: rgba(127, 127, 127, 0.16);
  padding: 1px 5px; border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
}
.bubble .md pre {
  background: rgba(127, 127, 127, 0.12);
  padding: 8px 10px; border-radius: 6px; overflow-x: auto; margin: 6px 0;
}
.bubble .md pre code { background: none; padding: 0; }
.bubble .md a { color: var(--primary-color, #18a058); }
.trace pre {
  font-size: 10px;
  max-height: 240px;
  overflow: auto;
  background: rgba(127, 127, 127, 0.06);
  padding: 8px;
  border-radius: 4px;
}
</style>
