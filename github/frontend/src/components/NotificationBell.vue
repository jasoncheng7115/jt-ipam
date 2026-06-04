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
import { BellIcon } from "@/icons";
import { useI18n } from "vue-i18n";

const { t } = useI18n();

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
          <n-icon :size="20" style="vertical-align: middle;"><BellIcon /></n-icon>
        </n-badge>
      </n-button>
    </template>
    <n-space vertical :size="8">
      <n-space justify="space-between" align="center">
        <strong>{{ t("notifications.title") }}</strong>
        <n-button v-if="unread > 0" size="tiny" @click="clearAll">{{ t("notifications.mark_all_read") }}</n-button>
      </n-space>
      <n-list v-if="items.length" hoverable>
        <n-list-item
          v-for="n in items"
          :key="n.id"
          style="cursor: pointer"
          :class="{ unread: !n.read_at }"
          @click="clickItem(n)"
        >
          <n-space vertical :size="2">
            <strong>{{ n.title }}</strong>
            <n-text v-if="n.body" depth="3" style="font-size: 12px">{{ n.body }}</n-text>
            <n-text depth="3" style="font-size: 11px">{{ n.created_at }}</n-text>
          </n-space>
        </n-list-item>
      </n-list>
      <n-empty v-else size="small" :description="t('notifications.empty')" />
    </n-space>
  </n-popover>
</template>

<style scoped>
.unread {
  background: rgba(64, 128, 255, 0.06);
}
</style>
