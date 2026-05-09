<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { NCard, NSpace, NStatistic, NAlert } from "naive-ui";

const { t } = useI18n();
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="t('dashboard.title')">
      <p>{{ t("dashboard.welcome") }}</p>
      <p>{{ t("app.tagline") }}</p>
    </n-card>
    <n-space :size="16" wrap>
      <n-card style="min-width: 220px">
        <n-statistic label="Sections" :value="0" />
      </n-card>
      <n-card style="min-width: 220px">
        <n-statistic label="Subnets" :value="0" />
      </n-card>
      <n-card style="min-width: 220px">
        <n-statistic label="IP Addresses" :value="0" />
      </n-card>
      <n-card style="min-width: 220px">
        <n-statistic label="Devices" :value="0" />
      </n-card>
    </n-space>
    <n-alert type="info" title="Phase 1 in progress">
      首頁、Section、Subnet、IP 表格、視覺方塊圖將於 Phase 1 階段陸續完成。
    </n-alert>
  </n-space>
</template>
