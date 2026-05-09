<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NTag, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { listDNSServers, testDNSServer, type DNSServer } from "@/api/integrations";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<DNSServer[]>([]);
const loading = ref(false);

async function refresh() {
  loading.value = true;
  try { rows.value = (await listDNSServers()).items ?? []; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function test(id: string) {
  try { await testDNSServer(id); msg.success(t("librenms_admin.test_ok")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const cols = computed<DataTableColumns<DNSServer>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("dns_admin.type"), key: "type" },
  { title: t("dns_admin.endpoint"), key: "endpoint" },
  {
    title: t("common.status"), key: "enabled",
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: "auth", key: "is_authoritative", render: (r) => r.is_authoritative ? "✓" : "—" },
  {
    title: t("common.actions"), key: "actions",
    render: (r) => h(NButton, { size: "small", onClick: () => test(r.id) }, () => t("common.test")),
  },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('dns_admin.title')">
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
