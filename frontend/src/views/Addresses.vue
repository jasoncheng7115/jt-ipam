<script setup lang="ts">
import { h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NTag,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { listAddresses } from "@/api/addresses";
import type { IPAddress } from "@/types";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<IPAddress[]>([]);
const loading = ref(false);

function statusTag(state: string) {
  const map: Record<string, "success" | "warning" | "error" | "default" | "info"> = {
    active: "success",
    reserved: "info",
    offline: "error",
    dhcp: "warning",
    used: "default",
  };
  return h(NTag, { type: map[state] ?? "default", size: "small" }, () => state);
}

const columns: DataTableColumns<IPAddress> = [
  { title: "IP", key: "ip" },
  { title: "Hostname", key: "hostname", render: (r) => r.hostname ?? "" },
  { title: "MAC", key: "mac", render: (r) => r.mac ?? "" },
  { title: "State", key: "state", render: (r) => statusTag(r.state) },
  { title: "Source", key: "discovery_source" },
];

async function refresh() {
  loading.value = true;
  try {
    const res = await listAddresses({ page: 1, pageSize: 100 });
    rows.value = res.items;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void refresh();
});
</script>

<template>
  <n-card :title="t('nav.addresses')">
    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      :pagination="{ pageSize: 100 }"
      :bordered="false"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>
  </n-card>
</template>
