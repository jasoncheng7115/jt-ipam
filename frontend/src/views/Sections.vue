<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { listSections } from "@/api/sections";
import type { Section } from "@/types";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Section[]>([]);
const loading = ref(false);

const columns: DataTableColumns<Section> = [
  { title: () => t("sections.name"), key: "name" },
  { title: () => t("sections.description"), key: "description", render: (r) => r.description ?? "" },
  { title: () => t("sections.strict_mode"), key: "strict_mode", render: (r) => (r.strict_mode ? "✓" : "—") },
];

async function refresh() {
  loading.value = true;
  try {
    const res = await listSections(1, 50);
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
  <n-card :title="t('sections.title')">
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
