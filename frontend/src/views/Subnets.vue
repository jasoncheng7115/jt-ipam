<script setup lang="ts">
import { h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NProgress,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { listSubnets, getSubnetUsage } from "@/api/subnets";
import type { Subnet, SubnetUsage } from "@/types";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Subnet[]>([]);
const usageMap = ref<Record<string, SubnetUsage>>({});
const loading = ref(false);

const columns: DataTableColumns<Subnet> = [
  { title: "CIDR", key: "cidr", render: (r) => r.cidr },
  {
    title: () => t("sections.description"),
    key: "description",
    render: (r) => r.description ?? "",
  },
  {
    title: "Used",
    key: "usage",
    render: (r) => {
      const u = usageMap.value[r.id];
      if (!u) return "—";
      const status = u.used_pct >= 90 ? "error" : u.used_pct >= 75 ? "warning" : "success";
      return h(NProgress, {
        type: "line",
        percentage: u.used_pct,
        status,
        showIndicator: true,
      });
    },
  },
];

async function refresh() {
  loading.value = true;
  try {
    const res = await listSubnets({ page: 1, pageSize: 50 });
    rows.value = res.items;
    const usages = await Promise.all(
      res.items.map(async (s) => {
        try {
          return await getSubnetUsage(s.id);
        } catch {
          return null;
        }
      }),
    );
    const map: Record<string, SubnetUsage> = {};
    usages.forEach((u) => {
      if (u) map[u.subnet_id] = u;
    });
    usageMap.value = map;
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
  <n-card :title="t('nav.subnets')">
    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      :pagination="{ pageSize: 50 }"
      :bordered="false"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>
  </n-card>
</template>
