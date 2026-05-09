<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NCard, NSpace, NStatistic, NAlert } from "naive-ui";
import { listSections } from "@/api/sections";
import { listSubnets } from "@/api/subnets";
import { listAddresses } from "@/api/addresses";

const { t } = useI18n();

const sectionCount = ref<number | null>(null);
const subnetCount = ref<number | null>(null);
const addressCount = ref<number | null>(null);
const loadError = ref(false);

onMounted(async () => {
  try {
    const [s, sn, a] = await Promise.all([
      listSections(1, 1),
      listSubnets({ page: 1, pageSize: 1 }),
      listAddresses({ page: 1, pageSize: 1 }),
    ]);
    sectionCount.value = s.total;
    subnetCount.value = sn.total;
    addressCount.value = a.total;
  } catch {
    loadError.value = true;
  }
});
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="t('dashboard.title')">
      <p>{{ t("dashboard.welcome") }}</p>
      <p>{{ t("app.tagline") }}</p>
    </n-card>
    <n-space :size="16" wrap>
      <n-card style="min-width: 220px">
        <n-statistic label="Sections" :value="sectionCount ?? 0" />
      </n-card>
      <n-card style="min-width: 220px">
        <n-statistic label="Subnets" :value="subnetCount ?? 0" />
      </n-card>
      <n-card style="min-width: 220px">
        <n-statistic label="IP Addresses" :value="addressCount ?? 0" />
      </n-card>
    </n-space>
    <n-alert v-if="loadError" type="warning">
      {{ t("errors.network") }}
    </n-alert>
    <n-alert type="info" title="Phase 1 in progress">
      Subnet 視覺方塊圖、IP 配發 UI、TOTP 啟用頁將於 Phase 1 階段陸續完成。
    </n-alert>
  </n-space>
</template>
