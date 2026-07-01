<script setup lang="ts">
import { onMounted, ref } from "vue";
import { NCard, NList, NListItem, NSpace, NText, NTag, NButton, NPagination, NEmpty, useMessage } from "naive-ui";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { listNotifications, markRead, markAllRead, type Notification } from "@/api/notifications";
import { fmtDateTime, fmtRelative } from "@/utils/datetime";

const { t } = useI18n();
const router = useRouter();
const message = useMessage();

// 有 i18n key 就依當前語言渲染（帶參數）；沒有則退回原字串（向下相容舊通知）
function dispTitle(n: Notification): string {
  return n.title_key ? t(n.title_key, (n.params || {}) as Record<string, unknown>) : n.title;
}
function dispBody(n: Notification): string {
  return n.body_key ? t(n.body_key, (n.params || {}) as Record<string, unknown>) : (n.body || "");
}

const items = ref<Notification[]>([]);
const page = ref(1);
const pageSize = ref(50);
const total = ref(0);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    const r = await listNotifications(false, page.value, pageSize.value);
    items.value = r.items;
    total.value = r.total;
  } finally {
    loading.value = false;
  }
}

function sevType(s: string): "error" | "warning" | "info" | "success" | "default" {
  if (s === "critical" || s === "error") return "error";
  if (s === "warning") return "warning";
  if (s === "success") return "success";
  if (s === "info") return "info";
  return "default";
}

async function clickItem(n: Notification) {
  if (!n.read_at) {
    try {
      await markRead(n.id);
      n.read_at = new Date().toISOString();
    } catch {
      // ignore
    }
  }
  if (n.link) void router.push(n.link);
}

async function clearAll() {
  try {
    await markAllRead();
    message.success(t("notifications.mark_all_read"));
    await load();
  } catch (e: any) {
    message.error(e?.response?.data?.detail ?? t("errors.network"));
  }
}

function onPage(p: number) {
  page.value = p;
  void load();
}

onMounted(load);
</script>

<template>
  <n-card :title="t('notifications.history_title')">
    <template #header-extra>
      <n-button size="small" @click="clearAll">{{ t("notifications.mark_all_read") }}</n-button>
    </template>
    <n-list v-if="items.length" hoverable clickable>
      <n-list-item
        v-for="n in items"
        :key="n.id"
        :class="{ unread: !n.read_at }"
        @click="clickItem(n)"
      >
        <n-space vertical :size="2">
          <n-space align="center" :size="8">
            <n-tag :type="sevType(n.severity)" size="small" round>{{ n.severity }}</n-tag>
            <strong>{{ dispTitle(n) }}</strong>
          </n-space>
          <n-text v-if="dispBody(n)" depth="3" style="font-size: 13px">{{ dispBody(n) }}</n-text>
          <n-text depth="3" style="font-size: 12px" :title="fmtDateTime(n.created_at)">
            {{ fmtRelative(n.created_at) }}
          </n-text>
        </n-space>
      </n-list-item>
    </n-list>
    <n-empty v-else :description="t('notifications.empty')" style="padding: 32px 0" />
    <div v-if="total > pageSize" style="display: flex; justify-content: center; margin-top: 16px">
      <n-pagination
        :page="page"
        :page-size="pageSize"
        :item-count="total"
        @update:page="onPage"
      />
    </div>
  </n-card>
</template>

<style scoped>
.unread {
  background: rgba(64, 128, 255, 0.06);
}
</style>
