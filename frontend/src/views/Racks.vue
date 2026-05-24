<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NSelect,
  NSpin,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { RacksIcon } from "@/icons";
import { apiClient } from "@/api/client";
import RackDiagram from "@/components/RackDiagram.vue";
import { getRackDiagram, type RackDiagram as RD } from "@/api/racks";

interface Rack {
  id: string;
  name: string;
  u_height: number;
  location_id: string | null;
  description: string | null;
}

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Rack[]>([]);
const loading = ref(false);
const selected = ref<string | null>(null);
const diagram = ref<RD | null>(null);
const diagramLoading = ref(false);

const columns: DataTableColumns<Rack> = [
  { title: "Name", key: "name" },
  { title: "U", key: "u_height" },
  { title: "Description", key: "description", render: (r) => r.description ?? "" },
];

async function refresh() {
  loading.value = true;
  try {
    const { data } = await apiClient.get<{ items: Rack[] }>("/api/v1/racks", {
      params: { page: 1, page_size: 200 },
    });
    rows.value = data.items;
    if (!selected.value && rows.value.length) {
      selected.value = rows.value[0].id;
    }
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function loadDiagram(id: string) {
  diagramLoading.value = true;
  try {
    diagram.value = await getRackDiagram(id);
  } catch {
    msg.error(t("errors.network"));
    diagram.value = null;
  } finally {
    diagramLoading.value = false;
  }
}

watch(selected, (v) => {
  if (v) void loadDiagram(v);
  else diagram.value = null;
});

onMounted(refresh);
</script>

<template>
  <n-space vertical :size="16">
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="22"><RacksIcon /></n-icon>
          <span>Racks</span>
        </n-space>
      </template>
      <n-space>
        <n-select
          v-model:value="selected"
          :options="rows.map((r) => ({ label: `${r.name} (${r.u_height}U)`, value: r.id }))"
          placeholder="選擇機櫃"
          style="width: 280px"
          clearable
        />
      </n-space>
    </n-card>

    <n-spin :show="diagramLoading">
      <rack-diagram v-if="diagram" :diagram="diagram" />
      <n-card v-else-if="!selected" title="Rack diagram">
        <p style="opacity: 0.7">請選擇機櫃以顯示 U 位視覺化。</p>
      </n-card>
    </n-spin>

    <n-card title="All racks">
      <n-data-table
        :columns="columns"
        :data="rows"
        :loading="loading"
        :pagination="{ pageSize: 50 }"
        :bordered="false"
        :row-props="(row: Rack) => ({
          style: 'cursor: pointer',
          onClick: () => { selected = row.id; },
        })"
      />
    </n-card>
  </n-space>
</template>
