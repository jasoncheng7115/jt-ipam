<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NTag, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { listLibreNMS, testLibreNMS, syncLibreNMS, type LibreNMSInstance } from "@/api/integrations";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<LibreNMSInstance[]>([]);
const loading = ref(false);

async function refresh() {
  loading.value = true;
  try { rows.value = (await listLibreNMS()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function test(id: string) {
  try { await testLibreNMS(id); msg.success(t("librenms_admin.test_ok")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function sync(id: string) {
  try {
    const res = await syncLibreNMS(id);
    msg.success(t("librenms_admin.sync_summary", { summary: JSON.stringify(res).slice(0, 80) }));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const cols = computed<DataTableColumns<LibreNMSInstance>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "API URL", key: "api_url" },
  {
    title: t("common.status"), key: "enabled",
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: "interval", key: "sync_interval_seconds", render: (r) => `${r.sync_interval_seconds}s` },
  {
    title: "last sync", key: "last_sync_at",
    render: (r) => r.last_sync_at ? new Date(r.last_sync_at).toLocaleString() : "—",
  },
  { title: "last error", key: "last_error", render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions",
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => test(r.id) }, () => t("common.test")),
      h(NButton, { size: "small", type: "primary", onClick: () => sync(r.id) }, () => t("common.sync")),
    ]),
  },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('librenms_admin.title')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false">
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>
  </n-card>
</template>
