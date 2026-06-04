<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NCard, NSpace, NIcon, NTag, NButton, NAlert, NDescriptions, NDescriptionsItem, useMessage } from "naive-ui";
import { apiClient } from "@/api/client";
import { DevicesIcon, RefreshIcon } from "@/icons";
import { fmtDateTime } from "@/utils/datetime";

const { t } = useI18n();
const msg = useMessage();

const stats = ref<{ count: number; last_updated: string | null } | null>(null);
const refreshing = ref(false);

async function loadStats() {
  try {
    const { data } = await apiClient.get("/api/v1/oui/stats");
    stats.value = data;
  } catch { /* silent */ }
}

async function refreshDb() {
  refreshing.value = true;
  try {
    const { data } = await apiClient.post("/api/v1/oui/refresh");
    msg.success(t("tools_page.oui_refresh_ok", { inserted: data.inserted, updated: data.updated, parsed: data.parsed }));
    await loadStats();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("tools_page.oui_refresh_fail"));
  } finally {
    refreshing.value = false;
  }
}

onMounted(() => { void loadStats(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><DevicesIcon /></n-icon>
        <span>{{ t("oui_admin.title") }}</span>
      </n-space>
    </template>

    <n-space vertical :size="16" style="max-width: 720px">
      <n-alert type="info" :show-icon="true">
        {{ t('tools_page.oui_alert_pre') }} <code>manuf</code> {{ t('tools_page.oui_alert_post') }}
        {{ t('tools_page.oui_alert_note') }}
        <div style="margin-top: 6px">{{ t('oui_admin.schedule_note') }}</div>
      </n-alert>

      <n-descriptions bordered :column="1" size="small" label-align="right" label-placement="left">
        <n-descriptions-item :label="t('tools_page.oui_db_count')">
          <n-tag type="info">{{ stats?.count?.toLocaleString() ?? "—" }}</n-tag>
        </n-descriptions-item>
        <n-descriptions-item :label="t('tools_page.oui_last_updated')">
          {{ fmtDateTime(stats?.last_updated) }}
        </n-descriptions-item>
      </n-descriptions>

      <n-space>
        <n-button type="primary" :loading="refreshing" @click="refreshDb">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>
          {{ t('tools_page.oui_refresh_now') }}
        </n-button>
      </n-space>
    </n-space>
  </n-card>
</template>
