<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NTabs, NTabPane, NDataTable, NSpace, NIcon, NButton, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { VirtualizationIcon, RefreshIcon, SyncIcon } from "@/icons";
import { Virt } from "@/api/phase3";

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"clusters" | "vms" | "proxmox">("clusters");

const clusters = ref<any[]>([]);
const vms = ref<any[]>([]);
const proxmox = ref<any[]>([]);
const loading = ref(false);

async function refresh() {
  loading.value = true;
  try {
    [clusters.value, vms.value, proxmox.value]
      = await Promise.all([Virt.clusters(), Virt.vms(), Virt.proxmox()]);
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function syncProxmox(id: string) {
  try {
    await Virt.syncProxmox(id);
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const clusterCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "type", key: "type" },
  { title: t("sections.description"), key: "description" },
]);
const vmCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("virt.cluster"), key: "cluster_id",
    render: (r) => clusters.value.find((c) => c.id === r.cluster_id)?.name ?? "—" },
  {
    title: t("common.status"), key: "status",
    render: (r) => h(NTag, {
      size: "small",
      type: r.status === "running" ? "success" : r.status === "stopped" ? "default" : "warning",
    }, () => r.status ?? "—"),
  },
]);
const proxmoxCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "API URL", key: "api_url" },
  { title: "node", key: "node" },
  {
    title: t("common.status"), key: "enabled",
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "default" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: "last sync", key: "last_sync_at",
    render: (r) => r.last_sync_at ? new Date(r.last_sync_at).toLocaleString() : "—",
  },
  {
    title: t("common.actions"), key: "_", width: 100,
    render: (r) => h(NButton, { size: "small", type: "primary",
      onClick: () => syncProxmox(r.id) }, () => t("common.sync")),
  },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><VirtualizationIcon /></n-icon>
        <span>{{ t("nav.virtualization") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
    </n-space>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="clusters" :tab="`${t('virt.clusters')} (${clusters.length})`">
        <n-data-table :columns="clusterCols" :data="clusters" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane name="vms" :tab="`${t('virt.vms')} (${vms.length})`">
        <n-data-table :columns="vmCols" :data="vms" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane name="proxmox" :tab="`${t('virt.proxmox')} (${proxmox.length})`">
        <n-data-table :columns="proxmoxCols" :data="proxmox" :loading="loading" :bordered="false" />
      </n-tab-pane>
    </n-tabs>
  </n-card>
</template>
