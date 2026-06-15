<script setup lang="ts">
/**
 * 我的 AI 對話歷程：與管理員版面相同，但只看自己的對話（非管理員也可用）。
 * 不含保留天數 / 立即清除等全域管理設定。
 */
import { computed, h, onMounted, ref } from "vue";
import {
  NButton, NCard, NDataTable, NIcon, NModal, NPopconfirm,
  NSpace, NSpin, useMessage, type DataTableColumns,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import {
  listMyConversations, getConversation, deleteConversation,
  type ConversationSummary, type ConversationDetail,
} from "@/api/chat";
import { renderMarkdown } from "@/utils/markdown";
import { fmtDateTime } from "@/utils/datetime";
import { autoSort } from "@/composables/useTableSort";
import { ChatHistoryIcon, EyeIcon, DeleteIcon, RefreshIcon } from "@/icons";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();

const { t } = useI18n();
const msg = useMessage();

const rows = ref<ConversationSummary[]>([]);
const loading = ref(false);
const detail = ref<ConversationDetail | null>(null);
const detailOpen = ref(false);

onMounted(load);

async function load() {
  loading.value = true;
  try {
    rows.value = await listMyConversations();
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function view(row: ConversationSummary) {
  try {
    detail.value = await getConversation(row.id);
    detailOpen.value = true;
  } catch {
    msg.error(t("errors.network"));
  }
}

async function remove(id: string) {
  try {
    await deleteConversation(id);
    await load();
  } catch {
    msg.error(t("errors.network"));
  }
}

const columns = computed<DataTableColumns<ConversationSummary>>(() => autoSort<ConversationSummary>([
  { title: t("chat_admin.title"), key: "title", minWidth: 280, ellipsis: { tooltip: true },
    render: (r) => r.title || t("chat.untitled") },
  { title: t("chat_admin.messages"), key: "message_count", width: 90, align: "center" },
  { title: t("chat_admin.updated"), key: "updated_at", width: 180,
    render: (r) => fmtDateTime(r.updated_at) },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 96,
    titleAlign: "center", align: "center",
    render: (r) => h(NSpace, { size: 4, justify: "center", wrapItem: false }, () => [
      h(NButton, { size: "small", quaternary: true, onClick: () => view(r) },
        { icon: () => h(NIcon, null, () => h(EyeIcon)) }),
      h(NPopconfirm, { onPositiveClick: () => remove(r.id) }, {
        trigger: () => h(NButton, { size: "small", quaternary: true, type: "error" },
          { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center">
        <n-icon :size="22"><ChatHistoryIcon /></n-icon>
        <span>{{ t("my_chat_history.title") }}</span>
      </n-space>
    </template>
    <template #header-extra>
      <n-button size="small" :loading="loading" @click="load">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
    </template>

    <n-spin :show="loading">
      <n-data-table :columns="columns" :data="rows" :bordered="false" :scroll-x="640"
        :pagination="pg" />
    </n-spin>
  </n-card>

  <n-modal v-model:show="detailOpen" preset="card" style="width: 720px; max-width: 92vw"
    :title="detail?.title || t('chat.untitled')">
    <div v-if="detail" class="conv-view">
      <div v-for="(m, i) in detail.messages" :key="i" class="conv-bubble" :class="m.role">
        <strong>{{ m.role === 'user' ? t('chat_admin.role_user') : 'AI' }}</strong>
        <pre v-if="m.role === 'user'">{{ m.content }}</pre>
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div v-else class="md" v-html="renderMarkdown(m.content)"></div>
        <div v-if="m.created_at || m.model || m.elapsed_ms != null" class="conv-meta">
          <span v-if="m.created_at">{{ fmtDateTime(m.created_at) }}</span>
          <span v-if="m.created_at && (m.model || m.elapsed_ms != null)"> · </span>
          <span v-if="m.model">{{ m.model }}</span>
          <span v-if="m.model && m.elapsed_ms != null"> · </span>
          <span v-if="m.elapsed_ms != null">{{ (m.elapsed_ms / 1000).toFixed(1) }}s</span>
        </div>
      </div>
    </div>
  </n-modal>
</template>

<style scoped>
.conv-view { max-height: 60vh; overflow-y: auto; }
.conv-bubble { margin: 8px 0; padding: 8px 12px; border-radius: 8px; font-size: 14px; }
.conv-bubble.user { background: rgba(59, 130, 246, 0.1); }
.conv-bubble.assistant { background: rgba(128, 128, 128, 0.08); }
.conv-bubble pre { white-space: pre-wrap; margin: 4px 0 0; font-family: inherit; }
.conv-meta { margin-top: 4px; font-size: 11px; opacity: 0.5; font-family: ui-monospace, monospace; }
</style>
