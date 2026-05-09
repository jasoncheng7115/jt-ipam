<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NTag, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { listPlugins, type PluginInfo } from "@/api/integrations";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<PluginInfo[]>([]);
const count = ref(0);
const loading = ref(false);

async function refresh() {
  loading.value = true;
  try {
    const r = await listPlugins();
    rows.value = r.plugins;
    count.value = r.count;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
const cols = computed<DataTableColumns<PluginInfo>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("plugins_admin.version"), key: "version", render: (r) => r.version ?? "—" },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
  {
    title: t("common.status"), key: "error",
    render: (r) => r.error
      ? h(NTag, { size: "small", type: "error" }, () => "error")
      : h(NTag, { size: "small", type: "success" }, () => t("plugins_admin.loaded")),
  },
  { title: t("common.fail"), key: "error_msg", render: (r) => r.error ?? "" },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="`${t('plugins_admin.title')} (${count})`">
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
