<script setup lang="ts">
/**
 * 浮動 AI 聊天視窗（規格 §11.1 — UI 右下角浮動按鈕）。
 *
 * 規格 §11.1：本地推論不外送 — 後端走 Ollama，本元件只與後端 /api/v1/ai/chat 通訊。
 */
import { computed, nextTick, ref, watch } from "vue";
import {
  NButton,
  NCard,
  NInput,
  NSpace,
  NSpin,
  NTag,
  NText,
  useMessage,
} from "naive-ui";
import { chat, type ChatMessage } from "@/api/chat";

const open = ref(false);
const input = ref("");
const messages = ref<ChatMessage[]>([]);
const loading = ref(false);
const trace = ref<ChatMessage[]>([]);
const showTrace = ref(false);
const msg = useMessage();
const scrollEl = ref<HTMLDivElement | null>(null);

const visibleMessages = computed(() =>
  messages.value.filter((m) => m.role === "user" || m.role === "assistant"),
);

async function send() {
  if (!input.value.trim()) return;
  const userMsg: ChatMessage = { role: "user", content: input.value.trim() };
  messages.value.push(userMsg);
  input.value = "";
  loading.value = true;
  await scroll();
  try {
    const r = await chat(
      messages.value.filter((m) => ["user", "assistant", "system"].includes(m.role)),
      4,
    );
    messages.value.push({ role: "assistant", content: r.answer || "(no answer)" });
    trace.value = r.trace_messages;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Chat failed");
  } finally {
    loading.value = false;
    await scroll();
  }
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
}
</script>

<template>
  <button
    v-if="!open"
    class="chat-fab"
    title="AI 助手"
    @click="open = true"
  >
    🤖
  </button>

  <div v-if="open" class="chat-shell">
    <n-card size="small" :bordered="false">
      <template #header>
        <n-space align="center">
          <span>jt-ipam AI</span>
          <n-tag size="tiny" type="info">本地 Ollama</n-tag>
        </n-space>
      </template>
      <template #header-extra>
        <n-space>
          <n-button text size="tiny" @click="showTrace = !showTrace">
            {{ showTrace ? "Hide trace" : "Trace" }}
          </n-button>
          <n-button text size="tiny" @click="reset">Reset</n-button>
          <n-button text size="tiny" @click="open = false">×</n-button>
        </n-space>
      </template>

      <div ref="scrollEl" class="chat-scroll">
        <div
          v-for="(m, i) in visibleMessages"
          :key="i"
          class="bubble"
          :class="m.role"
        >
          <strong v-if="m.role === 'user'">You</strong>
          <strong v-else>AI</strong>
          <pre>{{ m.content }}</pre>
        </div>
        <n-spin v-if="loading" size="small" style="margin: 8px 0" />
      </div>

      <details v-if="showTrace && trace.length" class="trace">
        <summary>Tool trace ({{ trace.length }} messages)</summary>
        <pre>{{ JSON.stringify(trace, null, 2) }}</pre>
      </details>

      <n-space style="margin-top: 8px">
        <n-input
          v-model:value="input"
          placeholder="例：列出 192.168.1.0/24 的使用率；找空閒的 10.0.0.0/24 的 IP；trace MAC 00:11:22:33:44:55"
          type="textarea"
          :rows="2"
          :disabled="loading"
          @keydown.enter.exact.prevent="send"
        />
        <n-button type="primary" :loading="loading" @click="send">Send</n-button>
      </n-space>
      <n-text depth="3" style="font-size: 11px">
        Enter 送出 · Shift+Enter 換行 · 對話不會送到外部，全部本地推論
      </n-text>
    </n-card>
  </div>
</template>

<style scoped>
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
  width: 420px;
  max-height: 70vh;
  z-index: 9000;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.25);
  background: var(--n-card-color, white);
}
.chat-scroll {
  max-height: 340px;
  overflow-y: auto;
  padding: 8px 0;
}
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
.bubble pre {
  margin: 4px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
}
.trace pre {
  font-size: 10px;
  max-height: 240px;
  overflow: auto;
  background: rgba(127, 127, 127, 0.06);
  padding: 8px;
  border-radius: 4px;
}
</style>
