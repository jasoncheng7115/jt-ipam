<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NCard, NDataTable, NButton, NSpace, useMessage, type DataTableColumns } from "naive-ui";
import { listSections, type Section } from "@/api/sections";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Section[]>([]);
const loading = ref(false);

const columns: DataTableColumns<Section> = [
  { title: () => t("sections.name"), key: "name" },
  { title: () => t("sections.description"), key: "description" },
  { title: () => t("sections.strict_mode"), key: "strict_mode" },
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
    <template #header-extra>
      <n-space>
        <n-button type="primary">{{ t("common.create") }}</n-button>
      </n-space>
    </template>
    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      :pagination="{ pageSize: 50 }"
      :bordered="false"
    />
  </n-card>
</template>
