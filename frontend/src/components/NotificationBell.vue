<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import {
  NPopover,
  NButton,
  NBadge,
  NIcon,
  NList,
  NListItem,
  NEmpty,
  NText,
  NSpace,
} from "naive-ui";
import { listNotifications, markAllRead, markRead, type Notification } from "@/api/notifications";
import { BellIcon, CheckIcon } from "@/icons";
import { fmtDateTime, fmtRelative } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";

const { t } = useI18n();
const router = useRouter();

// 有 i18n key 就依當前語言渲染（帶參數）；沒有則退回存起來的原字串（向下相容舊通知）
function dispTitle(n: Notification): string {
  return n.title_key ? t(n.title_key, (n.params || {}) as Record<string, unknown>) : n.title;
}
function dispBody(n: Notification): string {
  return n.body_key ? t(n.body_key, (n.params || {}) as Record<string, unknown>) : (n.body || "");
}

const items = ref<Notification[]>([]);
const unread = ref(0);
let timer: number | null = null;

async function refresh() {
  try {
    const r = await listNotifications(false, 1, 20);
    items.value = r.items;
    unread.value = items.value.filter((n) => !n.read_at).length;
  } catch {
    // silent — bell fails open
  }
}

async function clickItem(n: Notification) {
  if (!n.read_at) {
    try {
      await markRead(n.id);
      n.read_at = new Date().toISOString();
      unread.value = Math.max(unread.value - 1, 0);
    } catch {
      // ignore
    }
  }
}

async function clearAll() {
  await markAllRead();
  await refresh();
}

function goAll() {
  void router.push({ name: "notifications" });
}

onMounted(() => {
  void refresh();
  timer = window.setInterval(refresh, 60_000);
});

onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer);
});
</script>

<template>
  <n-popover trigger="click" placement="bottom-end" style="width: 360px" :show-arrow="false">
    <template #trigger>
      <n-button text :focusable="false" aria-label="notifications"
                style="display: flex; align-items: center;">
        <n-badge :value="unread" :max="99" :show="unread > 0" :offset="[2, -2]"
                 style="display: flex; align-items: center;">
          <n-icon :size="20" :class="{ 'bell-active': unread > 0 }"
                  style="vertical-align: middle;"><BellIcon /></n-icon>
        </n-badge>
      </n-button>
    </template>
    <n-space vertical :size="8">
      <n-space justify="space-between" align="center">
        <n-space :size="6" align="center" :wrap-item="false">
          <n-icon :size="16"><BellIcon /></n-icon>
          <strong>{{ t("notifications.title") }}</strong>
        </n-space>
        <n-button v-if="unread > 0" size="tiny" @click="clearAll">
          <template #icon><n-icon><CheckIcon /></n-icon></template>
          {{ t("notifications.mark_all_read") }}
        </n-button>
      </n-space>
      <div class="notif-scroll">
        <n-list v-if="items.length" hoverable>
          <n-list-item
            v-for="n in items"
            :key="n.id"
            style="cursor: pointer"
            :class="{ unread: !n.read_at }"
            @click="clickItem(n)"
          >
            <n-space vertical :size="2" style="width: 100%">
              <strong class="notif-text">{{ dispTitle(n) }}</strong>
              <n-text v-if="dispBody(n)" depth="3" class="notif-text" style="font-size: 12px">{{ dispBody(n) }}</n-text>
              <n-text depth="3" style="font-size: 11px" :title="fmtDateTime(n.created_at)">{{ fmtRelative(n.created_at) }}</n-text>
            </n-space>
          </n-list-item>
        </n-list>
        <n-empty v-else size="small" :description="t('notifications.empty')" />
      </div>
      <div style="text-align: center; border-top: 1px solid var(--n-divider-color); padding-top: 6px">
        <n-button text size="small" @click="goAll">{{ t("notifications.view_all") }}</n-button>
      </div>
    </n-space>
  </n-popover>
</template>

<style scoped>
/* 通知過多時內部捲動，不讓彈窗長過畫面（標題與「查看全部」維持固定可見） */
.notif-scroll {
  max-height: min(60vh, 460px);
  overflow-y: auto;
  margin: 0 -2px;
}
.unread {
  background: rgba(64, 128, 255, 0.06);
}
/* 標題 / 內容過長時換行，避免撐破彈窗 */
.notif-text {
  display: block;
  word-break: break-word;
  overflow-wrap: anywhere;
  white-space: normal;
}
/* 有未讀時通知圖示本身也變色（不只紅色數字） */
.bell-active {
  color: #f0a020;
  animation: bell-pulse 1.6s ease-in-out infinite;
}
@keyframes bell-pulse {
  0%, 100% { transform: rotate(0); }
  10% { transform: rotate(-12deg); }
  20% { transform: rotate(10deg); }
  30% { transform: rotate(-6deg); }
  40% { transform: rotate(4deg); }
  50% { transform: rotate(0); }
}
</style>
