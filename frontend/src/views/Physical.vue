<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NTabs, NTabPane, NDataTable, NSpace, NButton, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { Physical } from "@/api/phase3";

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"cabling" | "power" | "vpn">("cabling");

const cables = ref<any[]>([]);
const panels = ref<any[]>([]);
const feeds = ref<any[]>([]);
const outlets = ref<any[]>([]);
const vpns = ref<any[]>([]);
const loading = ref(false);

async function refresh() {
  loading.value = true;
  try {
    [cables.value, panels.value, feeds.value, outlets.value, vpns.value]
      = await Promise.all([
        Physical.cables(), Physical.panels(), Physical.feeds(),
        Physical.outlets(), Physical.vpns(),
      ]);
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

const cableCols = computed<DataTableColumns<any>>(() => [
  { title: "type", key: "type" },
  { title: t("common.status"), key: "status" },
  { title: t("sections.description"), key: "description" },
]);
const panelCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("nav.locations"), key: "location_id", render: (r: any) => r.location_id ?? "—" },
]);
const feedCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "panel", key: "panel_id" },
]);
const outletCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "feed", key: "feed_id" },
]);
const vpnCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "type", key: "type" },
  { title: t("common.status"), key: "status" },
]);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('nav.physical')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
    </n-space>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="cabling" :tab="`${t('physical.cabling')} (${cables.length})`">
        <n-data-table :columns="cableCols" :data="cables" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane name="power" :tab="t('physical.power')">
        <h3>{{ t("physical.panels") }} ({{ panels.length }})</h3>
        <n-data-table :columns="panelCols" :data="panels" :loading="loading" :bordered="false" />
        <h3 style="margin-top: 16px">{{ t("physical.feeds") }} ({{ feeds.length }})</h3>
        <n-data-table :columns="feedCols" :data="feeds" :loading="loading" :bordered="false" />
        <h3 style="margin-top: 16px">{{ t("physical.outlets") }} ({{ outlets.length }})</h3>
        <n-data-table :columns="outletCols" :data="outlets" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane name="vpn" :tab="`VPN (${vpns.length})`">
        <n-data-table :columns="vpnCols" :data="vpns" :loading="loading" :bordered="false" />
      </n-tab-pane>
    </n-tabs>
  </n-card>
</template>
