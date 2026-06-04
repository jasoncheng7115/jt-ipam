<script setup lang="ts">
import { ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NAlert, NStatistic, NGrid, NGi, NCode,
  useMessage,
} from "naive-ui";
import { runAnomalyScan, type AnomalyReport } from "@/api/phase3";
import {
  AnomalyIcon, TestIcon, InfoIcon,
} from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const loading = ref(false);
const report = ref<AnomalyReport | null>(null);
const lastRunAt = ref<string | null>(null);

async function run() {
  loading.value = true;
  try {
    report.value = await runAnomalyScan();
    lastRunAt.value = fmtDateTime(new Date());
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><AnomalyIcon /></n-icon>
        <span>{{ t("anomaly.title") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px">
      <n-button type="primary" :loading="loading" @click="run">
        <template #icon><n-icon><TestIcon /></n-icon></template>
        {{ t("anomaly.run_scan") }}
      </n-button>
      <span v-if="lastRunAt" style="opacity: 0.7">
        {{ t("anomaly.last_run") }}: {{ lastRunAt }}
      </span>
    </n-space>

    <n-alert v-if="!report" type="info">
      <template #icon><n-icon><InfoIcon /></n-icon></template>
      {{ t("anomaly.help") }}
    </n-alert>

    <template v-if="report">
      <n-grid :cols="4" x-gap="12" style="margin-bottom: 16px">
        <n-gi>
          <n-statistic :label="t('anomaly.ip_conflicts')" :value="report.ip_conflicts.length" />
        </n-gi>
        <n-gi>
          <n-statistic :label="t('anomaly.mac_drifts')" :value="report.mac_drifts.length" />
        </n-gi>
        <n-gi>
          <n-statistic :label="t('anomaly.ghost_ips')" :value="report.ghost_ips.length" />
        </n-gi>
        <n-gi>
          <n-statistic :label="t('anomaly.unauthorized')" :value="report.unauthorized_ips.length" />
        </n-gi>
      </n-grid>
      <n-card :title="t('anomaly.raw_report')" size="small">
        <n-code :code="JSON.stringify(report, null, 2)" language="json" />
      </n-card>
    </template>
  </n-card>
</template>
