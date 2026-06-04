<script setup lang="ts">
import { h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NDescriptions, NDescriptionsItem,
  NProgress, NDataTable, NSpin,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { SectionsIcon, SubnetsIcon, RefreshIcon } from "@/icons";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { apiClient } from "@/api/client";
import { listSubnets, getSubnetUsage } from "@/api/subnets";
import type { Section, Subnet, SubnetUsage } from "@/types";
import { autoSort } from "@/composables/useTableSort";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { computed } from "vue";
const { t } = useI18n();

const { visibleKeys: snVis, setVisible: snSet, reset: snReset } = useColumnPrefs(
  "section_detail_subnets",
  ["cidr", "description", "usage"],
  ["cidr", "description", "usage"],
);
const snPicker = [
  { key: "cidr", label: "CIDR" },
  { key: "description", label: t("cols.description") },
  { key: "usage", label: t("cols.usage") },
];

const route = useRoute();
const router = useRouter();
const links = useEntityLinks(router);
const msg = useMessage();

const section = ref<Section | null>(null);
const subnets = ref<Subnet[]>([]);
const usageMap = ref<Record<string, SubnetUsage>>({});
const loading = ref(false);

async function load(id: string) {
  loading.value = true;
  try {
    const [sec, subs] = await Promise.all([
      apiClient.get<Section>(`/api/v1/sections/${id}`).then((r) => r.data),
      listSubnets({ sectionId: id, page: 1, pageSize: 500 }),
    ]);
    section.value = sec;
    subnets.value = subs.items;
    const usages = await Promise.all(
      subs.items.map(async (s) => {
        try { return await getSubnetUsage(s.id); } catch { return null; }
      }),
    );
    const map: Record<string, SubnetUsage> = {};
    usages.forEach((u) => { if (u) map[u.subnet_id] = u; });
    usageMap.value = map;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

const allColumns: DataTableColumns<Subnet> = autoSort([
  { title: () => t("subnets.cidr"), key: "cidr", render: (r) => links.subnet(r.id, r.cidr) },
  {
    title: () => t("common.description"),
    key: "description",
    render: (r) => r.description ?? "",
  },
  {
    title: () => t("subnets.usage"),
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
]);

const columns = computed<DataTableColumns<Subnet>>(() =>
  allColumns.filter((c: any) => snVis.value.includes(c.key)),
);

watch(() => route.params.id, (id) => {
  if (typeof id === "string") void load(id);
});

onMounted(() => {
  const id = route.params.id;
  if (typeof id === "string") void load(id);
});
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="section">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><SectionsIcon /></n-icon>
            <span>{{ section.name }}</span>
          </n-space>
        </template>
        <template #header-extra>
          <n-button @click="router.push({ name: 'sections' })" size="small">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
            {{ t("common.back") }}
          </n-button>
        </template>
        <n-descriptions bordered :column="2" size="small">
          <n-descriptions-item :label="t('common.name')">{{ section.name }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.subnet_count')">{{ section.subnet_count ?? 0 }}</n-descriptions-item>
          <n-descriptions-item :label="t('sections.strict_mode')">{{ section.strict_mode ? "✓" : "—" }}</n-descriptions-item>
          <n-descriptions-item label="display_order">{{ section.display_order }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.description')" :span="2">
            {{ section.description ?? "—" }}
          </n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card>
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="20"><SubnetsIcon /></n-icon>
            <span>{{ t("nav.subnets") }} ({{ subnets.length }})</span>
          </n-space>
        </template>
        <template #header-extra>
          <n-space>
            <ColumnPicker :all="snPicker" :visible="snVis"
                          @update:visible="snSet" @reset="snReset" />
            <n-button
              v-if="section"
              @click="load(section.id)"
              :loading="loading"
              size="small"
            >
              <template #icon><n-icon><RefreshIcon /></n-icon></template>
              {{ t("common.refresh") }}
            </n-button>
          </n-space>
        </template>
        <n-data-table
          :columns="columns"
          :data="subnets"
          :loading="loading"
          :pagination="{ pageSize: 50 }"
          :bordered="false"
          :row-props="(row: Subnet) => ({
            style: 'cursor: pointer',
            onClick: () => router.push({ name: 'subnet-detail', params: { id: row.id } }),
          })"
        >
          <template #empty>
            <n-space justify="center">{{ t("common.no_data") }}</n-space>
          </template>
        </n-data-table>
      </n-card>
    </n-space>
  </n-spin>
</template>
